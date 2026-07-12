#!/usr/bin/env python3
"""Hermes skill entry point — run the full outbound chain, deliver artifacts.

This is the script the ``revenant-outbound`` Hermes skill shells out to. It
makes **Hermes the single front door**: the founder (or a judge testing the
bot) talks to Hermes (desktop app OR the Hermes-owned Telegram gateway),
Hermes invokes this script, and they see:

  1. A rich markdown brief printed to stdout — Hermes relays it into the
     chat, so the conversation lives inside Hermes.
  2. The real artifacts (walkthrough MP4, pitch deck) delivered as native
     attachments. We emit ``MEDIA:<absolute-path>`` tags in the brief;
     Hermes' gateway extracts them and delivers the files **to whoever
     triggered the run** — no hardcoded chat id, so any tester gets their
     own artifacts back. In the desktop app the same URLs are clickable too.

No standalone Revenant server. No competing bot token. Hermes owns the bot;
Revenant is the skill it runs.

Usage:
    python scripts/hermes_run.py "<the founder's ask, verbatim>"

Environment:
    REVENANT_REPO             Founder startup folder/URL (default ~/shroud)
    DIRECTOR_SKIP_LIPSYNC=1   Skip D-ID lip-sync (saves credits; default on here)
    REVENANT_TELEGRAM_TARGET  Optional explicit `hermes send` target
                              (e.g. telegram:123456789) for CLI testing
                              OUTSIDE a running gateway. Leave unset when
                              invoked as a Hermes skill — the MEDIA: tags
                              handle delivery to the current chat.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _stage_line(name: str, detail: str = "") -> None:
    """Human progress to stderr so Hermes' live tool output shows motion
    without polluting the stdout brief it relays verbatim."""
    msg = f"  → {name}" + (f": {detail}" if detail else "")
    print(msg, file=sys.stderr, flush=True)


def _resolve_target() -> str:
    """Which chat should the worker deliver to? Hermes exports the triggering
    chat as HERMES_SESSION_PLATFORM + HERMES_SESSION_CHAT_ID to the shell tool,
    so we reply to WHOEVER asked (any tester, dynamic). Fall back to an
    explicit override, then the Telegram home channel."""
    plat = os.getenv("HERMES_SESSION_PLATFORM", "").strip()
    chat = os.getenv("HERMES_SESSION_CHAT_ID", "").strip()
    if plat and chat:
        thread = os.getenv("HERMES_SESSION_THREAD_ID", "").strip()
        return f"{plat}:{chat}:{thread}" if thread else f"{plat}:{chat}"
    if os.getenv("REVENANT_TELEGRAM_TARGET"):
        return os.getenv("REVENANT_TELEGRAM_TARGET").strip()
    return "telegram"  # home channel (TELEGRAM_HOME_CHANNEL)


def _hermes_send(target: str, *, text: str = "", media: str = "",
                 caption: str = "") -> bool:
    """Deliver a message or media attachment to a chat via ``hermes send``.
    Reuses the gateway's own credentials — works with just the bot token, no
    running gateway required for Telegram. Never raises."""
    cmd = ["hermes", "send", "--to", target, "--quiet"]
    if media:
        if caption:
            cmd += ["--subject", caption[:180]]
        cmd.append(f"MEDIA:{media}")
    else:
        cmd.append(text)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print(f"  ! hermes send failed ({r.returncode}): "
                  f"{(r.stderr or r.stdout)[:200]}", file=sys.stderr)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        print(f"  ! hermes send unavailable: {exc}", file=sys.stderr)
        return False


SHORTLIST_PATH = Path.home() / ".revenant" / "last_shortlist.json"
CAMPAIGN_PATH = Path.home() / ".revenant" / "last_campaign.json"


def _chat_id_from_target(target: str) -> str:
    """Extract the raw chat id from a `platform:chat[:thread]` target, or the
    Telegram home channel when target is just 'telegram'."""
    if ":" in target:
        return target.split(":")[1]
    return os.getenv("TELEGRAM_HOME_CHANNEL", "").strip()


def _tg_send(target: str, html_text: str, *, buttons: list[list[str]] | None = None) -> bool:
    """Send a message straight through the Telegram Bot API (bypassing Hermes)
    so we control parse_mode=HTML AND can attach a tap-to-pick reply keyboard.
    Reply-keyboard buttons send their label as a normal message when tapped —
    which flows back through the Hermes gateway and routes via SOUL.md. Falls
    back to `hermes send` (plain) if the token/chat can't be resolved."""
    import httpx
    from ghost.config import settings
    token = settings.telegram_bot_token
    chat_id = _chat_id_from_target(target)
    if not token or not chat_id:
        return _hermes_send(target, text=_strip_html(html_text))
    body: dict = {"chat_id": chat_id, "text": html_text[:4096],
                  "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": True}}
    if buttons:
        body["reply_markup"] = {
            "keyboard": [[{"text": b} for b in row] for row in buttons],
            "resize_keyboard": True, "one_time_keyboard": True,
            "input_field_placeholder": "Tap an option or type…",
        }
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json=body, timeout=30)
        if r.status_code != 200:
            print(f"  ! tg send {r.status_code}: {r.text[:200]}", file=sys.stderr)
            return _hermes_send(target, text=_strip_html(html_text))
        return True
    except httpx.HTTPError as exc:
        print(f"  ! tg send error: {exc}", file=sys.stderr)
        return _hermes_send(target, text=_strip_html(html_text))


