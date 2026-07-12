"""RevenantBot — the interactive Telegram gateway.

Single-founder, single-chat design: long-polls for updates, runs the full
agent fleet on a dictated brief, streams a live progress board, then delivers
the artifacts with inline action buttons. Approve/Amend/Discard drive the
human-in-the-loop send.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass, field
from typing import Any

from ghost.config import settings

from ..context import FounderContext
from ..runner import CampaignArtifacts, redraft_email, run_campaign
from .api import TelegramAPI, inline_keyboard


# ── per-chat session ───────────────────────────────────────────
@dataclass
class Session:
    mode: str = "idle"          # idle | running | awaiting_recipient | awaiting_amendment
    art: CampaignArtifacts | None = None
    status_msg_id: int | None = None
    draft_msg_id: int | None = None
    stages: dict[str, str] = field(default_factory=dict)


_STAGE_ROWS = [
    ("research", "🔍", "Research"),
    ("engineer", "⚙️", "Engineer"),
    ("director", "🎬", "Director"),
    ("sales", "✍️", "Sales"),
]


class RevenantBot:
    def __init__(self, token: str, founder_context: FounderContext,
                 *, allowed_chat_id: int | None = None,
                 skip_lipsync: bool = True) -> None:
        self.api = TelegramAPI(token)
        self.ctx = founder_context
        self.allowed = allowed_chat_id
        self.skip_lipsync = skip_lipsync
        self._sessions: dict[int, Session] = {}
        self._running = False

    def session(self, chat_id: int) -> Session:
        return self._sessions.setdefault(chat_id, Session())

    # ── main loop ─────────────────────────────────────────────
    def run(self) -> None:
        self._running = True
        offset: int | None = None
        print(f"[telegram] @{_me(self.api)} online — waiting for the founder…")
        while self._running:
            updates = self.api.get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self._dispatch(u)
                except Exception as exc:  # a bad update must not kill the bot
                    print(f"[telegram] handler error: {exc!r}")

    def stop(self) -> None:
        self._running = False

    # ── dispatch ──────────────────────────────────────────────
    def _dispatch(self, u: dict[str, Any]) -> None:
        if "callback_query" in u:
            self._on_callback(u["callback_query"])
            return
        msg = u.get("message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if self.allowed is not None and chat_id != self.allowed:
            self.api.send_message(chat_id,
                                  "This Revenant is bound to another founder.")
            return
        print(f"[telegram] chat {chat_id}: {text[:80]}")

        if text.startswith("/"):
            self._on_command(chat_id, text)
            return

        sess = self.session(chat_id)
        if sess.mode == "awaiting_recipient":
            self._do_send(chat_id, text)
            return
        if sess.mode == "awaiting_amendment":
            self._do_amend(chat_id, text)
            return

        # otherwise: a targeting brief → run the fleet
        self._run_campaign(chat_id, text)

    def _on_command(self, chat_id: int, text: str) -> None:
        cmd = text.split()[0].lstrip("/").lower()
        if cmd in ("start", "help"):
            self.api.send_message(chat_id, _WELCOME.format(
                founder=html.escape(settings.founder_name),
                company=html.escape(settings.founder_company or "your startup")))
        elif cmd == "whoami":
            self.api.send_message(chat_id, f"chat id: <code>{chat_id}</code>")
        else:
            self.api.send_message(chat_id, "Unknown command. Send /help.")

    # ── run the fleet ─────────────────────────────────────────
    def _run_campaign(self, chat_id: int, brief: str) -> None:
        sess = self.session(chat_id)
        sess.mode = "running"
        sess.stages = {}
        m = self.api.send_message(chat_id, self._board(sess, "🕯️ Revenant is waking…"))
        sess.status_msg_id = m.get("result", {}).get("message_id")

        def on_stage(stage: str, detail: str) -> None:
            base = stage.replace("_done", "")
            if stage.endswith("_done"):
                sess.stages[base] = f"✓ {detail}" if detail else "✓"
            elif stage in ("research", "engineer", "director", "sales"):
                sess.stages[stage] = "⟳ …"
            self._update_board(chat_id, sess)

        self.api.send_chat_action(chat_id, "record_video")
        art = run_campaign(brief, self.ctx, on_stage=on_stage,
                           skip_lipsync=self.skip_lipsync)

        if not art.ok:
            self._update_board(chat_id, sess, header="✗ Run failed")
            self.api.send_message(chat_id, "⚠️ " + html.escape(art.error))
            sess.mode = "idle"
            return

        sess.art = art
        self._update_board(chat_id, sess, header=f"✅ Campaign ready — <b>{html.escape(art.company)}</b>")
        self._deliver(chat_id, art)
        sess.mode = "idle"

    # ── deliver artifacts ─────────────────────────────────────
    def _deliver(self, chat_id: int, art: CampaignArtifacts) -> None:
        # 1. the walkthrough video (upload the file so it plays inline)
        cap = f"🎬 <b>AI walkthrough</b> for {html.escape(art.company)}"
        if art.walkthrough_mp4 and os.path.exists(art.walkthrough_mp4):
            self.api.send_chat_action(chat_id, "upload_video")
            res = self.api.send_video(chat_id, art.walkthrough_mp4, caption=cap)
            if not res.get("ok") and art.walkthrough_url:
                self.api.send_message(chat_id, f"{cap}\n{art.walkthrough_url}")
        elif art.walkthrough_url:
            self.api.send_message(chat_id, f"{cap}\n{art.walkthrough_url}")

        # 2. the live prototype (link preview shows a card)
        if art.prototype_url and not art.prototype_url.startswith("file:"):
            self.api.send_message(
                chat_id,
                f"🕸 <b>Live prototype</b> — built for {html.escape(art.company)}\n"
                f"{art.prototype_url}")

        # 3. the pitch deck
        if art.deck_pptx and os.path.exists(art.deck_pptx):
            self.api.send_chat_action(chat_id, "upload_document")
            self.api.send_document(chat_id, art.deck_pptx,
                                   caption=f"📊 <b>Pitch deck</b> — {html.escape(art.company)}")
        elif art.deck_url and not art.deck_url.startswith("file:"):
            self.api.send_message(chat_id, f"📊 <b>Pitch deck</b>\n{art.deck_url}")

        # 4. the email draft + action buttons
        self._send_draft(chat_id, art)

    def _send_draft(self, chat_id: int, art: CampaignArtifacts) -> None:
        to = art.recipient_email or "—"
        body = (
            "✉️ <b>Email draft — awaiting your call</b>\n\n"
            f"<b>To:</b> {html.escape(art.contact_name or 'team')} "
            f"&lt;{html.escape(to)}&gt;\n"
            f"<b>Subject:</b> {html.escape(art.email_subject)}\n\n"
            f"{html.escape(art.email_body)}"
        )
        cid = art.campaign_id
        kb = inline_keyboard([
            [("✅ Approve & Send", f"approve:{cid}"), ("✏️ Amend", f"amend:{cid}")],
            [("❌ Discard", f"discard:{cid}")],
        ])
        m = self.api.send_message(chat_id, body, reply_markup=kb)
        self.session(chat_id).draft_msg_id = m.get("result", {}).get("message_id")

    # ── callbacks ─────────────────────────────────────────────
    def _on_callback(self, cq: dict[str, Any]) -> None:
        data = cq.get("data", "")
        chat_id = cq["message"]["chat"]["id"]
        cb_id = cq["id"]
        action, _, _cid = data.partition(":")
        sess = self.session(chat_id)

        if action == "approve":
            self.api.answer_callback(cb_id, "Approving…")
            art = sess.art
            if not art:
                self.api.send_message(chat_id, "No active draft to send.")
                return
            if art.recipient_email:
                self._do_send(chat_id, art.recipient_email)
            else:
                sess.mode = "awaiting_recipient"
                self.api.send_message(
                    chat_id, "📮 Reply with the recipient email address "
                             "(a founder-owned inbox for the demo).")
        elif action == "amend":
            self.api.answer_callback(cb_id, "Tell me what to change")
            sess.mode = "awaiting_amendment"
            self.api.send_message(
                chat_id, "✏️ Reply with what you'd like changed "
                         "(e.g. “make it shorter and mention SOC 2”).")
        elif action == "discard":
            self.api.answer_callback(cb_id, "Discarded")
            if sess.draft_msg_id:
                self.api.edit_message(chat_id, sess.draft_msg_id,
                                      "❌ <i>Draft discarded.</i>", reply_markup={})
            sess.mode = "idle"
            sess.art = None
        else:
            self.api.answer_callback(cb_id)

    # ── send / amend ──────────────────────────────────────────
    def _do_send(self, chat_id: int, to_email: str) -> None:
        sess = self.session(chat_id)
        art = sess.art
        if not art:
            self.api.send_message(chat_id, "No active draft.")
            sess.mode = "idle"
            return
        from ..sales import send as send_mod
        result = send_mod.send(art.campaign_id, to_email.strip())
        sess.mode = "idle"
        if result.get("sent") and result.get("dry_run"):
            self.api.send_message(
                chat_id, f"🧪 <b>DRY_RUN</b> — nothing actually left the machine.\n"
                         f"It <i>would</i> have gone to <code>{html.escape(to_email)}</code>.\n"
                         f"Flip <code>DRY_RUN=0</code> in .env to send for real.")
        elif result.get("sent"):
            self.api.send_message(
                chat_id, f"📨 <b>Sent</b> to <code>{html.escape(to_email)}</code>. Now we watch.")
            if sess.draft_msg_id:
                self.api.edit_message(chat_id, sess.draft_msg_id,
                                      f"✅ <i>Sent to {html.escape(to_email)}.</i>",
                                      reply_markup={})
        else:
            self.api.send_message(chat_id, "⚠️ " + html.escape(result.get("error", "send failed")))

    def _do_amend(self, chat_id: int, amendment: str) -> None:
        sess = self.session(chat_id)
        art = sess.art
        if not art:
            sess.mode = "idle"
            return
        self.api.send_chat_action(chat_id, "typing")
        m = self.api.send_message(chat_id, "✏️ Reworking the draft…")
        redraft_email(art, amendment, self.ctx)
        sess.mode = "idle"
        mid = m.get("result", {}).get("message_id")
        if mid:
            self.api.edit_message(chat_id, mid, "✅ Draft updated.")
        self._send_draft(chat_id, art)

    # ── progress board ────────────────────────────────────────
    def _board(self, sess: Session, header: str) -> str:
        lines = [header, ""]
        for key, icon, label in _STAGE_ROWS:
            state = sess.stages.get(key, "·")
            lines.append(f"{icon} <b>{label}</b>   {html.escape(state)}")
        return "\n".join(lines)

    def _update_board(self, chat_id: int, sess: Session,
                      header: str | None = None) -> None:
        if not sess.status_msg_id:
            return
        head = header or "🕯️ Revenant is working…"
        self.api.edit_message(chat_id, sess.status_msg_id, self._board(sess, head))


def _me(api: TelegramAPI) -> str:
    try:
        import httpx
        r = httpx.get(api._base + "/getMe", timeout=10).json()
        return r.get("result", {}).get("username", "revenant")
    except Exception:
        return "revenant"


_WELCOME = """🕯️ <b>Revenant</b> — your autonomous outbound engineer.

I hunt a fit prospect, build them a working prototype, film an AI walkthrough,
and draft the outreach — then hand it to you to approve.

Just tell me who to go after, e.g.:
<i>“Find a US healthtech startup for {company}”</i>

I'll send you the video, the prototype, the deck, and the email — with a
button to approve or amend. Nothing sends without your tap.

— on behalf of {founder}"""
