"""Bulletproof ON-STAGE demo path v2 — **Razorpay × Marketplace Splits (Route)**.

Same shape as ``demo_razorpay`` (canned context, deterministic shortlist,
staged progress pings, pinned artifacts) but the story is different:

The founder onboards Razorpay + their repo → the bot watches
``razorpayInc/Razorpay`` for merged PRs → **the moment a PR merges** the bot
reacts on its own: acks the merge, explains what shipped, and runs the streamed
shortlist of Indian **creator-payout startups** for whom this feature is a big
deal. No "find merchants" prompt.

Shortlist (deterministic, one at a time, ~5.5s apart, boAt-set retired):
  1. Rigi        — Series A · influencer paid communities/courses on WhatsApp
  2. Convosight  — Series A · community-creator monetization for brands
  3. Coto        — Seed · women-only creator community + Collective Artists

Rigi is the intended on-stage pick. The prototype we build for it is
STARTUP-CENTRIC — Rigi's product hero, with a "Powered by Razorpay Route"
section showing the multi-party split (creator + Rigi fee + GST + TDS).

Turn it on: onboard "Razorpay" — this module activates alongside the classic
``demo_razorpay`` (both share the same runtime switch).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

# Runtime switch flipped when the founder onboards Razorpay + the trigger repo.
# Independent from classic demo_razorpay.activate() so main's launchd bot
# (which never sees a razorpayInc/Razorpay URL in the founder's setup command)
# keeps running the boAt demo undisturbed.
_RUNTIME_ACTIVE = False


def activate() -> None:
    """Arm the split demo for the rest of this process (called when the
    founder onboards Razorpay AND the razorpayInc/Razorpay repo URL)."""
    global _RUNTIME_ACTIVE
    _RUNTIME_ACTIVE = True


def deactivate() -> None:  # test hygiene
    global _RUNTIME_ACTIVE
    _RUNTIME_ACTIVE = False


def demo_active() -> bool:
    return _RUNTIME_ACTIVE or os.getenv(
        "REVENANT_DEMO_SPLIT", "").strip().lower() in {"1", "true", "yes", "on"}


def matches_trigger_repo(source: str) -> bool:
    """True when the setup source contains the Razorpay demo trigger repo."""
    s = (source or "").lower()
    return "razorpayinc/razorpay" in s.replace("https://github.com/", "").replace("http://github.com/", "")


def is_razorpay_ctx(founder_context) -> bool:
    if founder_context is None:
        return False
    try:
        return "razorpay" in (founder_context.product_name or "").lower()
    except Exception:
        return False


# Pre-built artifacts (populated as we lock in each build). These override the
# classic demo_razorpay pinned assets when the split demo is armed.
RIGI_PROTOTYPE_URL = os.getenv(
    "RIGI_PROTOTYPE_URL",
    "https://razorpay-magic-demo.pages.dev")  # will swap to rigi build after we ship it
RIGI_WALKTHROUGH_URL = os.getenv("RIGI_WALKTHROUGH_URL", "")
RIGI_WALKTHROUGH_MP4 = Path(__file__).parent / "demo_razorpay_split_assets" / "rigi-walkthrough.mp4"


def is_rigi_pick(prospect: dict[str, Any]) -> bool:
    """True when the founder picked Rigi (the intended on-stage build)."""
    if not isinstance(prospect, dict):
        return False
    nm = (prospect.get("company_name") or "").lower()
    dom = (prospect.get("company_domain") or "").lower()
    return "rigi" in nm or "rigi.club" in dom


# ── The PR-merge storyline ────────────────────────────────────────
# Consistent narration for the manually-simulated merge. The verbiage adapts to
# WHATEVER PR title lands so a real merge on razorpayInc/Razorpay works too.
FEATURE_TAGLINE = ("One incoming payment, auto-settled to N parties — creator, "
                   "platform, GST reserve, TDS reserve — at checkout. Real "
                   "payout ledger, instant reconciliation.")

def pr_ack_lines(pr_title: str, pr_number: int, pr_url: str) -> list[tuple[str, float]]:
    """Post-merge acknowledgement narration. (message, seconds-to-dwell-after)."""
    return [
        (f"🚨 <b>PR #{pr_number} just merged in razorpayInc/Razorpay</b>\n"
         f"<code>{pr_title}</code>\n\n"
         f"<a href=\"{pr_url}\">Open the PR →</a>", 3),
        ("🧠 That's a big one. Let me read the diff…", 4),
        (f"<i>{FEATURE_TAGLINE}</i>", 3.5),
        ("🎯 Hunting Indian creator-economy startups whose payout mess this "
         "actually solves…", 2),
    ]


# ── Shortlist — Rigi first (intended on-stage pick) ───────────────
def split_shortlist() -> list[dict[str, Any]]:
    """Three real, currently-live Indian creator-payout startups. Rigi is
    first (the intended on-stage pick). Contacts are role-accurate demo data —
    the email is only ever saved as a Gmail DRAFT, never sent."""
    return [
        {
            "company_name": "Rigi",
            "company_domain": "rigi.club",
            "industry": "Creator-economy SaaS · paid communities & courses",
            "contact": {
                "name": "Ananya Singhal",
                "title": "Co-founder & CEO",
                "email_candidates": ["ananya@rigi.club"],
                "linkedin_url": "https://www.linkedin.com/company/rigi-club/",
            },
            "pain_evidence": [{
                "source_url": "https://rigi.club",
                "excerpt": ("Every paid community & course renewal on Rigi is a "
                            "single UPI in — but the money owes 4 places: the "
                            "creator, Rigi's fee, 18% GST, and 1% TDS under "
                            "194-O. Today it's a manual reconciliation across "
                            "thousands of micro-creators."),
            }],
            "fit_score": 0.97,
            "fit_rationale": (
                "Rigi's business model IS a 4-way split on every transaction — "
                "creator payout, Rigi's take, GST reserve, TDS reserve — done by "
                "hand across thousands of micro-creators today. Razorpay Route "
                "collapses that into one call at checkout: the money leaves the "
                "fan and lands in four ledgers instantly. It's not a checkout "
                "upgrade, it's their entire back-office replaced."
            ),
        },
        {
            "company_name": "Convosight",
            "company_domain": "convosight.com",
            "industry": "Community-creator monetization for brands",
            "contact": {
                "name": "Tamanna Dhamija",
                "title": "Co-founder & CEO",
                "email_candidates": ["tamanna@convosight.com"],
                "linkedin_url": "https://www.linkedin.com/company/convosight/",
            },
            "pain_evidence": [{
                "source_url": "https://convosight.com",
                "excerpt": ("Brands pay for sponsored posts inside 45,000 "
                            "WhatsApp/Facebook communities; Convosight redistributes "
                            "the payout to community admins. Every campaign becomes "
                            "a per-community payout spreadsheet, with admin fees "
                            "plus GST plus TDS on professional-services income."),
            }],
            "fit_score": 0.93,
            "fit_rationale": (
                "One brand pays Convosight ₹X for a sponsored campaign → Route "
                "auto-splits into the community admin's cut + Convosight's take + "
                "GST reserve + TDS on the admin's income, per community, at "
                "settlement. Kills the campaign-payout spreadsheet Convosight "
                "runs manually today across 45k communities."
            ),
        },
        {
            "company_name": "Coto",
            "company_domain": "coto.world",
            "industry": "Women-only creator community · Coto Gains tokens",
            "contact": {
                "name": "Tarun Katial",
                "title": "Co-founder & CEO",
                "email_candidates": ["tarun@coto.world"],
                "linkedin_url": "https://www.linkedin.com/company/cotoworld/",
            },
            "pain_evidence": [{
                "source_url": "https://coto.world",
                "excerpt": ("Coto Gains reward program and paid rooms (in partnership "
                            "with Collective Artists Network) mean each fan payment "
                            "splits across the woman creator, Coto's platform fee, "
                            "the Collective Artists rev share, plus GST and TDS."),
            }],
            "fit_score": 0.88,
            "fit_rationale": (
                "As Coto rolls out paid rooms + Coto Gains (with Collective Artists "
                "Network), every fan payment is a 4- or 5-way split: creator + Coto "
                "fee + Collective Artists share + GST + TDS. Route ships that at "
                "the source — the money never sits in one pot waiting to be divided."
            ),
        },
    ]


# ── Staged build for the picked startup — Rigi is 140s ────────────
# Same staging shape as demo_razorpay._STAGED_BUILD, tuned for the split story.
_STAGED_BUILD: list[tuple[str, str, float]] = [
    ("engineer",      "Reading Razorpay Route docs + Rigi's product and paid-community flows…", 30),
    ("engineer",      "Designing a Rigi-centric prototype with Route drop-in for creator payouts…", 35),
    ("engineer",      "Wiring the 4-way split simulator (creator + Rigi + GST + TDS)…", 40),
    ("engineer",      "Deploying to Cloudflare's edge + hardening the UI…", 35),
    ("engineer_done", RIGI_PROTOTYPE_URL, 5),
    ("deck",          "Designing the co-branded Razorpay Route × Rigi pitch deck…", 14),
    ("director",      "Filming the walkthrough — AI presenter narrating on-screen…", 40),
    ("director_done", "", 3),
    ("sales",         "Drafting the outreach email to Ananya at Rigi…", 8),
    ("sales_done",    "", 2),
]


def run_staged_build(on_stage, *, sleep=time.sleep) -> None:
    """Emit staged progress pings so the pre-built Rigi prototype + walkthrough
    'build' convincingly on stage."""
    for name, detail, dwell in _STAGED_BUILD:
        try:
            on_stage(name, detail)
        except Exception:
            pass
        sleep(dwell)


# ── PR-ack staging (called BEFORE the shortlist stream) ───────────
def run_pr_ack(send, pr_title: str, pr_number: int, pr_url: str,
               *, sleep=time.sleep) -> None:
    """Play the 4-beat 'we saw the merge' narration."""
    for msg, dwell in pr_ack_lines(pr_title, pr_number, pr_url):
        try:
            send(msg)
        except Exception:
            pass
        sleep(dwell)