def _strip_html(s: str) -> str:
    import re as _re
    return _re.sub(r"</?[a-z][^>]*>", "", s)


def main() -> None:
    """Launcher (fast path). Hermes calls this; it returns in <2s so the
    agent's tool call never times out. It dispatches on flags and spawns a
    DETACHED worker for the slow work, then prints a clean one-line ack.

    Three phases (SOUL.md routes to each):
      (default)    find a customer → shortlist worker → 3 options to pick from
      --build <n>  the founder picked → build worker → full campaign
      --send <em>  the founder approved → Gmail draft (fast, no fork)
    """
    if "--worker-shortlist" in sys.argv:
        return _worker_shortlist()
    if "--worker-build" in sys.argv:
        return _worker_build()
    if "--send" in sys.argv:
        return _do_send()

    if "--build" in sys.argv:
        choice = _arg_after("--build") or "1"
        _spawn_worker("--worker-build", choice)
        print(f"⚙️ Locked in **#{choice}** — building the prototype, "
              "walkthrough, deck, and email now. I'll drop the full campaign "
              "here in about 3–4 minutes.")
        return

    # Default: a fresh hunt → shortlist phase.
    ask = _extract_ask(sys.argv)
    _spawn_worker("--worker-shortlist", ask)
    print("🔎 On it — hunting verified prospects for you. I'll show you a "
          "few fits to choose from in under a minute.")


def _arg_after(flag: str) -> str:
    try:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
    except ValueError:
        return ""


def _spawn_worker(mode: str, arg: str) -> None:
    """Spawn a detached worker that survives our exit + the agent turn."""
    target = _resolve_target()
    log_path = Path(os.path.expanduser("~/.revenant")) / "last_run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "w")
    env = dict(os.environ)
    env["REVENANT_DELIVER_TARGET"] = target
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), mode, arg],
        stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
        start_new_session=True, env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )


def _extract_ask(argv) -> str:
    parts = [a for a in argv[1:] if not a.startswith("--")]
    return (parts[0] if parts else
            "Find one US healthtech prospect and run the whole outbound chain.")


def _load_ctx():
    """Ingest the founder's startup (from /setup pointer, else ~/shroud)."""
    from agents.context import FounderContext
    repo = os.getenv("REVENANT_REPO")
    if not repo:
        try:
            import json as _json
            if (Path.home() / ".revenant" / "active_context.json").exists():
                repo = _json.loads(
                    (Path.home() / ".revenant" / "active_context.json").read_text()
                ).get("source")
        except Exception:
            repo = None
    repo = os.path.expanduser(repo or "~/shroud")
    _stage_line("ingesting founder context", repo)
    if repo.startswith(("http://", "https://", "git@")) or (
            "/" in repo and not repo.startswith(("~", "/", "."))):
        return FounderContext.from_github(repo), repo
    return FounderContext.from_folder(repo), repo


# ── Phase 1: shortlist (present 3 verified fits to choose from) ────
def _worker_shortlist() -> None:
    os.environ.setdefault("REVENANT_MODE", "live")
    ask = _extract_ask(sys.argv)
    target = os.getenv("REVENANT_DELIVER_TARGET") or _resolve_target()

    # "search again" carries no vertical — reuse the last brief.
    import json as _json
    if "search again" in ask.lower() and SHORTLIST_PATH.exists():
        try:
            ask = _json.loads(SHORTLIST_PATH.read_text()).get("ask") or ask
        except Exception:
            pass

    from ghost.config import get_settings
    get_settings.cache_clear()
    from agents.runner import find_shortlist

    ctx, repo = _load_ctx()
    shortlist = find_shortlist(ask, ctx, on_stage=_stage_line, want=3)
    if not shortlist:
        _hermes_send(target, text=(
            "I couldn't lock in verified prospects with a real, addressable "
            "contact for that brief. Try a different vertical or a looser "
            "signal — e.g. \"any B2B SaaS handling sensitive customer data\"."))
        return

    # Persist so the build phase can reuse the exact prospects (no re-search).
    SHORTLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHORTLIST_PATH.write_text(_json.dumps({"ask": ask, "repo": repo,
                                           "prospects": shortlist}),
                              encoding="utf-8")

    n = len(shortlist)
    buttons = [[f"Build {i}" for i in range(1, n + 1)], ["Search again"]]
    _tg_send(target, _render_shortlist(shortlist), buttons=buttons)


