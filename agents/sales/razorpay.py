"""Razorpay payment link — the ₹ that flips the deal to WON.

Creates a test-mode Payment Link for the pilot fee. The already-deployed
Convex webhook (``convex/http.ts``) verifies Razorpay's HMAC signature and
flips the campaign to ``won`` when the payment lands — so a judge paying
the ₹1 test amount makes the WON banner fire on the live console.

Gracefully returns ``""`` when keys are missing so the pipeline never
blocks on an unconfigured sponsor integration.
"""

from __future__ import annotations

from typing import Any

import httpx

from ghost.config import settings


PILOT_AMOUNT_PAISE = 499_900   # ₹4,999 — the pilot fee on the microsites
DEMO_AMOUNT_PAISE = 100        # ₹1 — judge-friendly test payment


def create_payment_link(*, company: str, campaign_id: str,
                        amount_paise: int | None = None) -> dict[str, Any]:
    """Create a Razorpay Payment Link. Returns {url, id} or {url: "", note}."""
    if not (settings.razorpay_key_id and settings.razorpay_key_secret):
        return {"url": "", "note": "razorpay keys not configured — skipping "
                                    "payment link (add RAZORPAY_KEY_ID + "
                                    "RAZORPAY_KEY_SECRET to .env)"}

    amount = amount_paise or int(
        __import__("os").getenv("RAZORPAY_AMOUNT_PAISE", DEMO_AMOUNT_PAISE))
    body = {
        "amount": amount,
        "currency": "INR",
        "description": f"Shroud 30-day pilot — {company}",
        "reference_id": campaign_id[:40],
        "notes": {"campaign_id": campaign_id, "company": company},
        "reminder_enable": False,
    }
    try:
        resp = httpx.post(
            "https://api.razorpay.com/v1/payment_links",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json=body,
            timeout=20,
        )
    except httpx.HTTPError as exc:
        return {"url": "", "note": f"razorpay network error: {exc}"}

    if resp.status_code not in (200, 201):
        return {"url": "", "note": f"razorpay {resp.status_code}: {resp.text[:200]}"}

    data = resp.json() or {}
    return {"url": data.get("short_url", ""), "id": data.get("id", "")}
