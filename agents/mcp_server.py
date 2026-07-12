#!/usr/bin/env python3
"""Revenant AI as an MCP server — the Hermes front door (v2 integration).

Exposes Revenant's outbound pipeline as Model Context Protocol tools so Hermes
(or any MCP host) drives it with structured, typed tool calls instead of the
brittle shell-skill / SOUL.md-routing approach that failed before. The host LLM
picks the tool; we return structured results — no natural-language route
guessing, no context loss between "find" and "build", no tool-timeout
hallucination (the host waits on the tool and gets the real artifacts).

Tools
-----
  setup_startup(sources)   onboard the founder's startup (github / site / folder,
                           one or many) — persists the active-context pointer.
  find_prospects(brief)    verified shortlist (real decision-maker + addressable
                           email + a specific fit rationale) for a vertical/ICP.
  build_campaign(choice)   Engineer→Director→Sales for one picked prospect:
                           live prototype + AI walkthrough video + pitch deck +
                           drafted email. Returns artifact URLs + MEDIA: lines.
  draft_email(to_email)    create a Gmail draft from the built campaign.
  status()                 what's currently loaded (context / shortlist / campaign).

State (shared with the standalone bot + hermes_run.py, so front-ends interop):
  ~/.revenant/active_context.json   which startup we sell for
  ~/.revenant/last_shortlist.json   the last find_prospects result
  ~/.revenant/last_campaign.json    the last build_campaign result

Run (stdio):  <venv-python> agents/mcp_server.py
Register:     hermes mcp add revenant --command <venv-python> --args <abs path>
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Bootstrap: make the repo importable whether launched as `-m agents.mcp_server`
# or by absolute path (Hermes spawns us with its own cwd). ────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Everything downstream assumes live mode (real Apollo / deploys / Gmail).
os.environ.setdefault("REVENANT_MODE", "live")

from mcp.server.fastmcp import Context, FastMCP  # noqa: E402

REV = Path.home() / ".revenant"
ACTIVE_CTX_PATH = REV / "active_context.json"
SHORTLIST_PATH = REV / "last_shortlist.json"
CAMPAIGN_PATH = REV / "last_campaign.json"

mcp = FastMCP(
    "revenant",
    instructions=(
        "Revenant is an autonomous outbound-sales engineer. Typical flow: "
        "setup_startup (once, to point it at the founder's company) → "
        "find_prospects (get a shortlist for a vertical) → build_campaign "
        "(pick one; produces a live prototype, an AI walkthrough video, a "
        "pitch deck, and a drafted email) → draft_email (save it to Gmail). "
        "When a tool result contains lines beginning with 'MEDIA:', relay them "
        "verbatim in your reply — the gateway turns them into file attachments "
        "for the user."
    ),
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _log_call(name: str, detail: str = "") -> None:
    """Append a timestamped line whenever a tool is invoked — diagnostics for
    confirming the host actually called us (vs. narrating intent)."""
    try:
        import time
        REV.mkdir(parents=True, exist_ok=True)
        with (REV / "mcp_calls.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {name}  {detail}\n")
    except Exception:
        pass


def _looks_like_repo(src: str) -> bool:
    return src.startswith(("http://", "https://", "git@")) or (
        "/" in src and not src.startswith(("~", "/", ".")))


@contextlib.contextmanager
def _quiet_stdout():
    """Redirect the pipeline's prints to stderr.

    MCP stdio speaks JSON-RPC over *stdout*; any stray print from the agents /
    ghost layer would corrupt the framing. The MCP transport captured the real
    stdout when the server started, so swapping ``sys.stdout`` here only affects
    the pipeline's own prints (which we route to stderr → visible in gateway
    logs, harmless to the protocol).
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _split_sources(raw: str) -> list[str]:
    """Parse one or many sources from a free-text arg.

    Accepts commas, whitespace, or 'and' between sources; tolerates the founder
    pasting a whole sentence ("set up github.com/x/y and acme.com").
    """
    import re
    txt = (raw or "").strip()
    # normalise separators, drop obvious filler words that aren't sources
    parts = re.split(r"[,\s]+|\band\b", txt)
    out: list[str] = []
    for p in parts:
        p = p.strip().rstrip("/").strip()
        if not p:
            continue
        if _looks_like_repo(p) or p.startswith(("~", "/", ".")):
            out.append(p)
    # if nothing looked like a source, fall back to the last token
    if not out and txt:
        out = [txt.split()[-1].rstrip("/")]
    return out


