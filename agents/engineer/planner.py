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
import json
import re
from typing import Any

import httpx

from ghost.config import settings


_DEFAULT_PLANNER_MODEL = os.getenv("REVENANT_PLANNER_MODEL", "gpt-5.6-luna")


_PLANNER_SYSTEM = (
    "You are a senior product designer + copywriter briefing a front-end engineer. "
    "Given a startup and a target merchant, you produce a COMPLETE, opinionated "
    "spec for a one-page tailored prototype the merchant would fall in love with. "
    "You know the merchant's category, brand, buyer, homepage language, product "
    "surface, and operational workflow. You know the startup's value prop. You "
    "output a spec so complete that any competent engineer can write the whole "
    "page in one pass without asking questions.\n\n"
    "SPECIFICITY BAR: if the resulting prototype could work for a competitor by "
    "swapping the logo/company name, your spec is a failure. The merchant must "
    "recognise THEIR homepage language, THEIR category, THEIR customer journey, "
    "THEIR operational data, and THEIR buyer's KPI in the page.\n\n"
    "The spec MUST include:\n"
    "  1. ACCOUNT FINGERPRINT: 8-12 concrete details to visibly reuse. Pull from "
    "     the merchant domain, homepage phrases/nav/CTA language, prospect brief, "
    "     category, buyer, and pain. Mark each as 'confirmed' or 'inferred'.\n"
    "  2. The narrative hook (one sentence that captures why this fits THIS merchant).\n"
    "  3. Hero: headline + subhead + wordmark treatment + primary CTA. It must "
    "     include at least two fingerprint details, not just the company name.\n"
    "  4. 5-7 named sections in reading order, each with exact copy blocks and "
    "     the fingerprint details it uses. Every section must name a merchant "
    "     workflow, product/category, channel, buyer KPI, or homepage phrase.\n"
    "  5. A LIVE interactive demo section: what fields, what button, what output, "
    "     what real numbers the buyer would recognise. Ids MUST be #demo #demoInput "
    "     #demoRun #demoOutput. This is the centrepiece.\n"
    "     - Prefill #demoInput with merchant-specific sample data: real-ish order "
    "       lines, SKUs/categories, locations, customer states, policy cases, "
    "       transcripts, claims, or workflow objects from THIS merchant's world.\n"
    "     - #demoOutput must show merchant-specific computed results, not generic "
    "       'processed successfully' text. Include 3-5 rows/cards with believable "
    "       metrics tied to the buyer's KPI.\n"
    "  6. A social-proof/comparison section with 3-5 named brands from the "
    "     merchant's exact category or adjacent category, and say why they matter.\n"
    "  7. A code/integration snippet the buyer's engineers would recognise. It "
    "     must include merchant-flavoured object names, webhook/event names, or "
    "     payload fields, while staying plausible.\n"
    "  8. A closing CTA section (#cta) with a small, specific pilot ask tied to "
    "     the merchant's workflow, not a generic 'book a demo'.\n"
    "  9. Brand: palette (2 accent colours in hex tied to the merchant's identity), "
    "     font family choice, spacing tone (spacious/dense), any texture/motion cues.\n"
    " 10. SPECIFICITY CHECK: list at least 10 exact phrases/data points the "
    "     engineer must include verbatim in the final page.\n"
    " 11. VISUALS: NO external <img> tags — the engineer must not link to "
    "     images hosted on the merchant's CDN (they 404 half the time and "
    "     look broken). Direct them to use CSS gradients, inline SVG, or "
    "     emoji instead. Google Fonts is the ONLY external asset allowed.\n\n"
    "Forbidden generic phrases unless immediately followed by merchant-specific "
    "details: 'streamline operations', 'boost conversion', 'seamless experience', "
    "'tailored solution', 'AI-powered platform', 'unlock growth', 'your business'.\n\n"
    "Output PLAIN MARKDOWN with clear section headers. No preamble, no closing "
    "chatter. 900-1400 words. Copy that a real designer would ship — specific, "
    "confident, no hedging."
)


def _build_prompt(startup: str, startup_summary: str, merchant: str,
                  merchant_domain: str, pain: str, brand_brief: str,
                  prospect_brief: dict[str, Any] | None = None) -> str:
    prospect_json = ""
    if prospect_brief:
        try:
            prospect_json = json.dumps(prospect_brief, ensure_ascii=False)[:5000]
        except TypeError:
            prospect_json = str(prospect_brief)[:5000]
    parts = [
        f"Founder's startup: **{startup}**",
        f"What {startup} sells: {startup_summary or '(a startup selling to businesses)'}",
        f"Target merchant: **{merchant}** ({merchant_domain or 'unknown domain'})",
        f"Why the fit / pain: {pain or 'unstated'}",
    ]
    if brand_brief:
        parts.append("\nMerchant's live brand signals (pulled from their homepage):\n"
                     + brand_brief.strip())
    if prospect_json:
        parts.append("\nStructured prospect brief (ground the spec in this):\n"
                     + prospect_json)
    parts.append(
        "\nProduce the full spec now. Start by extracting the ACCOUNT FINGERPRINT "
        "from the supplied evidence. Then write the page plan. Every section is "
        "copy-ready; every colour and font is chosen; the interactive demo has "
        "merchant-specific sample input and output; the engineer should be able "
        "to write the entire page from this without any guesswork."
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
                         prospect_brief: dict[str, Any] | None = None,
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
                           merchant_domain, pain, brand_brief,
                           prospect_brief=prospect_brief)
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
        body["max_completion_tokens"] = 5500
    else:
        body["temperature"] = 0.4
        body["max_tokens"] = 3500
    try:
        r = httpx.post(base + "/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json=body, timeout=timeout)
        if r.status_code != 200:
            return ""
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""
