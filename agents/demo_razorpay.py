"""Bulletproof ON-STAGE demo path for **Razorpay** (standalone bot only).

Everything here is DETERMINISTIC — no live LLM/Apollo variance — and gated
behind ``REVENANT_DEMO=1`` so the reusable product is untouched when the flag
is off. It fires ONLY for Razorpay (any other onboarded startup runs the normal
live pipeline). Scripted flow the audience sees:

1. Founder onboards *Razorpay* → a fixed 3-merchant shortlist (boAt first).
2. Founder picks *boAt* → the Live Deal Room brief streams (with the rick-roll
   diversion) while the prototype "builds" for ~staged pings, then reveals the
   pre-built, pre-deployed Magic-Checkout-for-boAt site.
3. Director "films" for ~staged pings, then delivers the pre-built walkthrough
   video (Fiona narrating bottom-right). The video is delivered SEPARATELY —
   it is NOT embedded in the prototype.

Turn it on:  ``export REVENANT_DEMO=1``  then restart the bot service.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

# Pre-built, pre-deployed Razorpay-Magic-Checkout-for-boAt prototype (stable
# production URL — tracks the latest deploy of the razorpay-magic-demo project).
PROTOTYPE_URL = "https://razorpay-magic-demo.pages.dev"

# Site source lives in-repo (agents/demo_razorpay_site) so it survives.
# Redeploy after editing:  deploy_dir(SITE_DIR, project="razorpay-magic-demo")
SITE_DIR = Path(__file__).parent / "demo_razorpay_site"

# Pre-built walkthrough (Fiona narrating bottom-right). Both the hosted URL
# (immutable per-deploy hash) and a repo-local mp4 (survives out/ cleanup) so
# the bot can deliver the actual video file on stage. Re-record with:
#   revenant director <PROTOTYPE_URL> --company boAt ...   (DIRECTOR_SKIP_LIPSYNC=1)
WALKTHROUGH_URL = os.getenv(
    "RAZORPAY_WALKTHROUGH_URL",
    "https://43b11f25.revenant-walkthroughs.pages.dev/walkthrough.mp4")
WALKTHROUGH_MP4 = Path(__file__).parent / "demo_razorpay_assets" / "boat-walkthrough.mp4"


# Runtime switch: flipped on the moment the founder onboards "Razorpay" (see
# bot._do_setup), so the demo self-activates with zero env fiddling / restart.
# The REVENANT_DEMO env var still works as an alternative pre-arm.
_RUNTIME_ACTIVE = False


def activate() -> None:
    """Arm the on-stage demo for the rest of this process (called when the
    founder onboards Razorpay)."""
    global _RUNTIME_ACTIVE
    _RUNTIME_ACTIVE = True


def demo_active() -> bool:
    """True when the on-stage demo is armed — either at runtime (founder typed
    Razorpay in setup) or via REVENANT_DEMO=1."""
    if _RUNTIME_ACTIVE:
        return True
    return os.getenv("REVENANT_DEMO", "").strip().lower() in {"1", "true", "yes", "on"}


def is_razorpay(founder_context) -> bool:
    """True when the onboarded startup is Razorpay (by product name/source)."""
    if founder_context is None:
        return False
    try:
        name = (founder_context.product_name or "").lower()
    except Exception:
        name = ""
    src = (getattr(founder_context, "source", "") or "").lower()
    return "razorpay" in name or "razorpay" in src


def is_boat_pick(prospect: dict[str, Any]) -> bool:
    """True when the founder picked the boAt demo merchant."""
    if not isinstance(prospect, dict):
        return False
    dom = (prospect.get("company_domain") or "").lower()
    nm = (prospect.get("company_name") or "").lower()
    return "boat" in dom or "boat" in nm


# ── canned Razorpay founder context ───────────────────────────────
_RAZORPAY_SUMMARY = (
    "Razorpay is India's leading full-stack payments and business-banking "
    "platform. Its flagship checkout product, Magic Checkout, turns any "
    "merchant's checkout into a 1-click, address-prefilled, RTO-protected flow "
    "for the 100M+ shoppers already saved in the Razorpay network. Core value: "
    "up to ~40% higher conversion by removing form-filling, predictive COD/RTO "
    "risk models that nudge risky cash-on-delivery orders to prepaid, RTO "
    "Protection that reimburses failed deliveries, a coupon/affordability engine, "
    "and 100+ payment methods (UPI, cards, netbanking, wallets, EMI, BNPL). "
    "Go-live is ~a day via a single script tag on Shopify, WooCommerce, or "
    "custom stores. PCI-DSS Level 1 certified. ICP: high-volume D2C and "
    "e-commerce brands with COD-heavy, drop-off-prone checkouts. Tagline: "
    "'The 1-click checkout that recovers every drop-off.'"
)


def razorpay_context():
    """A canned FounderContext for Razorpay — no repo clone, fixed summary +
    product name (Razorpay isn't open-source, so /setup can't ingest a repo)."""
    from .context import FounderContext
    ctx = FounderContext(
        source="razorpay.com",
        root=Path("/tmp"),
        files={"README.md": "# Razorpay\n\n" + _RAZORPAY_SUMMARY},
    )
    ctx._summary_cache = _RAZORPAY_SUMMARY
    return ctx


# ── deterministic shortlist (boAt first) ──────────────────────────
def razorpay_shortlist() -> list[dict[str, Any]]:
    """Three real, ICP-fit Indian D2C merchants for Razorpay Magic Checkout.
    boAt is first (the intended on-stage pick). Contacts are role-accurate demo
    data — the email is only ever saved as a Gmail DRAFT, never sent."""
    return [
        {
            "company_name": "boAt",
            "company_domain": "boat-lifestyle.com",
            "industry": "D2C consumer electronics / audio",
            "contact": {
                "name": "Aman Gupta",
                "title": "Co-founder & CMO",
                "email_candidates": ["partnerships@boat-lifestyle.com"],
                "linkedin_url": "https://www.linkedin.com/company/boat-lifestyle/",
            },
            "pain_evidence": [{
                "source_url": "https://www.boat-lifestyle.com",
                "excerpt": ("COD-heavy electronics checkout: high return-to-origin "
                            "on cash-on-delivery orders, plus cart drop-off from "
                            "manual address entry on mobile."),
            }],
            "fit_score": 0.95,
            "fit_rationale": (
                "boAt is a COD-heavy D2C electronics brand — exactly where Magic "
                "Checkout pays for itself. 1-click prefill for the 100M+ pre-saved "
                "Razorpay shoppers kills mobile drop-off, and predictive COD/RTO "
                "models nudge risky orders to prepaid, cutting return-to-origin "
                "losses on high-value audio. A per-store pilot ships in a day."
            ),
        },
        {
            "company_name": "Mamaearth",
            "company_domain": "mamaearth.in",
            "industry": "D2C beauty & personal care",
            "contact": {
                "name": "Varun Alagh",
                "title": "Co-founder & CEO",
                "email_candidates": ["partnerships@mamaearth.in"],
                "linkedin_url": "https://www.linkedin.com/company/mamaearth/",
            },
            "pain_evidence": [{
                "source_url": "https://mamaearth.in",
                "excerpt": ("High-volume D2C beauty checkout with heavy COD mix "
                            "and repeat first-time buyers abandoning at address "
                            "and payment entry."),
            }],
            "fit_score": 0.9,
            "fit_rationale": (
                "Mamaearth's high-frequency, low-AOV beauty orders live or die on "
                "checkout friction. Magic Checkout's prefilled 1-click flow plus "
                "the coupon/affordability engine lifts conversion on exactly the "
                "impulse purchases Mamaearth runs, while COD risk scoring trims "
                "return-to-origin on new-shopper cash orders."
            ),
        },
        {
            "company_name": "Lenskart",
            "company_domain": "lenskart.com",
            "industry": "D2C eyewear / omnichannel retail",
            "contact": {
                "name": "Peyush Bansal",
                "title": "Founder & CEO",
                "email_candidates": ["partnerships@lenskart.com"],
                "linkedin_url": "https://www.linkedin.com/company/lenskart.com/",
            },
            "pain_evidence": [{
                "source_url": "https://www.lenskart.com",
                "excerpt": ("Considered eyewear purchases with prescription steps "
                            "see checkout abandonment, and COD orders on higher-"
                            "ticket frames drive return-to-origin cost."),
            }],
            "fit_score": 0.86,
            "fit_rationale": (
                "Lenskart's higher-ticket, considered checkout is where a "
                "one-tap, address-prefilled flow removes the last-mile friction, "
                "and RTO Protection plus prepaid nudges de-risk COD on premium "
                "frames — recovering revenue Lenskart loses at the final step."
            ),
        },
    ]


# ── staged build (feels like real work on stage) ──────────────────
# (stage, detail, seconds-to-dwell-AFTER-emitting). Reuses the real stage
# names the bot already narrates, so the founder sees the normal flow. The
# Live Deal Room brief + rick-roll diversion run in parallel (bot._build_for),
# unchanged. NO "embed_media" stage — the walkthrough is delivered separately,
# never embedded in the prototype.
_STAGED_BUILD: list[tuple[str, str, float]] = [
    # Engineer ≈ 140s (the founder asked for a real, natural-feeling build).
    ("engineer",      "Reading Razorpay's product docs + boAt's catalog & brand…", 30),
    ("engineer",      "Designing the Magic Checkout prototype for boAt…", 35),
    ("engineer",      "Wiring the 1-click prefill + COD/RTO risk logic…", 40),
    ("engineer",      "Deploying to Cloudflare's edge + hardening the UI…", 35),
    ("engineer_done", PROTOTYPE_URL, 5),
    # Director ≈ 22s.
    ("director",      "Filming the walkthrough — AI presenter narrating on-screen…", 22),
    ("director_done", "", 3),
    # Sales.
    ("sales",         "Writing the pitch email + assembling the deck…", 10),
    ("sales_done",    "", 2),
]


def run_staged_build(on_stage, *, sleep=time.sleep) -> None:
    """Emit staged progress pings so the pre-built boAt prototype + walkthrough
    'build' convincingly on stage (engineer ~140s, director ~22s)."""
    for name, detail, dwell in _STAGED_BUILD:
        try:
            on_stage(name, detail)
        except Exception:
            pass
        sleep(dwell)


# ── staged ingestion (~10s) ───────────────────────────────────────
_INGEST_STEPS: list[tuple[str, float]] = [
    ("🔗 Pulling Razorpay's product surface — Magic Checkout, pricing, docs…", 4),
    ("📚 Reading the COD/RTO playbook, UPI methods, and merchant case studies…", 4),
    ("🧠 Building a working model of what Razorpay sells and who it's for…", 2),
]


def run_staged_ingest(send, *, sleep=time.sleep) -> None:
    """~10s of ingestion pings so onboarding Razorpay feels like real work."""
    for msg, dwell in _INGEST_STEPS:
        try:
            send(msg)
        except Exception:
            pass
        sleep(dwell)