def _load_ctx():
    """Ingest the founder's startup (from the /setup pointer, else ~/shroud).

    Mirrors scripts/hermes_run.py:_load_ctx so the MCP path and the standalone
    bot resolve the same active context.
    """
    from agents.context import FounderContext
    repo = os.getenv("REVENANT_REPO")
    if not repo and ACTIVE_CTX_PATH.exists():
        try:
            repo = json.loads(ACTIVE_CTX_PATH.read_text()).get("source")
        except Exception:
            repo = None
    repo = os.path.expanduser(repo or "~/shroud")
    if repo.startswith(("http://", "https://", "git@")) or (
            "/" in repo and not repo.startswith(("~", "/", "."))):
        return FounderContext.from_github(repo), repo
    return FounderContext.from_folder(repo), repo


def _first_gist(briefing: str) -> str:
    for ln in briefing.splitlines():
        s = ln.strip("*-# ").strip()
        if s and not s.startswith("#"):
            return s
    return ""


def _prospect_line(i: int, p: dict[str, Any]) -> str:
    name = p.get("company_name", "?")
    contact = p.get("contact", {}) or {}
    who = contact.get("name", "")
    title = contact.get("title", "")
    emails = contact.get("email_candidates") or []
    email = emails[0] if emails else ""
    fit = p.get("fit_rationale", "")
    who_line = " · ".join(x for x in [who, title] if x)
    return (
        f"{i}. **{name}** ({p.get('company_domain','')})\n"
        f"   {who_line}{(' — ' + email) if email else ''}\n"
        f"   {fit}"
    )


def _resolve_choice(choice: str, prospects: list[dict]) -> dict | None:
    """Map 'build 1' / '2' / 'Plaid' / 'the first one' → a prospect dict."""
    import re
    c = (choice or "").strip().lower()
    m = re.search(r"\b([123])\b", c)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(prospects):
            return prospects[idx]
    for word, idx in (("first", 0), ("second", 1), ("third", 2), ("last", -1)):
        if word in c and abs(idx) < len(prospects):
            return prospects[idx]
    for p in prospects:
        name = (p.get("company_name") or "").lower()
        if name and name in c:
            return p
    return prospects[0] if prospects else None


# ── tools ─────────────────────────────────────────────────────────────────────
@mcp.tool()
def setup_startup(sources: str) -> str:
    """Onboard the founder's startup so every later campaign sells on its behalf.

    Call this once (or whenever the founder switches companies). Point it at any
    mix of a GitHub repo, a product/marketing website, a docs site, or a local
    folder — separate multiple with commas or spaces. Private repos that can't
    be read are skipped, not fatal, as long as at least one source is readable.

    Args:
        sources: One or more sources, e.g. "github.com/acme/api and acme.com".

    Returns a short confirmation of what the startup does.
    """
    _log_call("setup_startup", sources)
    from ghost.config import get_settings
    get_settings.cache_clear()
    from agents.context import FounderContext

    srcs = _split_sources(sources)
    if not srcs:
        return ("Point me at your startup: a GitHub repo, a website, or a local "
                "path — e.g. `github.com/you/your-startup` or `yourcompany.com`.")

    try:
        with _quiet_stdout():
            ctx = (FounderContext.from_sources(srcs) if len(srcs) > 1
                   else (FounderContext.from_github(srcs[0])
                         if _looks_like_repo(srcs[0])
                         else FounderContext.from_folder(os.path.expanduser(srcs[0]))))
            briefing = ctx.summary()
    except Exception as exc:  # noqa: BLE001
        return (f"I couldn't read {', '.join(srcs)} — {exc}. Double-check the "
                "repo is public (or the path exists) and try again.")

    report = getattr(ctx, "source_report", None)
    skipped = []
    if isinstance(report, dict):
        skipped = report.get("skipped") or []

    REV.mkdir(parents=True, exist_ok=True)
    ACTIVE_CTX_PATH.write_text(json.dumps({
        "source": srcs[0],
        "sources": srcs,
        "kind": "github" if _looks_like_repo(srcs[0]) else "folder",
        "product_name": getattr(ctx, "product_name", "") or "",
        "n_files": len(ctx.files),
    }), encoding="utf-8")

    gist = _first_gist(briefing)
    product = getattr(ctx, "product_name", "") or "your startup"
    msg = (f"✅ Locked onto **{product}** — read {len(ctx.files)} files from "
           f"{', '.join(srcs)}.")
    if skipped:
        msg += f"\n(Skipped, couldn't read: {', '.join(skipped)}.)"
    if gist:
        msg += f"\n\n_{gist[:280]}_"
    msg += ("\n\nWhen you're ready, say **find a <vertical> customer** and I'll "
            "build the whole campaign — verified prospect, live prototype, AI "
            "walkthrough video, pitch deck, and a drafted email.")
    return msg


