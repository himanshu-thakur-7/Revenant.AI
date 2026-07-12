"""Razorpay payment links — "book a paid pilot".

Every microsite carries a payment button. When the prospect pays (₹1 in test
mode on stage), Razorpay fires a webhook that Convex turns into a WON state —
the moment the dashboard flips live in the demo. Here we just create the link;
the webhook handler lives in ``convex/http.ts``.
"""

from __future__ import annotations

import httpx

from .config import settings
from .log import log
from .models import Campaign, SellerProfile


def create_payment_link(campaign: Campaign, seller: SellerProfile) -> Campaign:
    log.stage("Payments: minting a Razorpay paid-pilot link…")
    amount_paise = seller.pilot_price_inr * 100

    if settings.require_live("razorpay_key_id", "razorpay_key_secret"):
        link = _razorpay_link(campaign, seller, amount_paise)
    else:
        # Offline: a stable placeholder link that still renders the CTA.
        link = f"https://rzp.io/i/demo-{campaign.id[-8:]}"
        log.dim(f"[payments] offline → {link}")

    campaign.payment_link = link
    log.ok(f"Paid-pilot link ready ({seller.pilot_price_inr} INR)")
    return campaign


def _razorpay_link(campaign: Campaign, seller: SellerProfile, amount: int) -> str:  # pragma: no cover
    try:
        resp = httpx.post(
            "https://api.razorpay.com/v1/payment_links",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={
                "amount": amount,
                "currency": "INR",
                "description": f"{seller.name} pilot — {campaign.lead.company_name}",
                "notes": {"campaign_id": campaign.id, "seller": seller.slug},
                "notify": {"sms": False, "email": False},
                "callback_url": f"{settings.convex_url or ''}/razorpay/callback",
                "callback_method": "get",
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("short_url", "#")
    except Exception as exc:
        log.warn(f"[payments] Razorpay link failed ({exc!r}); using placeholder")
        return f"https://rzp.io/i/demo-{campaign.id[-8:]}"
