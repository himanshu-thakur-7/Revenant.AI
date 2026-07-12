"""Email dispatch — Resend API, DRY_RUN by default.

Safety posture (master-plan non-negotiable):
* ``DRY_RUN=1`` (the default) never sends — it logs the exact payload and
  marks the campaign ``sent`` so the demo flow completes.
* With ``DRY_RUN=0`` the send goes ONLY to the ``to_email`` explicitly
  passed by the founder — the agent never chooses the recipient on its own.
  At the buildathon that address is a founder-owned inbox, not the prospect.

Drafts are held in a module-level registry (campaign_id → row) so the
Orchestrator's ``send_approved_email`` tool can dispatch a draft the Sales
agent produced earlier in the same process.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ghost.config import settings


# campaign_id → draft row (set by queue.push_draft)
DRAFTS: dict[str, dict[str, Any]] = {}


def register_draft(row: dict[str, Any]) -> None:
    DRAFTS[row["id"]] = row


def get_draft(campaign_id: str) -> dict[str, Any] | None:
    return DRAFTS.get(campaign_id)


def send(campaign_id: str, to_email: str) -> dict[str, Any]:
    """Dispatch a registered draft. Returns a status dict, never raises."""
    row = DRAFTS.get(campaign_id)
    if row is None:
        known = ", ".join(DRAFTS) or "none"
        return {"sent": False,
                "error": f"no draft registered for {campaign_id!r} "
                         f"(known: {known})"}
    if not to_email or "@" not in to_email:
        return {"sent": False, "error": f"invalid to_email: {to_email!r}"}

    subject = row.get("email_subject", "")
    body = _render_body(row)

    if settings.dry_run:
        _mark_sent(campaign_id)
        return {
            "sent": True,
            "dry_run": True,
            "note": ("DRY_RUN=1 — nothing left the machine. The email below "
                     "WOULD have gone to " + to_email),
            "to": to_email,
            "subject": subject,
            "body_preview": body[:400],
        }

    if not settings.resend_api_key:
        return {"sent": False,
                "error": "DRY_RUN=0 but RESEND_API_KEY is not set — add it to "
                         ".env (resend.com/api-keys) or re-enable DRY_RUN."}

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": f"{settings.founder_name} <{settings.from_email}>",
                "to": [to_email],
                "reply_to": settings.founder_email or settings.from_email,
                "subject": subject,
                "text": body,
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        return {"sent": False, "error": f"resend network error: {exc}"}

    if resp.status_code not in (200, 201):
        return {"sent": False,
                "error": f"resend {resp.status_code}: {resp.text[:200]}"}

    _mark_sent(campaign_id)
    return {"sent": True, "dry_run": False, "to": to_email,
            "resend_id": (resp.json() or {}).get("id", ""),
            "subject": subject}


def _render_body(row: dict[str, Any]) -> str:
    body = row.get("email_body", "")
    pay = row.get("payment_link", "")
    if pay and pay not in body:
        body += f"\n\nReady to start? Book the pilot here: {pay}"
    return body


def _mark_sent(campaign_id: str) -> None:
    """Flip the campaign to `sent` in Convex + mirror an event."""
    if not settings.convex_url:
        return
    try:
        httpx.post(
            f"{settings.convex_url}/api/mutation",
            json={"path": "ledger:setState",
                  "args": {"campaign_id": campaign_id, "state": "sent"},
                  "format": "json"},
            timeout=10,
        )
    except httpx.HTTPError:
        pass
    try:
        from ..bridge import bridge
        bridge._emit("sales", "mail",
                     "Missive dispatched. Now we watch.",
                     campaign_id=campaign_id,
                     payload={"state": "sent", "sent_at": time.time()})
    except Exception:
        pass
