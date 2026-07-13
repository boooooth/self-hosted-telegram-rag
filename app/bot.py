"""aiogram bot, webhook mode only. Runs as its own container; the worker
handles actual ingestion, this process just accepts uploads/queries and
answers text queries synchronously via hybrid retrieval + Claude."""
import asyncio
import logging
import os
import time

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, ErrorEvent, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from rq import Callback, Retry

from app import cooldown, db, qdrant_store
from app.config import settings
from app.llm import generate_answer
from app.queue import get_queue
from app.retrieval import hybrid_search, rerank

DOCUMENTS_PAGE_SIZE = 20

# Shown in Telegram's native "/" command popup. PUBLIC_COMMANDS is set as
# the bot-wide default (every chat); ADMIN_COMMANDS is set per-admin-chat on
# top of that (see set_admin_command_menu) so non-admins never see commands
# they'd just get rejected from using.
PUBLIC_COMMANDS = [BotCommand(command="start", description="What this bot does")]
ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand(command="documents", description="List indexed documents"),
    BotCommand(command="delete", description="Remove a document by id"),
]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


async def set_admin_command_menu(bot: Bot, chat_id: int) -> None:
    """Telegram only accepts a per-chat command scope once that chat exists
    from its side ("chat not found" otherwise) -- true once a user has sent
    the bot at least one message. Called both at startup (best-effort, for
    admins who've already messaged the bot before) and from handle_start
    (guaranteed to work, since the chat obviously exists by then)."""
    try:
        await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=chat_id))
    except Exception:
        logger.warning("Could not set admin command menu for chat %s yet", chat_id)


@router.message(Command("start"))
async def handle_start(message: Message, bot: Bot) -> None:
    user = message.from_user
    is_admin = user.id in settings.admin_user_ids
    db.upsert_user(user.id, user.username, is_admin)

    if is_admin:
        await set_admin_command_menu(bot, message.chat.id)

    lines = [
        "👋 I'm a question-answering bot over a shared document knowledge base.",
        "Just send me a question and I'll search the indexed documents and answer with citations.",
    ]
    if is_admin:
        lines += [
            "",
            "Admin commands:",
            "• Send a PDF/DOCX/TXT/MD file to add it to the knowledge base.",
            "/documents — list indexed documents and their status",
            "/delete <id> — remove a document from the knowledge base",
        ]
    await message.reply("\n".join(lines))


