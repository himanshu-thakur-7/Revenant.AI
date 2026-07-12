"""Gmail drafts — the approved email lands in the founder's Drafts folder
with the deck and walkthrough attached. Nothing ever auto-sends.

OAuth: installed-app flow with the founder's own client secret
(``GOOGLE_OAUTH_CLIENT_JSON``). The one-time consent runs via
``revenant gmail-auth`` (opens a browser); the refresh token is cached at
``.secrets/gmail_token.json`` and renewed silently after that.

Scope is ``gmail.compose`` — drafts and send-permission on drafts only; we
never request full mailbox access.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

_CLIENT_JSON = os.path.expanduser(
    os.getenv("GOOGLE_OAUTH_CLIENT_JSON",
              "~/Revenant.AI/.secrets/google_oauth_client.json"))
_TOKEN_PATH = os.path.expanduser(
    os.getenv("GMAIL_TOKEN_PATH", "~/Revenant.AI/.secrets/gmail_token.json"))

# Gmail hard limit is 25 MB; leave headroom for base64 inflation.
_MAX_ATTACH_BYTES = 18 * 1024 * 1024


class GmailNotAuthed(RuntimeError):
    pass


def configured() -> bool:
    """True when a cached token exists (consent already granted)."""
    return os.path.exists(_TOKEN_PATH)


def authorize() -> str:
    """Run the one-time browser consent flow. Returns the authed email."""
    if not os.path.exists(_CLIENT_JSON):
        raise GmailNotAuthed(
            f"OAuth client secret not found at {_CLIENT_JSON} — set "
            "GOOGLE_OAUTH_CLIENT_JSON in .env.")
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(_CLIENT_JSON, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True,
                                  authorization_prompt_message="")
    Path(_TOKEN_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(_TOKEN_PATH).write_text(creds.to_json())
    return _profile_email(creds)


def _creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not os.path.exists(_TOKEN_PATH):
        raise GmailNotAuthed(
            "Gmail not authorized yet — run `revenant gmail-auth` once "
            "(opens a browser for consent).")
    creds = Credentials.from_authorized_user_file(_TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        Path(_TOKEN_PATH).write_text(creds.to_json())
    return creds


def _service():
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=_creds(), cache_discovery=False)


def _profile_email(creds) -> str:
    from googleapiclient.discovery import build

    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return svc.users().getProfile(userId="me").execute().get("emailAddress", "")


def create_draft(*, to_email: str, subject: str, body: str,
                 attachments: list[str | Path] | None = None) -> dict[str, Any]:
    """Create a Gmail draft with attachments. Returns
    ``{ok, draft_id, gmail_url, attached, skipped}`` or ``{ok: False, error}``.
    """
    try:
        svc = _service()
    except GmailNotAuthed as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"gmail auth error: {exc}"}

    msg = EmailMessage()
    if to_email:
        msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    attached: list[str] = []
    skipped: list[str] = []
    budget = _MAX_ATTACH_BYTES
    for raw in attachments or []:
        p = Path(raw)
        if not p.exists():
            skipped.append(f"{p.name} (missing)")
            continue
        size = p.stat().st_size
        if size > budget:
            skipped.append(f"{p.name} ({size // 1_048_576} MB — over the "
                           "attachment budget)")
            continue
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype,
                           filename=p.name)
        attached.append(p.name)
        budget -= size

    raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        draft = svc.users().drafts().create(
            userId="me", body={"message": {"raw": raw_b64}}).execute()
    except Exception as exc:
        return {"ok": False, "error": f"gmail draft create failed: {exc}"}

    draft_id = draft.get("id", "")
    return {
        "ok": True,
        "draft_id": draft_id,
        "gmail_url": "https://mail.google.com/mail/u/0/#drafts",
        "attached": attached,
        "skipped": skipped,
    }
