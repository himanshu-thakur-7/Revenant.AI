"""Prototype planner — a strong-model spec writer that runs BEFORE the Engineer.

Insight from profiling: the Engineer's authoring call dominates a build
(gpt-5-mini spends ~60s reasoning about structure; gpt-4o skips reasoning and
returns a thin 4kB skeleton). Give a fast author (gpt-4.1) a *very complete
spec* built by a smart planner (gpt-5.6-luna / gpt-5.6-sol / gpt-5-mini) and it
can write a proper 15k+ page in half the time WITHOUT compromising quality.

This module does that spec generation as ONE cheap LLM call. It returns a
Markdown spec the Engineer's opening prompt then injects verbatim.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from ghost.config import settings


_DEFAULT_PLANNER_MODEL = os.getenv("REVENANT_PLANNER_MODEL", "gpt-5.6-luna")


_PLANNER_SYSTEM = (
    "You are a senior product designer + copywriter briefing a front-end engineer. "
    "Given a startup and a target merchant, you produce a COMPLETE, opinionated "
    "spec for a one-page tailored prototype the merchant would fall in love with. "
    "You know the merchant's category, brand, and buyer. You know the startup's "
    "value prop. You output a spec so complete that any competent engineer can "
    "write the whole page in one pass without asking questions.\n\n"
    "The spec MUST include:\n"
    "  1. The narrative hook (one sentence that captures why this fits THIS merchant).\n"
    "  2. Hero: headline + subhead + wordmark treatment + primary CTA.\n"
    "  3. 4-6 named sections in reading order, each with a short brief on what it "
    "     shows and the exact copy angle (NOT lorem ipsum — real, merchant-specific "
    "     copy that references their business).\n"
    "  4. A LIVE interactive demo section: what fields, what button, what output, "
    "     what real numbers the buyer would recognise. Ids MUST be #demo #demoInput "
    "     #demoRun #demoOutput. This is the centrepiece.\n"
    "  5. A social-proof section with 3-5 named brands from the merchant's category.\n"
    "  6. A code/integration snippet the buyer's engineers would recognise.\n"
    "  7. A closing CTA section (#cta).\n"
    "  8. Brand: palette (2 accent colours in hex tied to the merchant's identity), "
    "     font family choice, spacing tone (spacious/dense), any texture/motion cues.\n"
    "  9. VISUALS: NO external <img> tags — the engineer must not link to "
    "     images hosted on the merchant's CDN (they 404 half the time and "
    "     look broken). Direct them to use CSS gradients, inline SVG, or "
    "     emoji instead. Google Fonts is the ONLY external asset allowed.\n\n"
    "Output PLAIN MARKDOWN with clear section headers. No preamble, no closing "
    "chatter. 500-900 words. Copy that a real designer would ship — specific, "
    "confident, no hedging."
)


def _build_prompt(startup: str, startup_summary: str, merchant: str,
                  merchant_domain: str, pain: str, brand_brief: str) -> str:
    parts = [
        f"Founder's startup: **{startup}**",
        f"What {startup} sells: {startup_summary or '(a startup selling to businesses)'}",
        f"Target merchant: **{merchant}** ({merchant_domain or 'unknown domain'})",
        f"Why the fit / pain: {pain or 'unstated'}",
    ]
    if brand_brief:
        parts.append("\nMerchant's live brand signals (pulled from their homepage):\n"
                     + brand_brief.strip())
    parts.append(
        "\nProduce the full spec now. Every section is copy-ready; every colour "
        "and font is chosen; the interactive demo has real inputs/outputs; the "
        "engineer should be able to write the entire page from this without any "
        "guesswork."
    )
    return "\n\n".join(parts)


def _openai_key_and_base() -> tuple[str, str]:
    key = (settings.strong_api_key or settings.openai_api_key
           or os.getenv("OPENAI_API_KEY") or "")
    base = (settings.strong_base_url or settings.llm_base_url
            or "https://api.openai.com/v1").rstrip("/")
    return key, base


def build_prototype_spec(*, startup: str, startup_summary: str, merchant: str,
                         merchant_domain: str = "", pain: str = "",
                         brand_brief: str = "",
                         model: str | None = None,
                         timeout: float = 60.0) -> str:
    """Return a rich Markdown spec for the prototype. Empty string on failure.

    Cheap: one LLM call, no tools. On failure returns "" so the Engineer just
    proceeds with its original opening — the planner is best-effort acceleration.
    """
    key, base = _openai_key_and_base()
    if not key:
        return ""
    model = model or _DEFAULT_PLANNER_MODEL
    prompt = _build_prompt(startup, startup_summary, merchant,
                           merchant_domain, pain, brand_brief)
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    # gpt-5 / o-series don't accept a custom temperature; other models get 0.4
    # for a bit of copywriting warmth without hallucination.
    is_reasoning = bool(re.match(r"^(gpt-5|o[134])", model))
    if is_reasoning:
        body["max_completion_tokens"] = 4000
    else:
        body["temperature"] = 0.4
        body["max_tokens"] = 2500
    try:
        r = httpx.post(base + "/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json=body, timeout=timeout)
        if r.status_code != 200:
            return ""
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""