@router.message(Command("documents"))
async def handle_documents(message: Message, command: CommandObject) -> None:
    user = message.from_user
    is_admin = user.id in settings.admin_user_ids
    db.upsert_user(user.id, user.username, is_admin)

    if not is_admin:
        await message.reply("Sorry, only admins can view the document list.")
        return

    page_arg = (command.args or "1").strip()
    if not page_arg.isdigit() or int(page_arg) < 1:
        await message.reply("Usage: /documents [page number]")
        return
    page = int(page_arg)

    total = db.count_documents()
    if total == 0:
        await message.reply("No documents have been uploaded yet.")
        return

    total_pages = -(-total // DOCUMENTS_PAGE_SIZE)  # ceiling division
    if page > total_pages:
        await message.reply(
            f"Page {page} doesn't exist — there are only {total_pages} "
            f"page{'s' if total_pages != 1 else ''} ({total} documents)."
        )
        return

    offset = (page - 1) * DOCUMENTS_PAGE_SIZE
    documents = db.list_documents(limit=DOCUMENTS_PAGE_SIZE, offset=offset)

    status_emoji = {"ready": "✅", "pending": "⏳", "failed": "❌"}
    lines = [f"📄 Page {page} of {total_pages} ({total} documents):"]
    for doc in documents:
        emoji = status_emoji.get(doc["status"], "")
        lines.append(
            f"#{doc['id']} {doc['filename']} — {emoji} {doc['status']} ({doc['chunk_count']} chunks)"
        )
    lines.append("")
    hints = []
    if page < total_pages:
        hints.append(f"/documents {page + 1} for more")
    hints.append("/delete <id> to remove one.")
    lines.append(" · ".join(hints))

    await message.reply("\n".join(lines))


@router.message(Command("delete"))
async def handle_delete(message: Message, command: CommandObject) -> None:
    user = message.from_user
    is_admin = user.id in settings.admin_user_ids
    db.upsert_user(user.id, user.username, is_admin)

    if not is_admin:
        await message.reply("Sorry, only admins can delete documents.")
        return

    args = (command.args or "").strip()
    if not args.isdigit():
        await message.reply("Usage: /delete <document id> — see /documents for ids.")
        return
    document_id = int(args)

    doc = db.get_document(document_id)
    if doc is None:
        await message.reply(f"No document with id {document_id}.")
        return

    try:
        qdrant_store.delete_document(document_id)
        db.delete_document(document_id)
    except Exception:
        logger.exception("Failed to delete document_id=%s", document_id)
        await message.reply("Sorry, something went wrong deleting that document — please try again.")
        return

    if os.path.exists(doc["storage_path"]):
        try:
            os.remove(doc["storage_path"])
        except OSError:
            logger.exception("Failed to remove stored file for document_id=%s", document_id)

    await message.reply(f'Deleted "{doc["filename"]}" (#{document_id}).')


@router.message(F.document)
async def handle_document(message: Message, bot: Bot) -> None:
    user = message.from_user
    is_admin = user.id in settings.admin_user_ids
    db.upsert_user(user.id, user.username, is_admin)

    if not is_admin:
        await message.reply("Sorry, only admins can upload documents to this knowledge base.")
        return

    document = message.document
    filename = os.path.basename(document.file_name or f"document_{document.file_unique_id}")
    storage_path = os.path.join(settings.upload_dir, f"{document.file_unique_id}_{filename}")

    try:
        file = await bot.get_file(document.file_id)
        await bot.download_file(file.file_path, destination=storage_path)
    except Exception:
        logger.exception("Failed to download document %s from Telegram", document.file_id)
        await message.reply(
            "Sorry, I couldn't download that file (it may be too large or Telegram had a "
            "hiccup) — please try again."
        )
        return

    document_id = db.insert_document(filename=filename, storage_path=storage_path, uploaded_by=user.id)
    get_queue().enqueue(
        "app.ingestion.process_document",
        document_id,
        retry=Retry(max=3, interval=[10, 30, 90]),
        on_failure=Callback("app.ingestion.notify_final_failure"),
    )

    await message.reply(f'"{filename}" received — indexing now...')


@router.message(F.text)
async def handle_text(message: Message) -> None:
    user = message.from_user
    is_admin = user.id in settings.admin_user_ids
    db.upsert_user(user.id, user.username, is_admin)

    if not cooldown.allow(user.id):
        await message.reply("You're sending questions too quickly — please wait a few seconds and try again.")
        return

    question = message.text
    loop = asyncio.get_running_loop()

    try:
        candidates = await loop.run_in_executor(None, hybrid_search, question)
        ranked = await loop.run_in_executor(None, rerank, question, candidates)

        if not ranked:
            answer = "I don't know — I couldn't find anything relevant in the knowledge base."
        else:
            answer = await loop.run_in_executor(None, generate_answer, question, ranked)
    except Exception:
        logger.exception("Failed to answer question from user %s", user.id)
        await message.reply(
            "Sorry, something went wrong while answering your question — please try again in a moment."
        )
        return

    await message.reply(answer)

    db.log_query(
        user_id=user.id,
        question=question,
        retrieved_chunk_ids=[c["chunk_id"] for c in ranked],
        answer=answer,
    )


@router.errors()
async def handle_errors(event: ErrorEvent, bot: Bot) -> None:
    """Last-resort safety net so no failure is ever fully silent to the user —
    handler-specific try/except blocks above give more precise messages, but
    anything they don't cover (or a bug in a future handler) still lands here."""
    logger.exception(
        "Unhandled exception while processing update %s",
        event.update.update_id,
        exc_info=event.exception,
    )
    chat = getattr(event.update.message, "chat", None)
    if chat is not None:
        try:
            await bot.send_message(chat.id, "Sorry, something went wrong on my end — please try again.")
        except Exception:
            logger.exception("Failed to notify user %s about an unhandled error", chat.id)


async def _wait_for_quick_tunnel_hostname(timeout_seconds: float = 60.0) -> str:
    """Poll cloudflared's local metrics endpoint for the quick-tunnel hostname.
    It rotates every restart since there's no named tunnel configured, and it
    can take a few seconds after container start for cloudflared to connect
    to Cloudflare's edge and get one assigned."""
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(settings.cloudflared_metrics_url, timeout=3)
                if response.status_code == 200:
                    hostname = response.json().get("hostname")
                    if hostname:
                        return hostname
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError(
        "Timed out waiting for cloudflared to report a quick-tunnel hostname"
    )


async def resolve_webhook_url() -> str:
    if settings.webhook_url:
        return settings.webhook_url
    hostname = await _wait_for_quick_tunnel_hostname()
    return f"https://{hostname}{settings.webhook_path}"


async def on_startup(bot: Bot) -> None:
    os.makedirs(settings.upload_dir, exist_ok=True)
    qdrant_store.ensure_collection()

    await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_user_ids:
        await set_admin_command_menu(bot, admin_id)

    webhook_url = await resolve_webhook_url()
    await bot.set_webhook(webhook_url, secret_token=settings.webhook_secret_token)
    logger.info("Webhook registered at %s", webhook_url)


def main() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=settings.webhook_secret_token
    ).register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