def _esc(s: str) -> str:
    import html as _html
    return _html.escape(str(s or ""))


def _render_shortlist(shortlist) -> str:
    lines = ["🎯 <b>I found 3 verified fits</b> — each has a real "
             "decision-maker and a reachable email. Here's why each fits:\n"]
    for i, p in enumerate(shortlist, 1):
        c = p.get("contact") or {}
        person = _esc(c.get("name") or "—")
        title = _esc(c.get("title") or "")
        emails = c.get("email_candidates") or []
        email = _esc(emails[0]) if emails else ""
        rationale = _esc((p.get("fit_rationale") or "").strip())
        lines.append(
            f"<b>{i}. {_esc(p.get('company_name','?'))}</b>\n"
            f"👤 {person}" + (f" · {title}" if title else "")
            + (f"\n📧 <code>{email}</code>" if email else "")
            + (f"\n💡 {rationale}" if rationale else "") + "\n")
    lines.append("👇 <b>Tap the one to build</b> (or type “build 2”). "
                 "I'll build it the full campaign — prototype, AI walkthrough, "
                 "deck, and a drafted email.")
    return "\n".join(lines)


# ── Phase 2: build the picked prospect ────────────────────────────
def _worker_build() -> None:
    os.environ.setdefault("REVENANT_MODE", "live")
    os.environ.setdefault("DIRECTOR_SKIP_LIPSYNC", "1")
    choice = _extract_ask(sys.argv)
    target = os.getenv("REVENANT_DELIVER_TARGET") or _resolve_target()

    import json as _json
    if not SHORTLIST_PATH.exists():
        _hermes_send(target, text=("I don't have a shortlist to build from — "
                     "ask me to \"find a customer\" first."))
        return
    saved = _json.loads(SHORTLIST_PATH.read_text())
    prospects = saved.get("prospects") or []
    picked = _resolve_choice(choice, prospects)
    if picked is None:
        _hermes_send(target, text=(f"I couldn't match \"{choice}\" to the "
                     "shortlist. Reply build 1, build 2, or build 3."))
        return

    from ghost.config import get_settings
    get_settings.cache_clear()
    from agents.runner import build_campaign_for

    # Re-ingest the same startup the shortlist used.
    if saved.get("repo"):
        os.environ["REVENANT_REPO"] = saved["repo"]
    ctx, _repo = _load_ctx()

    def notify(text: str) -> None:
        _tg_send(target, text)

    def on_stage(stage: str, detail: str = "") -> None:
        _stage_line(stage, detail)
        pings = {
            "engineer":      f"⚙️ Building a live prototype for {_esc(detail) or 'them'}…",
            "engineer_done": "🕸 Prototype deployed. Rolling the walkthrough film…",
            "director_done": "🎬 Walkthrough done. Assembling the deck + email…",
        }
        if stage in pings:
            notify(pings[stage])

    notify(f"⚙️ Building the campaign for <b>{_esc(picked.get('company_name','your pick'))}</b>…")
    art = build_campaign_for(picked, ctx, on_stage=on_stage, skip_lipsync=True)
    if not art.ok:
        notify(f"The build failed: {_esc(art.error or 'unknown error')}. Ask me to try again.")
        return

    # Persist campaign so "send it to <email>" can find it.
    CAMPAIGN_PATH.write_text(_json.dumps({
        "company": art.company, "campaign_id": art.campaign_id,
        "recipient_email": art.recipient_email,
        "contact_name": art.contact_name,
        "email_subject": art.email_subject, "email_body": art.email_body,
        "prototype_url": art.prototype_url, "walkthrough_url": art.walkthrough_url,
        "deck_url": art.deck_url, "deck_pptx": art.deck_pptx,
        "walkthrough_mp4": art.walkthrough_mp4,
    }), encoding="utf-8")

    if art.walkthrough_mp4 and Path(art.walkthrough_mp4).exists():
        _hermes_send(target, media=str(Path(art.walkthrough_mp4).resolve()),
                     caption=f"🎬 AI walkthrough — built for {art.company}")
    if art.deck_pptx and Path(art.deck_pptx).exists():
        _hermes_send(target, media=str(Path(art.deck_pptx).resolve()),
                     caption=f"📊 Pitch deck — {art.company}")
    # Brief + tap-to-act buttons. "Approve & send" → SOUL Route 3 (uses the
    # persisted recipient). "Build 2/3" → re-pick from the same shortlist.
    brief_buttons = [["✅ Approve & send", "✏️ Tweak the email"],
                     ["Build 2 instead", "Build 3 instead"]]
    _tg_send(target, _render_brief(art), buttons=brief_buttons)


