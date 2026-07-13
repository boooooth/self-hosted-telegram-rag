# Telegram RAG Bot

A Telegram bot backed by a shared, open-read knowledge base. Anyone can ask
questions; only admins (configured via `ADMIN_USER_IDS`) can upload documents.

## Architecture

- **bot** (aiogram, webhook mode only) — accepts uploads/queries, runs hybrid
  retrieval + reranking + LLM generation synchronously per query.
- **worker** (RQ) — long-running process that extracts text, chunks, embeds,
  and indexes documents in the background after an admin upload.
- **postgres** — `users`, `documents`, `chunks` (with full-text search),
  `queries` (every question/answer logged).
- **qdrant** — one vector point per chunk.
- **redis** — RQ job queue + per-user query cooldown.
- **ollama** — local LLM runtime, used only when `LLM_MODEL` points at it (the
  default — see below).
- **cloudflared** — always-on local tunnel giving the bot a public HTTPS URL
  for Telegram's webhook, no Cloudflare account needed (see below).

The answer-generation step is the only generative LLM call in the system
(embeddings and reranking are always local, non-generative models), and it
goes through [litellm](https://github.com/BerriAI/litellm) so the model is
just a string in `.env` (`LLM_MODEL`) — no per-provider code. Defaults to
`ollama/smollm2:135m` — the smallest realistic model in Ollama's library
(~271MB), chosen so the bot runs with zero API keys and a light download out
of the box; after first start, pull it once:
```
docker compose exec ollama ollama pull smollm2:135m
```
Answer quality at 135M params is noticeably weaker than a hosted model (it
can drift off the retrieved context or ignore the citation instruction) —
this is meant as a "get it running immediately" default, not a quality
target. For meaningfully better local answers with still no API key, swap to
`ollama/llama3.2` (3B, ~2GB) or `ollama/llama3.1` (8B, ~4.7GB, slower on
CPU-only hardware) — just change `LLM_MODEL` and pull that model instead.

To use Claude or GPT instead, set `LLM_MODEL=claude-sonnet-5` (or
`gpt-4o-mini`) in `.env` and fill in the matching API key
(`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) — the `ollama` service becomes
unused and can be left idle or removed. litellm supports most other
providers too (Gemini, Groq, Bedrock, ...) — same pattern, just a different
model string and API key.

`bot` and `worker` are built from the same Docker image (see `Dockerfile`);
only the `command:` differs per service in `docker-compose.yml`.

## Quick start

1. Copy the env file and fill in secrets:
   ```
   cp .env.example .env
   ```
   You'll need: a Telegram bot token from [@BotFather](https://t.me/BotFather),
   your own Telegram numeric user ID (message [@userinfobot](https://t.me/userinfobot))
   for `ADMIN_USER_IDS`. No LLM API key is required by default (`LLM_MODEL=ollama/smollm2:135m`) —
   see the LLM note above if you'd rather use Claude or GPT, or a larger local model.

   Also replace the placeholder `WEBHOOK_SECRET_TOKEN` and `POSTGRES_PASSWORD`
   values in `.env` (both ship as literal `change-me...` strings) — harmless
   for local dev, but worth generating real values before deploying anywhere
   reachable from the internet.

2. Leave `WEBHOOK_URL` empty in `.env` — see the tunnel note below.

3. Bring everything up:
   ```
   docker compose up --build
   ```

4. Message your bot on Telegram. Upload a text-based PDF as an admin to index
   it; ask questions as anyone.

## Commands

Every reply — including this list — renders as a single monospace code
block in Telegram (no selective markdown parsing, so nothing in an answer
can break formatting). Commands also appear in Telegram's native "/" menu:
everyone sees `/start`; admins additionally see `/documents` and `/delete`
there, scoped so non-admins never see commands they can't use.

- `/start` — onboarding message; shows admin commands too if you're one.
- `/documents [page]` — admin only. Lists uploaded documents (newest first,
  20 per page) with id, filename, status, and chunk count.
- `/delete <id>` — admin only. Removes a document (Qdrant vectors, Postgres
  row + its chunks, and the stored file) by the id shown in `/documents`.

Anyone can also just send a plain-text question, or (admins only) upload a
PDF/DOCX/TXT/MD file to index it.

## Tunnel: quick tunnel by default, no Cloudflare account needed

With `WEBHOOK_URL` left empty, `cloudflared` runs an ephemeral **quick
tunnel** — no login, no domain, no Cloudflare account required. It gets a
random `https://<random-words>.trycloudflare.com` hostname each time the
container starts. Since that hostname changes on every restart, the bot
doesn't rely on a fixed `WEBHOOK_URL`: on startup it polls cloudflared's local
metrics endpoint (`CLOUDFLARED_METRICS_URL`, internal to the Docker network)
until it reports the current hostname, then registers that as its Telegram
webhook. This is all automatic — `docker compose up --build` is genuinely all
you need to do for the tunnel.

**Tradeoff:** each restart gets a new public hostname (harmless — the bot
re-registers automatically), and there's a several-second window on startup
while cloudflared connects to Cloudflare's edge before the bot can register
its webhook (the bot retries for up to 60s, so this resolves itself).

### Upgrading to a stable named tunnel later

If you get a domain added to Cloudflare later and want a fixed hostname
instead (no rotation, no startup race):

```
# 1. Log in (opens a browser; needs the domain added to your Cloudflare account)
cloudflared tunnel login

# 2. Create a named tunnel — writes credentials to ~/.cloudflared/<TUNNEL_ID>.json
cloudflared tunnel create telegram-rag-bot

# 3. Point a hostname at it (replace with your own domain/subdomain)
cloudflared tunnel route dns telegram-rag-bot bot.yourdomain.com

# 4. Copy the credentials file into this repo's cloudflared/ dir so the
#    container can read it (it's gitignored, safe to keep here locally)
cp ~/.cloudflared/<TUNNEL_ID>.json ./cloudflared/

# 5. Copy and fill in the config template
cp cloudflared/config.yml.example cloudflared/config.yml
# edit cloudflared/config.yml: set `tunnel:`, `credentials-file:`, and `hostname:`
```

Then in `docker-compose.yml`, change the `cloudflared` service's `command` from
`tunnel --url http://bot:${PORT} --metrics 0.0.0.0:20241 --no-autoupdate` to
`tunnel run`, and add back a volume mount `./cloudflared:/etc/cloudflared:ro`.
Finally set `WEBHOOK_URL=https://bot.yourdomain.com/webhook` in `.env` — with
that set, the bot uses it directly and skips the quick-tunnel discovery step
entirely.

## Deploying beyond local dev

Once you deploy to a host that provides its own public URL (Railway, Render,
a VPS with its own domain/reverse proxy):

- **Remove the `cloudflared` service** from `docker-compose.yml` (or just
  don't run it) — it exists purely to solve the "no public URL on localhost"
  problem for local development.
- **Point `WEBHOOK_URL`** at the URL your host assigns/you configure instead.
- **Uploaded files volume**: the `uploads` named volume is a real persistent
  Docker volume, which both Railway and Render support natively today, so no
  changes should be needed. If your target host doesn't provide persistent
  volumes, that's the one spot to swap for S3/MinIO-backed storage instead of
  local disk (`app/db.py`'s `storage_path` and the upload/download code in
  `app/bot.py` and `app/ingestion.py` would need to read/write from the
  object store instead of the filesystem).

## Development

Install dev dependencies (adds `pytest` on top of the runtime requirements
in `requirements.txt` -- the production image only ever installs the latter,
see `Dockerfile`):
```
pip install -r requirements-dev.txt
```

Run the test suite:
```
python -m pytest tests/ -v
```
(`python -m pytest`, not bare `pytest` -- the latter doesn't put the repo
root on `sys.path`, so `import app.x` fails in every test file.)

Every PR to `main` runs this same test suite plus a syntax/import check in
CI (`.github/workflows/ci.yml`). `main` is branch-protected: changes go
through a PR, and CI must pass before it can merge.

## Limitations

- Supported upload formats: PDF, DOCX, TXT, MD. PDF ingestion is text-based
  only (`pypdf`) — scanned/image PDFs with no extractable text, or any other
  unsupported file type, will be marked `status='failed'` and the uploading
  admin is notified, rather than being silently indexed as empty.
- No conversation memory — every query is answered independently from the
  knowledge base, with no history from prior messages.
- A per-user cooldown (`COOLDOWN_SECONDS` in `.env`) throttles query
  frequency — cheap insurance against abuse, and directly protects against API
  cost if you switch `LLM_MODEL` to a hosted provider, since reads are open
  to anyone.
