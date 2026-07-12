"""RevenantBot — the interactive Telegram gateway.

Single-founder, single-chat design: long-polls for updates, runs the full
agent fleet on a dictated brief, streams a live progress board, then delivers
the artifacts with inline action buttons. Approve/Amend/Discard drive the
human-in-the-loop send.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from typing import Any

_URL_RX = re.compile(r"(?:https?://|git@|(?:github\.com/))\S+|"
                     r"\b[\w.-]+/[\w.-]+(?=\s|$)")


def _extract_url(text: str) -> str:
    """Pull a repo/docs URL (or owner/repo shorthand) out of a message."""
    for m in _URL_RX.finditer(text.strip()):
        cand = m.group(0).rstrip(".,)")
        # owner/repo shorthand must look like a slug pair, not a sentence word
        if "://" in cand or cand.startswith(("git@", "github.com/")):
            return cand
        if re.fullmatch(r"[\w.-]+/[\w.-]+", cand) and "." not in cand.split("/")[0]:
            return cand
    return ""

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
    # per-session founder context — /setup swaps the startup this chat
    # represents; None falls back to the bot's boot-time default
    ctx: FounderContext | None = None
    ctx_label: str = ""


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

        self._route_text(chat_id, text)

    # ── intent routing (the bot's brain) ──────────────────────
    def _route_text(self, chat_id: int, text: str) -> None:
        """Decide what the founder wants — never launch a 4-minute fleet run
        on 'hi'. URLs configure; explicit hunt asks run; everything else is
        answered conversationally from the founder context."""
        # bare URL (or 'configure <url>') → reconfigure this session
        url = _extract_url(text)
        if url and len(text.split()) <= 6:
            self._do_setup(chat_id, url)
            return

        intent = self._classify(chat_id, text)
        if intent == "run_campaign":
            self._run_campaign(chat_id, text)
        elif intent == "configure" and url:
            self._do_setup(chat_id, url)
        else:
            self._answer(chat_id, text)

    def _classify(self, chat_id: int, text: str) -> str:
        from ghost.llm import complete_json

        result = complete_json(
            "Classify the founder's Telegram message for an outbound-sales "
            "agent. Categories:\n"
            "- run_campaign: explicitly asks to find/hunt/target prospects, "
            "run outbound, build a campaign, or names a vertical to pursue "
            "(e.g. 'find me a healthtech prospect', 'go after fintech CTOs')\n"
            "- configure: asks to switch/load a different startup/company "
            "context, or shares a repo/docs link\n"
            "- question: asks about their company, the product, a past run, "
            "or how anything works\n"
            "- smalltalk: greetings, thanks, everything else\n\n"
            f"Message: {text!r}\n\n"
            'Respond: {"intent": "<category>"}',
            agent="telegram.intent",
            offline={"intent": "question"},
        )
        intent = str(result.get("intent", "question")).strip().lower()
        return intent if intent in {"run_campaign", "configure", "question",
                                    "smalltalk"} else "question"

    def _answer(self, chat_id: int, text: str) -> None:
        """Conversational reply grounded in the session's founder context."""
        from ghost.llm import complete

        self.api.send_chat_action(chat_id, "typing")
        sess = self.session(chat_id)
        ctx = sess.ctx or self.ctx
        briefing = ctx.summary() if ctx else "(no startup context loaded)"
        reply = complete(
            f"The founder asked: {text}\n\nAnswer in 1-4 short sentences, "
            "plain text (no markdown headers). If they seem ready to hunt "
            "prospects, remind them they can just say who to go after.",
            agent="telegram.chat",
            system=("You are Revenant, an autonomous outbound engineer working "
                    f"for {settings.founder_name}. You represent this startup:\n"
                    f"{briefing}\n\nBe warm, terse, and concrete. Never invent "
                    "product facts not in the briefing."),
            offline="I'm Revenant. Tell me who to go after — e.g. “find a US "
                    "healthtech startup” — and I'll build the whole campaign.",
        )
        self.api.send_message(chat_id, html.escape(reply.strip()),
                              disable_preview=True)

    def _do_setup(self, chat_id: int, source: str) -> None:
        """Ingest a new startup context for this session (github URL or path)."""
        sess = self.session(chat_id)
        self.api.send_chat_action(chat_id, "typing")
        m = self.api.send_message(
            chat_id, f"🧬 Reading <code>{html.escape(source)}</code> — "
                     "ingesting docs + code…")
        mid = m.get("result", {}).get("message_id")
        try:
            if source.startswith(("http://", "https://", "git@")) or (
                    "/" in source and not source.startswith(("~", "/", "."))):
                ctx = FounderContext.from_github(source)
            else:
                ctx = FounderContext.from_folder(source)
            briefing = ctx.summary()
        except Exception as exc:
            if mid:
                self.api.edit_message(chat_id, mid,
                                      f"⚠️ Couldn't ingest that: {html.escape(str(exc)[:200])}")
            return
        sess.ctx = ctx
        sess.ctx_label = source
        one_liner = briefing.strip().splitlines()
        # pull the first non-header, non-empty line as the human summary
        gist = next((ln.strip("*- #") for ln in one_liner
                     if ln.strip() and not ln.strip().startswith("#")), "")
        if mid:
            self.api.edit_message(
                chat_id, mid,
                f"🧬 <b>Context configured</b> — {len(ctx.files)} files from "
                f"<code>{html.escape(source)}</code>\n\n"
                f"<i>{html.escape(gist[:300])}</i>\n\n"
                "This chat now sells on behalf of that startup. "
                "Tell me who to go after.")

    def _on_command(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("start", "help"):
            self.api.send_message(chat_id, _WELCOME.format(
                founder=html.escape(settings.founder_name),
                company=html.escape(settings.founder_company or "your startup")))
        elif cmd == "setup":
            if arg:
                self._do_setup(chat_id, arg)
            else:
                self.api.send_message(
                    chat_id, "Usage: <code>/setup github.com/you/your-startup</code> "
                             "— I'll read the repo and sell on its behalf.")
        elif cmd == "context":
            sess = self.session(chat_id)
            label = sess.ctx_label or "(default) " + str(getattr(self.ctx, "source", "~/shroud"))
            n = len((sess.ctx or self.ctx).files) if (sess.ctx or self.ctx) else 0
            self.api.send_message(chat_id,
                                  f"🧬 Current startup: <code>{html.escape(str(label))}</code> "
                                  f"({n} files)")
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
        art = run_campaign(brief, sess.ctx or self.ctx, on_stage=on_stage,
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

    # ── approve → Gmail draft ─────────────────────────────────
    def _do_send(self, chat_id: int, to_email: str) -> None:
        """Approve = save a ready-to-send draft in the founder's Gmail with
        the deck + walkthrough attached. Never auto-sends."""
        sess = self.session(chat_id)
        art = sess.art
        if not art:
            self.api.send_message(chat_id, "No active draft.")
            sess.mode = "idle"
            return
        sess.mode = "idle"
        to_email = to_email.strip()

        from ..sales import gmail_draft
        if not gmail_draft.configured():
            self.api.send_message(
                chat_id, "⚠️ Gmail isn't authorized yet. Run "
                         "<code>revenant gmail-auth</code> on the laptop once, "
                         "then tap Approve again.")
            return

        self.api.send_chat_action(chat_id, "upload_document")
        body = art.email_body
        links = [f"Prototype: {art.prototype_url}" if art.prototype_url else "",
                 f"Walkthrough: {art.walkthrough_url}" if art.walkthrough_url else "",
                 f"Deck: {art.deck_url}" if art.deck_url else ""]
        links = [l for l in links if l]
        if links and not all(l.split(": ", 1)[1] in body for l in links):
            body += "\n\n" + "\n".join(links)

        result = gmail_draft.create_draft(
            to_email=to_email,
            subject=art.email_subject,
            body=body,
            attachments=[p for p in (art.deck_pptx, art.walkthrough_mp4) if p],
        )
        if not result.get("ok"):
            self.api.send_message(chat_id, "⚠️ " + html.escape(result.get("error", "draft failed")))
            return

        attached = ", ".join(result.get("attached") or []) or "links only"
        note = ""
        if result.get("skipped"):
            note = "\n<i>skipped: " + html.escape(", ".join(result["skipped"])) + "</i>"
        self.api.send_message(
            chat_id,
            f"📥 <b>Saved to your Gmail drafts</b> — to "
            f"<code>{html.escape(to_email or 'no recipient yet')}</code>\n"
            f"📎 attached: {html.escape(attached)}{note}\n"
            f"Open Gmail → Drafts, give it one last look, hit send when ready.\n"
            f"{result.get('gmail_url','')}", disable_preview=True)
        if sess.draft_msg_id:
            self.api.edit_message(chat_id, sess.draft_msg_id,
                                  "✅ <i>Approved — waiting in your Gmail drafts.</i>",
                                  reply_markup={})
        # mirror to the live console: campaign leaves the review queue
        try:
            from ..bridge import bridge
            bridge._emit("sales", "mail",
                         "Approved — draft parked in the founder's Gmail.",
                         campaign_id=art.campaign_id,
                         payload={"state": "sent"})
        except Exception:
            pass

    def _do_amend(self, chat_id: int, amendment: str) -> None:
        sess = self.session(chat_id)
        art = sess.art
        if not art:
            sess.mode = "idle"
            return
        self.api.send_chat_action(chat_id, "typing")
        m = self.api.send_message(chat_id, "✏️ Reworking the draft…")
        redraft_email(art, amendment, sess.ctx or self.ctx)
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

<b>1. Point me at your startup</b> (or keep the default):
<code>/setup github.com/you/your-startup</code>

<b>2. Tell me who to go after:</b>
<i>“Find a US healthtech startup for {company}”</i>

I'll send back the video, the live prototype, the deck, and the email draft —
approve, amend, or discard with a tap. Approved mail lands in your Gmail
drafts with everything attached; nothing sends itself.

You can also just ask me things — I've read the whole codebase.

— on behalf of {founder}"""