@mcp.tool()
def find_prospects(brief: str, want: int = 3) -> str:
    """Find a shortlist of real, verified prospects for a vertical or ICP.

    Each prospect is Apollo-verified, has a named decision-maker with an
    addressable email, and carries a specific two-sentence fit rationale
    grounded in the founder's product. Nothing is built or sent yet — this just
    returns choices. Follow with build_campaign to act on one.

    Args:
        brief: What to hunt, e.g. "healthcare startups handling patient data"
               or "Series A fintech".
        want: How many prospects to return (default 3).

    Returns a numbered shortlist; the founder then picks one to build for.
    """
    _log_call("find_prospects", brief)
    from ghost.config import get_settings
    get_settings.cache_clear()
    from agents.runner import find_shortlist

    try:
        with _quiet_stdout():
            ctx, repo = _load_ctx()
            shortlist = find_shortlist(brief, ctx, want=max(1, min(int(want or 3), 5)))
    except Exception as exc:  # noqa: BLE001
        return f"Search failed: {exc}. Try a different vertical or a looser signal."

    if not shortlist:
        return ("I couldn't lock in verified prospects with a real, addressable "
                "contact for that brief. Try a different vertical or a looser "
                "signal — e.g. \"any B2B SaaS handling sensitive customer data\".")

    REV.mkdir(parents=True, exist_ok=True)
    SHORTLIST_PATH.write_text(
        json.dumps({"ask": brief, "repo": repo, "prospects": shortlist}),
        encoding="utf-8")

    lines = [_prospect_line(i, p) for i, p in enumerate(shortlist, 1)]
    return (
        f"Found {len(shortlist)} verified fit"
        f"{'s' if len(shortlist) != 1 else ''} for _{brief}_:\n\n"
        + "\n\n".join(lines)
        + "\n\nSay **build 1** (or 2 / 3, or the company name) and I'll produce "
          "the prototype, walkthrough video, deck, and email for that one."
    )


