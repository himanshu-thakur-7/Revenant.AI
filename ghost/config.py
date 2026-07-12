"""Central configuration and mode handling.

Two global switches decide how the pipeline behaves:

* ``REVENANT_MODE`` — ``offline`` (default) replays canned fixtures and makes
  zero network calls, so the whole flow is demoable and testable on a plane.
  ``live`` hits the real APIs.
* ``DRY_RUN`` — when set, the delivery layer never actually sends an email.
  On by default; a non-negotiable per the master-plan handoff notes.

Nothing else in the codebase reads ``os.environ`` directly — everything goes
through :data:`settings` so the mode is enforced in one place.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    """Resolved runtime configuration. Immutable snapshot of the env."""

    mode: str = "offline"          # offline | live
    dry_run: bool = True

    # LLM (orchestration reasoning — Hermes/Nous Portal/OpenAI-compatible)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"

    openai_api_key: str | None = None
    linkup_api_key: str | None = None

    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_agent_id: str | None = None

    cloudflare_api_token: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_pages_project: str = "revenant-prototypes"

    convex_url: str | None = None
    convex_deploy_key: str | None = None

    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None

    resend_api_key: str | None = None
    from_email: str = "founder@revenant.ai"

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    stage1b_provider: str = "openai"

    # D-ID (avatar lip-sync)
    did_api_key: str | None = None
    did_presenter_id: str = "amy-Aq6OmGZnMt"  # stock: warm business tone
    skip_lipsync: bool = False  # DIRECTOR_SKIP_LIPSYNC=1 to save credits

    # Cloudinary (video host)
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None

    @property
    def offline(self) -> bool:
        return self.mode.lower() != "live"

    def require_live(self, *keys: str) -> bool:
        """True only in live mode with every named key present."""
        if self.offline:
            return False
        return all(getattr(self, k, None) for k in keys)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        mode=os.getenv("REVENANT_MODE", "offline"),
        dry_run=_flag("DRY_RUN", True),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        linkup_api_key=os.getenv("LINKUP_API_KEY"),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
        elevenlabs_agent_id=os.getenv("ELEVENLABS_AGENT_ID"),
        cloudflare_api_token=os.getenv("CLOUDFLARE_API_TOKEN"),
        cloudflare_account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID"),
        cloudflare_pages_project=os.getenv("CLOUDFLARE_PAGES_PROJECT", "revenant-prototypes"),
        convex_url=os.getenv("CONVEX_URL"),
        convex_deploy_key=os.getenv("CONVEX_DEPLOY_KEY"),
        razorpay_key_id=os.getenv("RAZORPAY_KEY_ID"),
        razorpay_key_secret=os.getenv("RAZORPAY_KEY_SECRET"),
        razorpay_webhook_secret=os.getenv("RAZORPAY_WEBHOOK_SECRET"),
        resend_api_key=os.getenv("RESEND_API_KEY"),
        from_email=os.getenv("FROM_EMAIL", "founder@revenant.ai"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        stage1b_provider=os.getenv("STAGE1B_PROVIDER", "openai"),
        did_api_key=os.getenv("DID_API_KEY"),
        did_presenter_id=os.getenv("DID_PRESENTER_ID", "amy-Aq6OmGZnMt"),
        skip_lipsync=_flag("DIRECTOR_SKIP_LIPSYNC", False),
        cloudinary_cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        cloudinary_api_key=os.getenv("CLOUDINARY_API_KEY"),
    )


settings = get_settings()
