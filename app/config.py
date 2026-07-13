"""Central place all env vars are read from. Import `settings` everywhere else."""
import os
from dataclasses import dataclass, field


def _parse_admin_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = field(default_factory=lambda: os.environ["TELEGRAM_BOT_TOKEN"])
    admin_user_ids: frozenset[int] = field(
        default_factory=lambda: _parse_admin_ids(os.environ["ADMIN_USER_IDS"])
    )

    # Leave WEBHOOK_URL unset for local dev — the bot discovers the current
    # cloudflared quick-tunnel hostname at startup instead (see bot.py
    # resolve_webhook_url()). Set it explicitly once deployed somewhere with
    # its own stable public URL (Railway/Render/a VPS, or a named tunnel).
    webhook_url: str = field(default_factory=lambda: os.environ.get("WEBHOOK_URL", ""))
    webhook_path: str = field(default_factory=lambda: os.environ.get("WEBHOOK_PATH", "/webhook"))
    webhook_secret_token: str = field(default_factory=lambda: os.environ["WEBHOOK_SECRET_TOKEN"])
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8080")))

    # Local cloudflared container's metrics endpoint, used only when WEBHOOK_URL
    # is unset, to read the current quick-tunnel hostname (it rotates every
    # restart since there's no named tunnel/fixed domain configured).
    cloudflared_metrics_url: str = field(
        default_factory=lambda: os.environ.get(
            "CLOUDFLARED_METRICS_URL", "http://cloudflared:20241/quicktunnel"
        )
    )

    # litellm model string — e.g. "ollama/llama3.1", "claude-sonnet-5", "gpt-4o-mini".
    # litellm infers the provider from this string and reads the matching API key
    # (ANTHROPIC_API_KEY / OPENAI_API_KEY) straight from the environment — no
    # separate per-provider settings needed here.
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "ollama/smollm2:135m"))
    llm_api_base: str = field(default_factory=lambda: os.environ.get("LLM_API_BASE", "http://ollama:11434"))

    database_url: str = field(default_factory=lambda: os.environ["DATABASE_URL"])
    redis_url: str = field(default_factory=lambda: os.environ["REDIS_URL"])

    qdrant_url: str = field(default_factory=lambda: os.environ["QDRANT_URL"])
    qdrant_collection: str = field(default_factory=lambda: os.environ.get("QDRANT_COLLECTION", "chunks"))

    upload_dir: str = field(default_factory=lambda: os.environ.get("UPLOAD_DIR", "/data/uploads"))

    embedding_model_name: str = field(
        default_factory=lambda: os.environ.get(
            "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    cross_encoder_model_name: str = field(
        default_factory=lambda: os.environ.get(
            "CROSS_ENCODER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
    )

    chunk_size: int = field(default_factory=lambda: int(os.environ.get("CHUNK_SIZE", "800")))
    chunk_overlap: int = field(default_factory=lambda: int(os.environ.get("CHUNK_OVERLAP", "150")))

    candidate_pool_size: int = field(
        default_factory=lambda: int(os.environ.get("CANDIDATE_POOL_SIZE", "25"))
    )
    rerank_top_k: int = field(default_factory=lambda: int(os.environ.get("RERANK_TOP_K", "5")))

    cooldown_seconds: int = field(default_factory=lambda: int(os.environ.get("COOLDOWN_SECONDS", "10")))

    def __post_init__(self) -> None:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be less than CHUNK_SIZE "
                f"({self.chunk_size}) — otherwise chunk_text()'s sliding window never "
                "advances and hangs forever."
            )


settings = Settings()