@mcp.tool()
def build_campaign(choice: str = "1") -> str:
    """Build the full campaign for one prospect from the last shortlist.

    Runs Engineer → Director → Sales: a live, deployed prototype tailored to the
    prospect, an AI-narrated walkthrough video, a pitch deck, and a drafted
    outreach email. Takes a few minutes. Requires a prior find_prospects call.

    Args:
        choice: Which prospect — "1"/"2"/"3", "the first one", or a company name.

    Returns the artifact URLs, the drafted email, and MEDIA: lines (walkthrough
    video + deck) for the gateway to attach. Relay MEDIA: lines verbatim.
    """
    _log_call("build_campaign", choice)
    if not SHORTLIST_PATH.exists():
        return ("I don't have a shortlist to build from — ask me to "
                "\"find a customer\" first.")

    from ghost.config import get_settings
    get_settings.cache_clear()
    from agents.runner import build_campaign_for

    saved = json.loads(SHORTLIST_PATH.read_text())
    prospects = saved.get("prospects") or []
    picked = _resolve_choice(choice, prospects)
    if picked is None:
        return (f"I couldn't match \"{choice}\" to the shortlist. "
                "Reply build 1, build 2, or build 3.")

    if saved.get("repo"):
        os.environ["REVENANT_REPO"] = saved["repo"]

    # D-ID credits are exhausted by default; skip lip-sync unless explicitly on.
    skip_lipsync = os.getenv("REVENANT_SKIP_LIPSYNC", "1") not in ("0", "false", "")

    try:
        with _quiet_stdout():
            ctx, _repo = _load_ctx()
            art = build_campaign_for(picked, ctx, skip_lipsync=skip_lipsync)
    except Exception as exc:  # noqa: BLE001
        return f"Build failed: {exc}"

    if not getattr(art, "ok", False):
        return f"Build failed: {getattr(art, 'error', 'unknown error')}"

    # Persist for draft_email / interop with the standalone bot.
    REV.mkdir(parents=True, exist_ok=True)
    CAMPAIGN_PATH.write_text(json.dumps({
        "company": art.company,
        "domain": art.domain,
        "recipient_email": art.recipient_email,
        "contact_name": art.contact_name,
        "prototype_url": art.prototype_url,
        "walkthrough_url": art.walkthrough_url,
        "walkthrough_mp4": art.walkthrough_mp4,
        "deck_url": art.deck_url,
        "deck_pptx": art.deck_pptx,
        "email_subject": art.email_subject,
        "email_body": art.email_body,
        "campaign_id": art.campaign_id,
    }), encoding="utf-8")

    parts = [f"🎯 **{art.company}** campaign is ready."]
    if art.contact_name:
        parts.append(f"Contact: {art.contact_name}"
                     + (f" <{art.recipient_email}>" if art.recipient_email else ""))
    links = []
    if art.prototype_url:
        links.append(f"• Prototype: {art.prototype_url}")
    if art.walkthrough_url:
        links.append(f"• Walkthrough: {art.walkthrough_url}")
    if art.deck_url:
        links.append(f"• Deck: {art.deck_url}")
    if links:
        parts.append("\n".join(links))
    if art.email_subject:
        parts.append(f"**Draft email — {art.email_subject}**\n\n{art.email_body}")
    if art.warnings:
        parts.append("_Notes: " + "; ".join(art.warnings) + "_")
    parts.append(f"_Cost this run: ${art.cost_usd:.2f}._")
    parts.append("Say **draft it** (optionally with a recipient email) to save "
                 "this as a Gmail draft with the deck + video attached.")

    # MEDIA lines — the Hermes gateway extracts these from the reply and
    # delivers them as native attachments to the current chat.
    media = []
    if art.walkthrough_mp4 and Path(art.walkthrough_mp4).exists():
        media.append(f"MEDIA:{art.walkthrough_mp4}")
    if art.deck_pptx and Path(art.deck_pptx).exists():
        media.append(f"MEDIA:{art.deck_pptx}")

    out = "\n\n".join(parts)
    if media:
        out += "\n\n" + "\n".join(media)
    return out


