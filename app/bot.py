"""aiogram bot, webhook mode only. Runs as its own container; the worker
handles actual ingestion, this process just accepts uploads/queries and
answers text queries synchronously via hybrid retrieval + Claude."""
import asyncio
import logging
import os
import time

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import ErrorEvent, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from rq import Callback, Retry

from app import cooldown, db
from app.config import settings
from app.llm import generate_answer
from app.qdrant_store import ensure_collection
from app.queue import get_queue
from app.retrieval import hybrid_search, rerank

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


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
    ensure_collection()
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
