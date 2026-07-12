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
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class _TypingLoop:
    """Keep Telegram's 'typing…' indicator alive during long stages.

    Telegram's ``sendChatAction`` lasts ~5 s. We fire it every 4 s from a
    daemon thread so the founder's phone shows a continuous "revenant is
    working" hint even when a stage (Playwright capture, ffmpeg mux) takes
    a minute. Exit clean via ``with`` / ``__exit__``.
    """

    def __init__(self, api, chat_id: int, action: str = "typing") -> None:
        self.api, self.chat_id = api, chat_id
        self._action = action
        self._stop = threading.Event()
        self._th: threading.Thread | None = None

    def set_action(self, action: str) -> None:
        self._action = action

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.api.send_chat_action(self.chat_id, self._action)
            except Exception:
                pass
            self._stop.wait(4.0)

    def __enter__(self) -> "_TypingLoop":
        self._th = threading.Thread(target=self._run, daemon=True,
                                    name=f"typing-{self.chat_id}")
        self._th.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._th:
            self._th.join(timeout=1.5)

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
from ..runner import (
    CampaignArtifacts, build_campaign_for, find_shortlist,
    redraft_email, run_campaign,
)
from .api import TelegramAPI, inline_keyboard


# ── per-chat session ───────────────────────────────────────────
@dataclass
class Session:
    mode: str = "idle"          # idle | running | awaiting_recipient | awaiting_amendment | awaiting_pick
    art: CampaignArtifacts | None = None
    draft_msg_id: int | None = None
    # per-session founder context — /setup swaps the startup this chat
    # represents; None falls back to the bot's boot-time default
    ctx: FounderContext | None = None
    ctx_label: str = ""
    # First-interaction gate: we ask who they're selling for once per session,
    # regardless of what the CLI --repo default was. Feels like an agency
    # onboarding conversation instead of a hard-coded pipeline.
    setup_done: bool = False
    # After Research surfaces 3 verified candidates, they wait here until
    # the founder taps one — no downstream agents run without a pick.
    shortlist: list[dict] = field(default_factory=list)
    shortlist_msg_id: int | None = None
    last_brief: str = ""