@mcp.tool()
async def build_prototype(startup: str, merchant: str, merchant_domain: str = "",
                          pain: str = "", startup_summary: str = "",
                          mcp_ctx: "Context | None" = None) -> str:
    """Build & DEPLOY a real, working prototype for ONE merchant, selling `startup`.

    Use this INSIDE an Engineer sub-agent once a merchant is chosen — or run
    several in parallel (one Engineer sub-agent per merchant) to build all the
    shortlisted merchants at once. Returns the LIVE Cloudflare Pages URL.
    Takes ~1-2 minutes. Unlike build_campaign, this takes the merchant EXPLICITLY
    (no prior shortlist needed), so a crew that researched from knowledge can build.

    Args:
        startup: the founder's company you sell FOR (e.g. "Razorpay").
        merchant: the target company to build the prototype for (e.g. "BigBasket").
        merchant_domain: the merchant's domain if known (e.g. "bigbasket.com").
        pain: one line on why this merchant fits / the pain the prototype addresses.
        startup_summary: what `startup` does — only needed for non-Razorpay startups.
    """
    _log_call("build_prototype", f"{startup} -> {merchant}")

    # Progress heartbeat helper — surface stage narration through MCP so the
    # caller (Hermes/console) has something to render instead of ~75s of dead
    # air. Best-effort: never break the build if progress delivery fails.
    async def _tick(msg: str) -> None:
        if mcp_ctx is None:
            return
        try:
            await mcp_ctx.info(msg)
        except Exception:
            pass

    await _tick(f"🎯 Building prototype for {merchant}…")

    from ghost.config import get_settings
    get_settings.cache_clear()

    # 1. Founder context — canned (instant) for Razorpay, else a minimal context.
    if "razorpay" in (startup or "").lower():
        from agents import demo_razorpay
        ctx = demo_razorpay.razorpay_context()
    else:
        from agents.context import FounderContext
        summary = startup_summary or f"{startup}. {pain}".strip()
        ctx = FounderContext(source=startup or "startup", root=Path("/tmp"),
                             files={"README.md": f"# {startup}\n\n{summary}"})
        try:
            ctx._summary_cache = summary
        except Exception:
            pass

    # 2. Prospect from the explicit args.
    dom = (merchant_domain or "").lower().replace("https://", "").replace("http://", "").strip("/")
    prospect = {
        "company_name": merchant,
        "company_domain": dom,
        "industry": "",
        "contact": {"name": "", "title": "", "email_candidates": [], "linkedin_url": ""},
        "pain_evidence": [{"source_url": f"https://{dom}" if dom else "", "excerpt": pain}],
        "fit_rationale": pain,
    }

    # 3. Build via the Engineer (already deploys to Cloudflare Pages as its
    #    finalize step — that's our sponsor URL). Optional VISION QA pass.
    #    With the planner+author architecture producing clean 22kB pages,
    #    polish usually returns +0 → opt-in via REVENANT_POLISH=1 to save ~9s.
    #    Ngrok stays as a fallback if CF is unreachable.
    import anyio
    try:
        import os as _os
        from agents.engineer import Engineer
        from agents.engineer.prototype import _harden_html
        from agents.engineer.cf_pages import deploy_dir
        import re as _re
        _run_polish = _os.getenv("REVENANT_POLISH", "0") not in ("", "0", "false", "no")

        # Attach a per-Engineer progress hook so we tick every time the
        # LLM finishes an internal step. Uses Engineer's `on_event` sink.
        _phase_names = {
            "read_prospect_brief":   "📎 Reading the prospect brief…",
            "read_founder_file":     "📚 Studying the founder's product…",
            "list_founder_files":    "📚 Studying the founder's product…",
            "search_founder_context":"📚 Studying the founder's product…",
            "write_prototype_file":  "🎨 Writing the tailored HTML…",
            "list_prototype_files":  "🗂  Reviewing the workspace…",
            "deploy_prototype":      "☁️  Deploying to Cloudflare Pages…",
            "finalize_prototype":    "✅ Finalising the build…",
        }
        _last_ticked_msg = {"v": ""}

        def _on_event(kind: str, payload: Any) -> None:
            # Called synchronously from the Engineer thread — schedule the
            # async tick without blocking. Deduplicate rapid repeats.
            if mcp_ctx is None or kind != "tool_call":
                return
            name = (payload or {}).get("name") or ""
            msg = _phase_names.get(name)
            if not msg or msg == _last_ticked_msg["v"]:
                return
            _last_ticked_msg["v"] = msg
            try:
                from anyio.from_thread import run_sync
                run_sync(lambda m=msg: mcp_ctx.info(m))
            except Exception:
                pass

        await _tick("🧠 Planning the prototype (senior-designer spec)…")

        def _do_build():
            with _quiet_stdout():
                eng = Engineer(founder_context=ctx, prospect=prospect)
                res = eng.build(on_event=_on_event)
                return eng, res

        eng, res = await anyio.to_thread.run_sync(_do_build)
        url = (res or {}).get("url", "")              # Engineer's own CF Pages URL
        ws = Path(res.get("workspace") or eng._state.workspace)
        idx = ws / "index.html"
        if idx.exists() and _run_polish:
            await _tick("🔍 Vision QA polish pass…")
            from agents.engineer import polish as _polish
            original = idx.read_text(encoding="utf-8")
            polished = await anyio.to_thread.run_sync(
                lambda: _polish.polish_html(original, startup=startup,
                                            merchant=merchant, passes=1))
            if polished and polished != original:
                improved = _harden_html(polished)
                idx.write_text(improved, encoding="utf-8")
                await _tick("☁️  Redeploying polished build to Cloudflare…")
                redeploy = await anyio.to_thread.run_sync(lambda: deploy_dir(ws))
                url = redeploy.get("url") or url
        if idx.exists() and (not url or url.startswith("file:")):
            await _tick("🌐 CF unavailable — falling back to local + ngrok…")
            slug = _re.sub(r"[^a-z0-9]+", "-", merchant.lower()).strip("-") or "merchant"
            try:
                from agents.engineer import local_host
                url = await anyio.to_thread.run_sync(
                    lambda: local_host.publish(slug, idx.read_text(encoding="utf-8")))
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        return f"Build failed for {merchant}: {exc}"

    if not url or url.startswith("file:"):
        return (f"Built a prototype for {merchant}, but the deploy didn't return a "
                f"public URL ({url or 'none'}).")
    return f"✅ {merchant}: live prototype deployed → {url}"