def _resolve_choice(choice: str, prospects: list):
    """Map 'build 1' / '2' / 'Plaid' / 'the first one' → a prospect dict."""
    c = (choice or "").strip().lower()
    import re as _re
    m = _re.search(r"\b([123])\b", c)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(prospects):
            return prospects[idx]
    for word, idx in (("first", 0), ("second", 1), ("third", 2), ("last", -1)):
        if word in c and abs(idx) < len(prospects):
            return prospects[idx]
    for p in prospects:  # company-name match
        name = (p.get("company_name") or "").lower()
        if name and name in c:
            return p
    return prospects[0] if prospects else None


# ── Phase 3: approve → Gmail draft ────────────────────────────────
def _do_send() -> None:
    os.environ.setdefault("REVENANT_MODE", "live")
    recipient = _arg_after("--send").strip()
    target = _resolve_target()
    import json as _json
    if not CAMPAIGN_PATH.exists():
        print("There's no drafted campaign to send yet. Build one first with "
              "\"find a customer\" → \"build 1\".")
        return
    camp = _json.loads(CAMPAIGN_PATH.read_text())
    recipient = recipient or camp.get("recipient_email", "")
    if not recipient:
        print("Reply with the recipient email, e.g. \"send it to dzhou@plaid.com\".")
        return

    from ghost.config import get_settings
    get_settings.cache_clear()
    from agents.sales import gmail_draft
    if not gmail_draft.configured():
        print("⚠️ Gmail isn't authorized yet. Run `revenant gmail-auth` on the "
              "laptop once, then approve again.")
        return

    body = camp.get("email_body", "")
    links = [f"Walkthrough: {camp.get('walkthrough_url','')}",
             f"Prototype: {camp.get('prototype_url','')}",
             f"Deck: {camp.get('deck_url','')}"]
    links = [l for l in links if l.split(": ", 1)[1]]
    if links and not all(l.split(": ", 1)[1] in body for l in links):
        body += "\n\n" + "\n".join(links)

    result = gmail_draft.create_draft(
        to_email=recipient, subject=camp.get("email_subject", ""), body=body,
        attachments=[p for p in (camp.get("deck_pptx"), camp.get("walkthrough_mp4"))
                     if p and Path(p).exists()])
    if not result.get("ok"):
        print("⚠️ " + str(result.get("error", "draft failed")))
        return
    attached = ", ".join(result.get("attached") or []) or "links only"
    print(f"📥 **Saved to your Gmail drafts** — to `{recipient}`\n"
          f"📎 attached: {attached}\n"
          f"Open Gmail → Drafts, give it a final look, hit send when ready.\n"
          f"{result.get('gmail_url','')}")


def _render_brief(art, runners_up=None, *, media_paths=None) -> str:
    """HTML (Telegram parse_mode=HTML) — sent via _tg_send with buttons."""
    contact = art.prospect.get("contact") or {}
    person = _esc(contact.get("name") or "—")
    title = _esc(contact.get("title") or "")
    who = person + (f" · {title}" if title else "")

    lines = [f"🎯 <b>{_esc(art.company)}</b>", "",
             f"<b>Decision-maker:</b> {who}"]
    if art.recipient_email:
        lines.append(f"<b>Email:</b> <code>{_esc(art.recipient_email)}</code>")
    if art.fit_rationale:
        lines += ["", f"<b>Why they fit:</b> {_esc(art.fit_rationale)}"]

    lines += ["", "<b>What I built them</b>"]
    if art.walkthrough_url and not art.walkthrough_url.startswith("file:"):
        lines.append(f"🎬 <a href=\"{_esc(art.walkthrough_url)}\">Walkthrough (AI voice)</a>")
    if art.prototype_url and not art.prototype_url.startswith("file:"):
        lines.append(f"🕸 <a href=\"{_esc(art.prototype_url)}\">Live prototype</a>")
    if art.deck_url and not art.deck_url.startswith("file:"):
        lines.append(f"📊 <a href=\"{_esc(art.deck_url)}\">Pitch deck</a>")

    lines += ["", "<b>The email I drafted</b>",
              f"<b>Subject:</b> {_esc(art.email_subject)}", "",
              _esc(art.email_body.strip())]

    if art.warnings:
        lines += ["", "⚠️ <i>" + _esc(" · ".join(art.warnings[:3])) + "</i>"]

    lines += ["", "──────────", "<b>👇 Your move — tap below</b>",
              "• ✅ <b>Approve &amp; send</b> → I'll draft it into your Gmail "
              "(never auto-sends).",
              "• ✏️ <b>Tweak the email</b> → tell me what to change.",
              "• ♻️ <b>Build 2 / 3 instead</b> → pick a different target."]

    return "\n".join(lines)


if __name__ == "__main__":
    main()