# Human narration templates per stage boundary — each fires a NEW message
# to the founder so their phone pings when something meaningful happens.
# ``{a}`` interpolates the stage's own detail argument (company name, url, etc).
_STAGE_NARR: dict[str, str] = {
    "research":           "🔎 On it — hunting a fit prospect right now.",
    "brainstorm":         "🧠 {a}",
    "apollo_pick":        "🎯 {a}",
    "apollo_contact":     "",  # silent — apollo_pick already carries the contact
    "research_llm":       "🔎 Nothing matched from that list — switching to open web recon.",
    "research_retry":     "First pass came up dry — I'm broadening the search and trying again.",
    "research_done":      "⚙️ Now building them a working prototype tailored to their setup. About 90 seconds.",
    "engineer":           "",
    "engineer_fallback":  "",  # silent — the deploy line below tells the story
    "engineer_done":      "🕸 Prototype deployed.\n\n🎬 Rolling film — an AI voice narrating a Loom-style walkthrough. Another 90 seconds.",
    "director":           "",
    "director_done":      "🎬 Walkthrough done.",
    "embed_media":        "🔗 Embedding the video + AI rep into the prototype…",
    "sales":              "✍️ Last leg — assembling the pitch deck and drafting the email. Almost there.",
    "sales_done":         "All set. Sending your bundle over now.",
    "done":               "",
    "failed":             "",
}


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

        # Self-check: validate the token, kill any stale webhook (long-poll
        # is dead-on-arrival if a webhook is set), and print the bot's real
        # @username so the founder knows exactly which chat to open.
        me = self.api.get_me()
        if not me.get("ok"):
            desc = (me.get("description") or me.get("error") or "unknown")[:200]
            print(f"[telegram] ❌ getMe failed — {desc}. "
                  "Check TELEGRAM_BOT_TOKEN in ~/Revenant.AI/.env (or "
                  "~/.hermes/.env). Nothing to do until the token is valid.")
            return
        info = me.get("result", {})
        username = info.get("username", "revenant")
        first = info.get("first_name", "Revenant")

        wh = self.api.delete_webhook(drop_pending=False)
        if not wh.get("ok"):
            print(f"[telegram] warning: deleteWebhook — {wh.get('description') or wh.get('error')}")

        print(f"[telegram] ✅ Connected as {first} (@{username}) — "
              f"open https://t.me/{username} in Telegram to start.")
        if self.allowed is not None:
            print(f"[telegram]   Locked to chat_id {self.allowed} — "
                  "messages from any other chat will be politely rejected.")
        else:
            print("[telegram]   No chat-id lock — will accept any /start.")

        offset: int | None = None
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
        # Before setup we don't know their company — the answer must feel
        # like an agency's first meeting, not a fake authority.
        if not sess.setup_done:
            return self._first_meeting_reply(chat_id, text)
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

    def _first_meeting_reply(self, chat_id: int, text: str) -> None:
        """Answer as a new agency in its first meeting — no fake authority
        about the founder's product until they configure a startup."""
        from ghost.llm import complete

        reply = complete(
            f"The founder just sent their first message: {text!r}\n\n"
            "Respond in 1-3 short sentences, plain text, warmly. You do NOT "
            "know what their startup does yet — you have not been configured. "
            "If the question is about their product/company, honestly say you "
            "haven't been briefed yet and ask them to share the GitHub repo "
            "or product URL. If it's a greeting or 'what can you do', "
            "introduce yourself in one line and ask the same. Never invent "
            "any product facts.",
            agent="telegram.first",
            system=("You are Revenant, an autonomous outbound-engineering "
                    "agency. This is the founder's first message — treat it "
                    "like the first meeting: warm, curious, and honest about "
                    "what you don't yet know. You will help find prospects, "
                    "build tailored prototypes, film walkthroughs, and draft "
                    "outreach — but only after you've read their startup."),
            offline="Hi — I'm Revenant, your outbound engineer. To help I need "
                    "to know your startup: send a GitHub repo or product URL.",
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
        sess.setup_done = True
        one_liner = briefing.strip().splitlines()
        # pull the first non-header, non-empty line as the human summary
        gist = next((ln.strip("*- #") for ln in one_liner
                     if ln.strip() and not ln.strip().startswith("#")), "")
        if mid:
            self.api.edit_message(
                chat_id, mid,
                f"Got it. Read {len(ctx.files)} files from "
                f"<code>{html.escape(source)}</code>.\n\n"
                f"<i>{html.escape(gist[:280])}</i>\n\n"
                "I'll sell on their behalf from here. When you're ready, "
                "tell me who to go after — e.g. "
                "<i>“find a US healthtech startup”</i>.")

    def _on_command(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("start", "help"):
            from ..hermes_link import detect as _hd
            h = _hd()
            hermes_tag = (f"<i>Powered by Hermes Agent v{h.version} · "
                          f"{h.model or 'Hermes-4-405B'}</i>\n\n"
                          if h.installed else "")
            self.api.send_message(chat_id, hermes_tag + _WELCOME.format(
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
        """Stage 1: surface a shortlist of 3 verified prospects and wait
        for the founder to pick. Engineer / Director / Sales only run after
        the founder taps one of the buttons — see ``_build_for``."""
        sess = self.session(chat_id)
        ctx = sess.ctx or self.ctx
        if ctx is None or not sess.setup_done:
            self._needs_setup(chat_id, before_hunt=True)
            return

        sess.mode = "running"
        sess.last_brief = brief
        sess.shortlist = []
        sess.shortlist_msg_id = None

        def on_stage(stage: str, detail: str) -> None:
            template = _STAGE_NARR.get(stage, "")
            if not template:
                return
            msg = template.format(a=html.escape(detail)) if "{a}" in template else template
            self.api.send_message(chat_id, msg, disable_preview=True)

        with _TypingLoop(self.api, chat_id):
            shortlist = find_shortlist(brief, ctx, on_stage=on_stage, want=3)

        if not shortlist:
            self.api.send_message(
                chat_id,
                "😕 Couldn't lock in a fit prospect with a real, addressable "
                "contact. Try a different vertical or looser signal — e.g. "
                "<i>“find any B2B SaaS handling sensitive customer data”</i>.")
            sess.mode = "idle"
            return

        sess.shortlist = shortlist
        sess.mode = "awaiting_pick"
        self._show_shortlist(chat_id, shortlist)

    def _show_shortlist(self, chat_id: int, shortlist: list[dict]) -> None:
        """Render the 3 candidates side-by-side with fit rationales + tap-to-build
        buttons. The founder picks; only then do we burn Engineer/Director/Sales."""
        lines = [
            "🕯 <b>Three verified fits</b> — pick the one to build for.\n"
            "Each has a real decision-maker + email on file.\n"
        ]
        buttons: list[list[tuple[str, str]]] = []
        for i, p in enumerate(shortlist):
            company = p.get("company_name", "?")
            contact = p.get("contact") or {}
            person = contact.get("name") or "—"
            title = contact.get("title") or ""
            email = ""
            emails = contact.get("email_candidates") or []
            if emails:
                email = emails[0]
            rationale = (p.get("fit_rationale") or "").strip()
            # trim a 2-sentence rationale to a Telegram-safe length
            if len(rationale) > 360:
                rationale = rationale[:357] + "…"
            emp = p.get("employees") or ""
            emp_s = f"{emp} emp · " if emp else ""
            lines.append(
                f"<b>{i+1}. {html.escape(company)}</b>  "
                f"<i>({emp_s}{html.escape(str(p.get('industry','')))})</i>\n"
                f"👤 {html.escape(person)}"
                + (f" — {html.escape(title)}" if title else "")
                + (f"\n📧 <code>{html.escape(email)}</code>" if email else "")
                + f"\n<i>Why they fit:</i> {html.escape(rationale)}\n"
            )
            buttons.append([(f"🎯 Build for {i+1}. {company[:22]}",
                             f"pick:{i}")])
        buttons.append([("♻️ Try a different brief", "pick:cancel")])
        body = "\n".join(lines) + (
            "\nTap one and I'll build them a working prototype + AI walkthrough "
            "+ pitch deck. About 3 minutes."
        )
        m = self.api.send_message(chat_id, body,
                                   reply_markup=inline_keyboard(buttons),
                                   disable_preview=True)
        self.session(chat_id).shortlist_msg_id = (
            m.get("result", {}).get("message_id"))

    def _build_for(self, chat_id: int, index: int) -> None:
        """Stage 2: run Engineer → Director → Sales for the picked prospect."""
        sess = self.session(chat_id)
        if not sess.shortlist or index < 0 or index >= len(sess.shortlist):
            self.api.send_message(chat_id, "That pick expired — start a new hunt.")
            sess.mode = "idle"
            return
        picked = sess.shortlist[index]
        ctx = sess.ctx or self.ctx
        sess.mode = "running"

        def on_stage(stage: str, detail: str) -> None:
            template = _STAGE_NARR.get(stage, "")
            if not template:
                return
            msg = template.format(a=html.escape(detail)) if "{a}" in template else template
            self.api.send_message(chat_id, msg, disable_preview=True)

        with _TypingLoop(self.api, chat_id):
            art = build_campaign_for(picked, ctx, on_stage=on_stage,
                                      skip_lipsync=self.skip_lipsync)

        if not art.ok:
            self.api.send_message(
                chat_id,
                f"😕 {html.escape(art.error or 'build failed')}")
            sess.mode = "idle"
            return

        sess.art = art
        self._deliver(chat_id, art)
        sess.mode = "idle"

    def _needs_setup(self, chat_id: int, *, before_hunt: bool = False) -> None:
        prefix = ("Before I hunt anything, I need to know who I'm selling for. "
                  if before_hunt else "")
        self.api.send_message(
            chat_id,
            prefix +
            "Point me at your startup — send a GitHub repo or a link "
            "to your product site and I'll ingest the whole thing:\n\n"
            "<code>/setup github.com/you/your-startup</code>\n"
            "or just paste the URL by itself.\n\n"
            "Give me ~20 seconds after that and I'll brief myself.",
            disable_preview=True)

    # ── deliver artifacts ─────────────────────────────────────
    def _deliver(self, chat_id: int, art: CampaignArtifacts) -> None:
        # 1. Walkthrough — upload the file so it plays inline in Telegram
        cap = f"🎬 AI walkthrough — built for {html.escape(art.company)}"
        video_sent = False
        if art.walkthrough_mp4 and os.path.exists(art.walkthrough_mp4):
            self.api.send_chat_action(chat_id, "upload_video")
            res = self.api.send_video(chat_id, art.walkthrough_mp4, caption=cap)
            video_sent = bool(res.get("ok"))
        # only fall back to a link if the upload actually failed AND we have a
        # public URL (never expose a file:// URL to the founder in chat)
        if not video_sent and art.walkthrough_url and not art.walkthrough_url.startswith("file:"):
            self.api.send_message(chat_id, f"{cap}\n{art.walkthrough_url}")

        # 2. Live prototype — link preview shows a rich card
        if art.prototype_url and not art.prototype_url.startswith("file:"):
            self.api.send_message(
                chat_id,
                f"🕸 <b>Live prototype</b> — built for {html.escape(art.company)}\n"
                f"{art.prototype_url}")
        elif art.prototype_url:
            # deploy fell back to file:// — say so honestly, don't paste a
            # useless local path
            self.api.send_message(
                chat_id,
                "⚠️ Prototype deploy failed — Cloudflare Pages didn't accept "
                "the upload. The local copy is on the laptop; open it from "
                "there for review.")

        # 3. Pitch deck — upload the .pptx so Telegram shows the icon
        if art.deck_pptx and os.path.exists(art.deck_pptx):
            self.api.send_chat_action(chat_id, "upload_document")
            self.api.send_document(chat_id, art.deck_pptx,
                                   caption=f"📊 Pitch deck for {html.escape(art.company)}")
        elif art.deck_url and not art.deck_url.startswith("file:"):
            self.api.send_message(chat_id, f"📊 <b>Pitch deck</b>\n{art.deck_url}")

        # 4. Email draft + inline buttons
        self._send_draft(chat_id, art)

    def _send_draft(self, chat_id: int, art: CampaignArtifacts) -> None:
        to = art.recipient_email or "not-yet-verified"
        who = art.contact_name or "team"
        body = (
            "✉️ <b>Here's the draft I'd send.</b> Your call.\n\n"
            f"<b>To:</b> {html.escape(who)} "
            f"&lt;{html.escape(to)}&gt;\n"
            f"<b>Subject:</b> {html.escape(art.email_subject)}\n\n"
            "<i>─────────────────</i>\n"
            f"{html.escape(art.email_body)}\n"
            "<i>─────────────────</i>\n\n"
            "Approve and I'll drop it in your Gmail drafts with the deck + "
            "video attached — you send when you're ready. Or amend it in "
            "plain English and I'll rework."
        )
        cid = art.campaign_id
        kb = inline_keyboard([
            [("✅ Approve", f"approve:{cid}"), ("✏️ Amend", f"amend:{cid}")],
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

        if action == "pick":
            payload = data.split(":", 1)[1] if ":" in data else ""
            if payload == "cancel":
                self.api.answer_callback(cb_id, "Cancelled")
                if sess.shortlist_msg_id:
                    self.api.edit_message(
                        chat_id, sess.shortlist_msg_id,
                        "♻️ <i>Shortlist dismissed — send a new brief when ready.</i>",
                        reply_markup={})
                sess.mode = "idle"
                sess.shortlist = []
                return
            try:
                idx = int(payload)
            except ValueError:
                self.api.answer_callback(cb_id, "Bad pick")
                return
            self.api.answer_callback(cb_id, f"Building for {idx+1}…")
            if sess.shortlist_msg_id and 0 <= idx < len(sess.shortlist):
                picked_name = sess.shortlist[idx].get("company_name", "?")
                self.api.edit_message(
                    chat_id, sess.shortlist_msg_id,
                    f"✅ <b>Locked in: {html.escape(picked_name)}</b> — "
                    "building the prototype + walkthrough + deck now.",
                    reply_markup={})
            self._build_for(chat_id, idx)
            return

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

def _me(api: TelegramAPI) -> str:
    try:
        import httpx
        r = httpx.get(api._base + "/getMe", timeout=10).json()
        return r.get("result", {}).get("username", "revenant")
    except Exception:
        return "revenant"


_WELCOME = """Hey {founder} 👋 I'm Revenant — think of me as the outbound
agency that never sleeps.

I hunt a fit prospect, build them a working prototype, film an AI
walkthrough, and draft the outreach — all before you finish your coffee.

<b>Before we start</b>, tell me a little about the company I'm selling for.
The quickest way is to point me at a GitHub repo or a product URL:

<code>/setup github.com/you/your-startup</code>

or just paste the link on its own. I'll read the docs + code (~20 s), brief
myself, and then you can just say <i>"go find a customer in healthtech"</i> —
I'll take it from there.

Approved drafts land in your Gmail with the deck and walkthrough attached.
Nothing sends without your tap."""