@mcp.tool()
async def film_walkthrough(prototype_url: str, merchant: str, startup: str = "Razorpay",
                           pain: str = "", startup_summary: str = "",
                           mcp_ctx: "Context | None" = None) -> str:
    """Film an AI walkthrough VIDEO of a deployed prototype (the Director agent).

    Use this INSIDE a **Director sub-agent**, after the Engineer has returned a
    live prototype URL. It records the page with a presenter narrating on-screen
    (Playwright + AI voice + avatar bottom-right), deploys the MP4, and returns
    the live video URL. Takes ~60-90s.

    Args:
        prototype_url: the LIVE prototype URL the Engineer just deployed.
        merchant: the target company the prototype was built for.
        startup: the founder's company being sold (default "Razorpay").
        pain: one line on the prospect's pain (helps the narration).
        startup_summary: what `startup` does — only for non-Razorpay startups.
    """
    _log_call("film_walkthrough", f"{startup} -> {merchant} @ {prototype_url}")

    async def _tick(msg: str) -> None:
        if mcp_ctx is None:
            return
        try:
            await mcp_ctx.info(msg)
        except Exception:
            pass

    if not prototype_url or prototype_url.startswith("file:"):
        return "I need a live prototype URL to film — build the prototype first."

    await _tick(f"🎬 Filming a walkthrough for {merchant}…")

    from ghost.config import get_settings
    get_settings.cache_clear()

    # Context + prospect (mirror build_prototype).
    if "razorpay" in (startup or "").lower():
        from agents import demo_razorpay
        ctx = demo_razorpay.razorpay_context()  # noqa: F841 (parity; Director uses prospect)
    prospect = {
        "company_name": merchant,
        "industry": "",
        "contact": {"name": "", "title": "", "email_candidates": [], "linkedin_url": None},
        "pain_evidence": [{"source_url": "", "excerpt": pain}] if pain else [],
        "fit_rationale": pain or "hand-supplied prospect",
    }

    import os as _os
    import anyio
    # Fast path for the console: skip the ~130s D-ID poll (Fiona fallback clip +
    # natural OpenAI voice instead) and skip the DEAD ElevenLabs key (401 on every
    # beat) so TTS goes straight to OpenAI. Mutate the shared settings singleton
    # the Director already holds. Set REVENANT_DIRECTOR_LIPSYNC=1 to force D-ID.
    from ghost.config import settings as _settings
    try:
        if _os.getenv("REVENANT_DIRECTOR_LIPSYNC", "0") in ("", "0", "false", "no"):
            _settings.skip_lipsync = True
        _settings.elevenlabs_api_key = None
    except Exception:
        pass

    _phase_names = {
        "read_prototype_url":     "🔗 Loading the prototype…",
        "read_prospect_context":  "📎 Reading the prospect context…",
        "render_walkthrough":     "🎥 Recording + narrating the walkthrough…",
        "finalize_walkthrough":   "☁️  Publishing the video…",
    }
    _last = {"v": ""}

    def _on_event(kind: str, payload: Any) -> None:
        if mcp_ctx is None or kind != "tool_call":
            return
        msg = _phase_names.get((payload or {}).get("name") or "")
        if not msg or msg == _last["v"]:
            return
        _last["v"] = msg
        try:
            from anyio.from_thread import run_sync
            run_sync(lambda m=msg: mcp_ctx.info(m))
        except Exception:
            pass

    try:
        from agents.director.agent import Director

        def _do_film():
            with _quiet_stdout():
                d = Director(prototype_url=prototype_url, prospect=prospect)
                return d.film(on_event=_on_event)

        res = await anyio.to_thread.run_sync(_do_film)
        url = (res or {}).get("video_url", "")
        # Fallback: host the local mp4 via ngrok if the deploy returned nothing.
        if (not url or url.startswith("file:")) and (res or {}).get("mp4_path"):
            mp4 = Path(res["mp4_path"])
            if mp4.exists():
                await _tick("🌐 CF unavailable — serving the video via ngrok…")
                import re as _re
                slug = "walkthrough-" + (_re.sub(r"[^a-z0-9]+", "-", merchant.lower()).strip("-") or "merchant")
                try:
                    from agents.engineer import local_host
                    # publish expects html; for a binary mp4 we just copy it in
                    d2 = local_host.PROTO_ROOT / slug
                    d2.mkdir(parents=True, exist_ok=True)
                    (d2 / "walkthrough.mp4").write_bytes(mp4.read_bytes())
                    port = await anyio.to_thread.run_sync(local_host._ensure_server)
                    public = await anyio.to_thread.run_sync(lambda: local_host._ensure_tunnel(port))
                    url = f"{public.rstrip('/')}/{slug}/walkthrough.mp4"
                except Exception:
                    pass
    except Exception as exc:  # noqa: BLE001
        return f"Filming failed for {merchant}: {exc}"

    if not url or url.startswith("file:"):
        return f"Filmed a walkthrough for {merchant}, but publishing didn't return a public URL."
    return f"🎬 {merchant}: AI walkthrough filmed → {url}"


