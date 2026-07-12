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
import re
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


# Load Revenant's own .env first (higher priority — user's explicit overrides
# for Apollo/CF/ElevenLabs keys stay authoritative). Then fall back to the
# Hermes .env with `override=False` so anything Revenant hasn't set (LLM
# route, Telegram token) inherits Hermes's world.
load_dotenv()

_HERMES_HOME = Path.home() / ".hermes"
_HERMES_ENV = _HERMES_HOME / ".env"
_HERMES_CONFIG = _HERMES_HOME / "config.yaml"

if _HERMES_ENV.exists():
    load_dotenv(_HERMES_ENV, override=False)


def _hermes_default_model() -> str | None:
    """Read Hermes's default model from ``~/.hermes/config.yaml`` — the source
    of truth when the user hasn't set ``LLM_MODEL`` explicitly. Returns None
    if Hermes isn't installed or the config is unparseable (we don't want a
    tiny YAML quirk to crash Revenant boot)."""
    if not _HERMES_CONFIG.exists():
        return None
    try:
        raw = _HERMES_CONFIG.read_text()
    except OSError:
        return None
    # Look for `default: <name>` under a `model:` block. Kill inline comments
    # first so we don't grab something like `default: <name> # comment`.
    no_comments = re.sub(r"#.*$", "", raw, flags=re.M)
    m = re.search(r"^\s*default\s*:\s*['\"]?([^'\"\n]+?)['\"]?\s*$",
                  no_comments, flags=re.M)
    return m.group(1).strip() if m else None


def _hermes_default_base_url() -> str | None:
    """Read Hermes's model.base_url from config.yaml. Used when the user is
    routing Hermes through OpenRouter and hasn't set LLM_BASE_URL."""
    if not _HERMES_CONFIG.exists():
        return None
    try:
        raw = _HERMES_CONFIG.read_text()
    except OSError:
        return None
    no_comments = re.sub(r"#.*$", "", raw, flags=re.M)
    m = re.search(r"^\s*base_url\s*:\s*['\"]?([^'\"\n]+?)['\"]?\s*$",
                  no_comments, flags=re.M)
    return m.group(1).strip() if m else None


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

    # Stronger model used for quality-critical steps (research reasoning,
    # email drafting) — reads STRONG_MODEL / STRONG_MODEL_KEY / STRONG_MODEL_URL,
    # defaults to OpenAI gpt-4o via the OPENAI_API_KEY.
    strong_model: str = "gpt-4o"
    strong_base_url: str = "https://api.openai.com/v1"
    strong_api_key: str | None = None

    openai_api_key: str | None = None
    linkup_api_key: str | None = None

    elevenlabs_api_key: str | None = None
    # Bella — warm female voice. Matches the Fiona D-ID avatar.
    # Override with ELEVENLABS_VOICE_ID to swap presenters.
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
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

    # Founder identity — signs the outbound emails, never invented by the LLM
    founder_name: str = "the founder"
    founder_email: str | None = None
    founder_company: str | None = None

    # Apollo.io — contact discovery (people search + email reveal)
    apollo_api_key: str | None = None

    # D-ID (avatar lip-sync)
    did_api_key: str | None = None
    did_presenter_id: str = "amy-Aq6OmGZnMt"  # stock: warm business tone
    # Default TRUE so real prospect walkthroughs render in ~30s instead of
    # ~250s (D-ID trial queue can idle 3 min before rendering starts). The
    # Razorpay/boAt demo path uses a pre-built mp4 and never hits this. Set
    # DIRECTOR_SKIP_LIPSYNC=0 for premium demos when D-ID credits are healthy.
    skip_lipsync: bool = True

    # D-ID interactive agent (Sana) — embedded in prototypes to answer
    # prospect questions about the startup. Needs its embed domain
    # whitelisted in the D-ID Studio before it renders on external pages.
    did_agent_id: str | None = None
    did_agent_client_key: str | None = None
    did_knowledge_id: str | None = None

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
    # Hermes-as-fallback resolution. When the user just has Hermes running,
    # ~/.hermes/.env supplies OPENAI_API_KEY + OPENAI_BASE_URL (pointing at
    # OpenRouter or another OpenAI-compatible endpoint), and ~/.hermes/config.yaml
    # holds the default model. Revenant reuses that world so the founder
    # doesn't have to duplicate keys into ~/Revenant.AI/.env.
    _hermes_model = _hermes_default_model()
    _hermes_base = _hermes_default_base_url()
    _oai_key = os.getenv("OPENAI_API_KEY")
    _oai_base = os.getenv("OPENAI_BASE_URL")

    llm_api_key = (os.getenv("LLM_API_KEY") or _oai_key)
    llm_base_url = (os.getenv("LLM_BASE_URL")
                    or _oai_base
                    or _hermes_base
                    or "https://api.openai.com/v1")
    llm_model = (os.getenv("LLM_MODEL")
                 or _hermes_model
                 or "gpt-4o-mini")

    # Strong-model default: if the user hasn't set a distinct STRONG_MODEL_KEY,
    # route strong requests through the same OpenAI-compatible endpoint as the
    # LLM. OpenRouter routes model id `openai/gpt-4o` correctly; direct OpenAI
    # routes `gpt-4o`. Pick the id shape by base_url.
    _strong_key = os.getenv("STRONG_MODEL_KEY") or _oai_key
    _strong_base_default = (os.getenv("STRONG_MODEL_URL")
                            or _oai_base
                            or _hermes_base
                            or "https://api.openai.com/v1")
    # Pick a strong-model id that the resolved base_url can actually serve —
    # a (URL, model) mismatch (e.g. Nous + gpt-4o) 404s at runtime. When we
    # can't be sure OpenAI is reachable, degrade to the llm_model so Sales
    # falls back onto the shared Hermes route instead of a broken pair.
    _sbu = (_strong_base_default or "").lower()
    if os.getenv("STRONG_MODEL"):
        strong_model = os.getenv("STRONG_MODEL")
    elif "openrouter" in _sbu:
        strong_model = "openai/gpt-4o"
    elif "api.openai.com" in _sbu:
        strong_model = "gpt-4o"
    else:
        strong_model = llm_model  # e.g. Nous route → keep Hermes-4-405B

    return Settings(
        mode=os.getenv("REVENANT_MODE", "offline"),
        dry_run=_flag("DRY_RUN", True),
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        strong_model=strong_model,
        strong_base_url=_strong_base_default,
        strong_api_key=_strong_key,
        openai_api_key=_oai_key,
        linkup_api_key=os.getenv("LINKUP_API_KEY"),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL"),
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
        founder_name=os.getenv("FOUNDER_NAME", "the founder"),
        founder_email=os.getenv("FOUNDER_EMAIL"),
        founder_company=os.getenv("FOUNDER_COMPANY"),
        apollo_api_key=os.getenv("APOLLO_API_KEY"),
        did_api_key=os.getenv("DID_API_KEY"),
        did_presenter_id=os.getenv("DID_PRESENTER_ID", "amy-Aq6OmGZnMt"),
        skip_lipsync=_flag("DIRECTOR_SKIP_LIPSYNC", True),
        did_agent_id=os.getenv("DID_AGENT_ID"),
        did_agent_client_key=os.getenv("DID_AGENT_CLIENT_KEY"),
        did_knowledge_id=os.getenv("DID_KNOWLEDGE_ID"),
        cloudinary_cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        cloudinary_api_key=os.getenv("CLOUDINARY_API_KEY"),
    )


settings = get_settings()