@mcp.tool()
def draft_email(to_email: str = "") -> str:
    """Save the last built campaign's email as a Gmail draft (deck + video attached).

    Args:
        to_email: Recipient. If omitted, uses the prospect's verified email.

    Returns the Gmail draft link.
    """
    _log_call("draft_email", to_email)
    if not CAMPAIGN_PATH.exists():
        return ("No campaign to draft yet — run build_campaign first.")
    camp = json.loads(CAMPAIGN_PATH.read_text())

    recipient = (to_email or camp.get("recipient_email") or "").strip()
    if not recipient:
        return ("I don't have a recipient email for this prospect. "
                "Give me an address to draft to.")

    from agents.sales.gmail_draft import create_draft
    attachments = [p for p in (camp.get("walkthrough_mp4"), camp.get("deck_pptx"))
                   if p and Path(p).exists()]
    try:
        with _quiet_stdout():
            res = create_draft(
                to_email=recipient,
                subject=camp.get("email_subject", ""),
                body=camp.get("email_body", ""),
                attachments=attachments,
            )
    except Exception as exc:  # noqa: BLE001
        return f"Couldn't create the draft: {exc}"

    if not res.get("ok"):
        return (f"Couldn't create the draft: {res.get('error','unknown error')}. "
                "You may need to authorize Gmail (`revenant gmail-auth`).")
    url = res.get("gmail_url", "")
    skipped = res.get("skipped") or []
    msg = f"✉️ Draft saved to Gmail for {recipient} — {url}"
    if skipped:
        msg += f"\n(Not attached: {', '.join(skipped)}.)"
    return msg


@mcp.tool()
def status() -> str:
    """Report what Revenant currently has loaded: active startup, last shortlist,
    last built campaign."""
    _log_call("status")
    lines = []
    if ACTIVE_CTX_PATH.exists():
        try:
            c = json.loads(ACTIVE_CTX_PATH.read_text())
            lines.append(f"Selling for: {c.get('product_name') or c.get('source','?')} "
                         f"(from {', '.join(c.get('sources', [c.get('source','?')]))})")
        except Exception:
            pass
    else:
        lines.append("No startup loaded yet — call setup_startup.")
    if SHORTLIST_PATH.exists():
        try:
            s = json.loads(SHORTLIST_PATH.read_text())
            ps = s.get("prospects") or []
            names = ", ".join(p.get("company_name", "?") for p in ps)
            lines.append(f"Last shortlist ({s.get('ask','')}): {names}")
        except Exception:
            pass
    if CAMPAIGN_PATH.exists():
        try:
            camp = json.loads(CAMPAIGN_PATH.read_text())
            lines.append(f"Last campaign built: {camp.get('company','?')} "
                         f"→ {camp.get('prototype_url','')}")
        except Exception:
            pass
    return "\n".join(lines) if lines else "Nothing loaded yet."


if __name__ == "__main__":
    mcp.run()  # stdio transport
