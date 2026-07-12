"""`revenant chat` — the Orchestrator REPL.

Also exposes `revenant research --brief "..."` so a sub-agent can be run in
isolation for testing without going through the Orchestrator.

Slash commands inside chat:
    /context <path-or-url>   attach a new startup context
    /tools                   list tools available to the agent
    /reset                   clear conversation memory (keeps context)
    /help                    show this
    /quit  /exit             leave
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from ghost.config import settings
from ghost.llm import COST

from .base import AgentEvent
from .bridge import bridge  # noqa: F401  (self-registers the Convex live mirror)
from .context import FounderContext
from .director import Director
from .engineer import Engineer
from .orchestrator import Orchestrator
from .research import Research
from .sales import Sales
from . import ui


app = typer.Typer(add_completion=False, no_args_is_help=False, invoke_without_command=True)
console = Console()


# ── context resolution ─────────────────────────────────────────
def _load_context(spec: str) -> FounderContext:
    spec = spec.strip()
    if not spec:
        raise ValueError("empty context spec")
    if spec.startswith(("http://", "https://", "git@")) or spec.startswith("github.com/"):
        return FounderContext.from_github(spec)
    # `owner/repo` short form: no dots, single slash, doesn't exist as a path
    if "/" in spec and not any(sep in spec for sep in [".", os.sep]) and not Path(spec).exists():
        return FounderContext.from_github(spec)
    return FounderContext.from_folder(spec)


# ── event → terminal ───────────────────────────────────────────
def _event_printer(agent_label: str = "revenant"):
    def sink(ev: AgentEvent) -> None:
        if ev.kind == "tool_call":
            ui.print_tool_event(console, "call", ev.tool,
                                ui.joined_args(ev.args), agent=ev.agent)
        elif ev.kind == "tool_result":
            snippet = ev.result.replace("\n", " ⏎ ")
            ui.print_tool_event(console, "result", ev.tool,
                                ui.short(snippet, 220), agent=ev.agent)
        elif ev.kind == "error":
            ui.print_tool_event(console, "error", "", ev.text, agent=ev.agent)
    return sink


def _print_reply(text: str, agent: str = "revenant") -> None:
    if not text.strip():
        return
    ui.print_agent_reply_header(console, agent)
    console.print(Markdown(text.strip()))
    console.print()


# ── entrypoint ─────────────────────────────────────────────────
@app.callback()
def _root() -> None:
    """Revenant CLI — chat with the Orchestrator, then let it delegate."""


# ── chat ───────────────────────────────────────────────────────
@app.command("chat")
def chat_cmd(
    repo: str = typer.Option(None, "--repo", "-r", help="Local folder of the founder's startup."),
    github: str = typer.Option(None, "--github", "-g", help="GitHub URL or owner/repo to shallow-clone."),
    no_context: bool = typer.Option(False, "--no-context", help="Start without a startup context."),
) -> None:
    """Open the founder ↔ Orchestrator REPL."""
    _boot(repo=repo, github=github, no_context=no_context)


def _boot(*, repo: str | None, github: str | None, no_context: bool) -> None:
    ctx: FounderContext | None = None
    spec = repo or github
    ctx_label = spec

    ui.render_banner(console, model=settings.llm_model, ctx_source=ctx_label)
    console.print()
    ui.boot_start(console)

    if spec and not no_context:
        try:
            with console.status(f"[dim]ingesting {spec}…[/dim]", spinner="dots"):
                ctx = _load_context(spec)
            ui.boot_line(console, "ingest founder context", f"{len(ctx.files)} files")
        except Exception as exc:
            ui.boot_line(console, "ingest founder context", f"FAILED — {exc}", ok=False)
            return
        try:
            with console.status("[dim]briefing the orchestrator…[/dim]", spinner="dots"):
                _ = ctx.summary()
            ui.boot_line(console, "brief orchestrator", "briefed")
        except Exception as exc:  # pragma: no cover - LLM path
            ui.boot_line(console, "brief orchestrator", f"skipped — {exc}", ok=False)

    agent = Orchestrator(context=ctx)
    ui.boot_line(console, "register agent tools", f"{len(agent.registry)} tools")
    ui.boot_line(console, "wire research agent", "linkup + web recon")
    ui.boot_line(console, "engineer / director / sales", "stubs")
    ui.boot_ready(console)

    if ctx is None:
        console.print(
            Padding(Text.assemble(
                ("no startup context loaded. attach one with ", ui.MUTED),
                ("/context ~/my-startup", ui.WISP),
                (" or ", ui.MUTED),
                ("/context owner/repo", ui.WISP),
            ), (0, 0, 1, 2)),
        )
    ui.print_slash_hint(console)
    console.print()

    _repl(agent)


def _repl(agent: Orchestrator) -> None:
    while True:
        try:
            ui.print_founder_prompt(console)
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print(Text("  ⌇ session closed", style=ui.MUTED))
            return

        if not line:
            continue

        if line.startswith("/"):
            if _handle_slash(agent, line):
                return
            continue

        # Séance mode: a persistent ghost banner cycles while the LLM works,
        # with the status line updated on every tool call so the founder sees
        # what the fleet is doing.
        seance = ui.SeanceStatus(console)
        base_sink = _event_printer("revenant")

        def sink_with_status(ev: AgentEvent) -> None:
            # Update the ghost banner FIRST so it changes just before the
            # detailed line prints.
            if ev.kind == "tool_call":
                seance.update(f"[{ev.agent}]  {ui.stage_message(ev.tool)}")
            elif ev.kind == "final":
                seance.update("thinking…")
            base_sink(ev)

        try:
            with seance:
                final = agent.run_turn(line, on_event=sink_with_status)
        except Exception as exc:
            console.print(Text(f"  ! {exc.__class__.__name__}: {exc}", style=ui.BLOOD))
            continue

        _print_reply(final)
        ui.print_status_bar(
            console,
            model=settings.llm_model,
            tools=len(agent.registry),
            ctx_files=len(agent.context.files) if agent.context else 0,
            cost_cents=COST.cents,
        )
        console.print()


def _handle_slash(agent: Orchestrator, line: str) -> bool:
    parts = line[1:].split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in {"quit", "exit", "q"}:
        console.print(Text("  ⌇ session closed", style=ui.MUTED))
        return True

    if cmd == "reset":
        agent.reset()
        console.print(Text("  ⌇ memory cleared", style=ui.MUTED))
        return False

    if cmd == "tools":
        for t in agent.registry:
            console.print(
                Text(f"  • {t.name}", style=ui.WISP),
                Text(f"  {t.description}", style=ui.MUTED),
            )
        return False

    if cmd == "context":
        if not arg:
            if agent.context:
                console.print(Text(
                    f"  current context: {agent.context.source} "
                    f"({len(agent.context.files)} files)", style=ui.MUTED,
                ))
            else:
                console.print(Text("  no context loaded", style=ui.MUTED))
            return False
        try:
            with console.status(f"[dim]ingesting {arg}…[/dim]", spinner="dots"):
                new_ctx = _load_context(arg)
            with console.status("[dim]briefing the orchestrator…[/dim]", spinner="dots"):
                agent.bind_context(new_ctx)
            console.print(Text(
                f"  ✓ context attached — {len(new_ctx.files)} files from {new_ctx.source}",
                style=ui.MUTED,
            ))
        except Exception as exc:
            console.print(Text(f"  ! could not ingest {arg}: {exc}", style=ui.BLOOD))
        return False

    if cmd in {"help", "h", "?"}:
        console.print(Text(
            "  /context <path-or-repo>   attach founder context\n"
            "  /tools                    list agent tools\n"
            "  /reset                    clear conversation memory\n"
            "  /quit                     exit",
            style=ui.MUTED,
        ))
        return False

    console.print(Text(f"  ! unknown command: /{cmd}", style=ui.BLOOD))
    return False


# ── standalone research (for iterating on Agent 1) ─────────────
@app.command("research")
def research_cmd(
    brief: str = typer.Argument(..., help="Prospect brief — ICP, pain signals, count."),
    count: int = typer.Option(3, "--count", "-n", help="Target shortlist size."),
) -> None:
    """Run the Research agent directly (skip the Orchestrator)."""
    ui.render_banner(console, model=settings.llm_model, ctx_source=None)
    console.print()
    ui.boot_start(console)
    ui.boot_line(console, "spawning research agent", f"target={count}")
    ui.boot_line(console, "linkup key",
                 "configured" if settings.linkup_api_key else "MISSING", ok=bool(settings.linkup_api_key))
    ui.boot_ready(console)

    r = Research()
    full = f"{brief}\n\nTarget shortlist size: {count}."
    sink = _event_printer("research")

    with console.status("[dim]scouting…[/dim]", spinner="dots"):
        result = r.run_brief(full, on_event=sink)

    console.print()
    _render_shortlist(result)
    console.print()
    ui.print_status_bar(
        console,
        model=settings.llm_model,
        tools=len(r.registry),
        ctx_files=0,
        cost_cents=COST.cents,
    )


def _render_shortlist(result: dict) -> None:
    n = result.get("count", 0)
    console.print(Text(f"  ── shortlist ── {n} prospect(s)", style=ui.WISP))
    for i, p in enumerate(result.get("prospects", []), 1):
        console.print()
        console.print(Text(f"  [{i}] {p['company_name']}", style=f"bold {ui.INK}"),
                      Text(f"  {p['company_domain']}", style=ui.FAINT))
        console.print(Text(f"      industry: {p.get('industry', '—')}", style=ui.MUTED))
        console.print(Text(f"      fit: {p['fit_score']:.2f}", style=ui.WISP),
                      Text(f"  {p['fit_rationale']}", style=ui.MUTED))
        c = p.get("contact") or {}
        if c.get("name"):
            emails = ", ".join(c.get("email_candidates", []) or []) or "—"
            console.print(Text(f"      contact: {c['name']} ({c.get('title','')})", style=ui.INK))
            console.print(Text(f"      emails: {emails}", style=ui.FAINT))
        for ev in p.get("pain_evidence", [])[:3]:
            excerpt = (ev.get("excerpt") or "")[:150]
            console.print(Text(f'      "{excerpt}"', style=f"italic {ui.MUTED}"))
            console.print(Text(f"        — {ev.get('source_url','')}", style=ui.FAINT))


# ── standalone engineer (for iterating on Agent 2) ─────────────
@app.command("engineer")
def engineer_cmd(
    repo: str = typer.Argument(..., help="Founder's startup — local folder or owner/repo."),
    company: str = typer.Option(..., "--company", "-c", help="Prospect company name."),
    domain: str = typer.Option(..., "--domain", "-d", help="Prospect company domain."),
    industry: str = typer.Option("", "--industry", "-i", help="Prospect industry."),
    person: str = typer.Option("", "--person", "-p", help="Prospect contact name."),
    title: str = typer.Option("", "--title", "-t", help="Prospect contact title."),
    email: str = typer.Option("", "--email", "-e", help="Best-guess email."),
    pain: str = typer.Option("", "--pain", help="One-line pain summary."),
    rationale: str = typer.Option("", "--rationale", help="Why they fit."),
) -> None:
    """Build one prototype for a hand-supplied prospect (skip the Orchestrator)."""
    ui.render_banner(console, model=settings.llm_model, ctx_source=repo)
    console.print()
    ui.boot_start(console)

    try:
        with console.status(f"[dim]ingesting {repo}…[/dim]", spinner="dots"):
            ctx = _load_context(repo)
        ui.boot_line(console, "ingest founder context", f"{len(ctx.files)} files")
    except Exception as exc:
        ui.boot_line(console, "ingest founder context", f"FAILED — {exc}", ok=False)
        return

    ui.boot_line(console, "cloudflare pages",
                 "configured" if settings.cloudflare_api_token and settings.cloudflare_account_id
                 else "not set — will fall back to file://",
                 ok=bool(settings.cloudflare_api_token))
    ui.boot_ready(console)

    prospect = {
        "company_name": company,
        "company_domain": domain,
        "industry": industry or "unknown",
        "contact": {"name": person, "title": title,
                    "email_candidates": [email] if email else [], "linkedin_url": None},
        "pain_evidence": [{"source_url": "", "excerpt": pain}] if pain else [],
        "fit_score": 0.75,
        "fit_rationale": rationale or "hand-supplied prospect",
    }

    eng = Engineer(founder_context=ctx, prospect=prospect)
    sink = _event_printer("engineer")

    with console.status("[dim]building prototype…[/dim]", spinner="dots"):
        result = eng.build(on_event=sink)

    console.print()
    console.print(Text(f"  ── prototype shipped", style=ui.WISP))
    console.print(Text(f"  workspace: {result['workspace']}", style=ui.MUTED))
    console.print(Text(f"  files: {', '.join(result['files']) or '—'}", style=ui.MUTED))
    if result["url"]:
        console.print(Text(f"  URL ({result['deployer']}): {result['url']}",
                           style=f"bold {ui.SUMMON}"))
    console.print()
    ui.print_status_bar(
        console,
        model=settings.llm_model,
        tools=len(eng.registry),
        ctx_files=len(ctx.files),
        cost_cents=COST.cents,
    )


# ── standalone director (for iterating on Agent 3) ────────────
@app.command("director")
def director_cmd(
    prototype_url: str = typer.Argument(..., help="URL of the prototype to film."),
    company: str = typer.Option(..., "--company", "-c", help="Prospect company name."),
    industry: str = typer.Option("", "--industry", "-i", help="Prospect industry."),
    person: str = typer.Option("", "--person", "-p", help="Prospect contact name."),
    title: str = typer.Option("", "--title", "-t", help="Prospect contact title."),
    pain: str = typer.Option("", "--pain", help="One-line pain summary."),
    rationale: str = typer.Option("", "--rationale", help="Why they fit."),
) -> None:
    """Film ONE walkthrough for a hand-supplied prospect (skip the Orchestrator)."""
    ui.render_banner(console, model=settings.llm_model, ctx_source=prototype_url)
    console.print()
    ui.boot_start(console)
    ui.boot_line(console, "prototype url", prototype_url[:50])
    ui.boot_line(console, "elevenlabs key",
                 "configured" if settings.elevenlabs_api_key else "not set — using macOS say fallback",
                 ok=bool(settings.elevenlabs_api_key))
    ui.boot_line(console, "d-id lip-sync",
                 "configured" if settings.did_api_key else "not set — bubble stays static",
                 ok=bool(settings.did_api_key))
    ui.boot_line(console, "cloudflare pages",
                 "configured" if settings.cloudflare_api_token else "MISSING",
                 ok=bool(settings.cloudflare_api_token))
    ui.boot_ready(console)

    prospect = {
        "company_name": company,
        "industry": industry or "unknown",
        "contact": {"name": person, "title": title,
                    "email_candidates": [], "linkedin_url": None},
        "pain_evidence": [{"source_url": "", "excerpt": pain}] if pain else [],
        "fit_rationale": rationale or "hand-supplied prospect",
    }

    d = Director(prototype_url=prototype_url, prospect=prospect)
    sink = _event_printer("director")

    with console.status("[dim]filming walkthrough…[/dim]", spinner="dots"):
        result = d.film(on_event=sink)

    console.print()
    console.print(Text(f"  ── walkthrough filmed", style=ui.WISP))
    console.print(Text(f"  workspace: {result['workspace']}", style=ui.MUTED))
    console.print(Text(f"  duration: {result['duration_s']}s  ({result['beats']} beats)", style=ui.MUTED))
    if result["mp4_path"]:
        console.print(Text(f"  local MP4: {result['mp4_path']}", style=ui.MUTED))
    if result["video_url"]:
        console.print(Text(f"  Video URL: {result['video_url']}", style=f"bold {ui.SUMMON}"))
    console.print()
    ui.print_status_bar(
        console,
        model=settings.llm_model,
        tools=len(d.registry),
        ctx_files=0,
        cost_cents=COST.cents,
    )


# ── standalone sales (for iterating on Agent 4) ────────────────
@app.command("sales")
def sales_cmd(
    repo: str = typer.Argument(..., help="Founder's startup — local folder or owner/repo."),
    prototype_url: str = typer.Option(..., "--prototype", help="Prototype URL from Engineer."),
    walkthrough_url: str = typer.Option(..., "--walkthrough", help="Walkthrough URL from Director."),
    company: str = typer.Option(..., "--company", "-c", help="Prospect company name."),
    domain: str = typer.Option(..., "--domain", "-d", help="Prospect company domain."),
    industry: str = typer.Option("", "--industry", "-i", help="Prospect industry."),
    person: str = typer.Option("", "--person", "-p", help="Prospect contact name."),
    title: str = typer.Option("", "--title", "-t", help="Prospect contact title."),
    email: str = typer.Option("", "--email", "-e", help="Best-guess email."),
    pain: str = typer.Option("", "--pain", help="One-line pain summary."),
    rationale: str = typer.Option("", "--rationale", help="Why they fit."),
) -> None:
    """Draft the outreach + deck for a hand-supplied prospect (skip Orchestrator)."""
    ui.render_banner(console, model=settings.llm_model, ctx_source=repo)
    console.print()
    ui.boot_start(console)

    try:
        with console.status(f"[dim]ingesting {repo}…[/dim]", spinner="dots"):
            ctx = _load_context(repo)
        ui.boot_line(console, "ingest founder context", f"{len(ctx.files)} files")
    except Exception as exc:
        ui.boot_line(console, "ingest founder context", f"FAILED — {exc}", ok=False)
        return

    ui.boot_line(console, "cloudflare pages",
                 "configured" if settings.cloudflare_api_token else "MISSING — deck saves file:// only",
                 ok=bool(settings.cloudflare_api_token))
    ui.boot_line(console, "convex review queue",
                 "configured" if settings.convex_url else "not set — local file only",
                 ok=bool(settings.convex_url))
    ui.boot_ready(console)

    prospect = {
        "company_name": company,
        "company_domain": domain,
        "industry": industry or "unknown",
        "contact": {"name": person, "title": title,
                    "email_candidates": [email] if email else [], "linkedin_url": None},
        "pain_evidence": [{"source_url": "", "excerpt": pain}] if pain else [],
        "fit_score": 0.8,
        "fit_rationale": rationale or "hand-supplied prospect",
    }

    s = Sales(founder_context=ctx, prospect=prospect,
              prototype_url=prototype_url, walkthrough_url=walkthrough_url)
    sink = _event_printer("sales")

    with console.status("[dim]drafting outreach + deck…[/dim]", spinner="dots"):
        result = s.draft(on_event=sink)

    console.print()
    console.print(Text(f"  ── draft ready — awaiting review", style=ui.WISP))
    console.print(Text(f"  campaign  : {result['campaign_id']}", style=ui.MUTED))
    console.print(Text(f"  workspace : {result['workspace']}", style=ui.MUTED))
    if result.get("email_subject"):
        console.print(Text(f"  subject   : {result['email_subject']}", style=ui.INK))
    if result.get("email_md_path"):
        console.print(Text(f"  email md  : {result['email_md_path']}", style=ui.MUTED))
    if result.get("deck_url"):
        console.print(Text(f"  deck URL  : {result['deck_url']}", style=f"bold {ui.SUMMON}"))
    if result.get("deck_pptx_path"):
        console.print(Text(f"  deck pptx : {result['deck_pptx_path']}", style=ui.MUTED))
    console.print()
    ui.print_status_bar(
        console,
        model=settings.llm_model,
        tools=len(s.registry),
        ctx_files=len(ctx.files),
        cost_cents=COST.cents,
    )


# ── telegram gateway ───────────────────────────────────────────
@app.command("telegram")
def telegram_cmd(
    repo: str = typer.Option("~/shroud", "--repo", "-r",
                             help="Founder's startup — local folder or owner/repo."),
    chat_id: int = typer.Option(None, "--chat-id",
                                help="Lock the bot to one chat id (recommended for demo)."),
    lipsync: bool = typer.Option(False, "--lipsync",
                                 help="Use D-ID lip-sync avatar (burns credits)."),
) -> None:
    """Launch the Telegram gateway — command Revenant from your phone."""
    from .telegram import RevenantBot

    token = settings.telegram_bot_token
    if not token:
        console.print(Text("  ! TELEGRAM_BOT_TOKEN not set in .env", style=ui.BLOOD))
        raise typer.Exit(1)

    ui.render_banner(console, model=settings.llm_model, ctx_source=repo)
    console.print()
    ui.boot_start(console)
    with console.status(f"[dim]ingesting {repo}…[/dim]", spinner="dots"):
        ctx = _load_context(repo)
    ui.boot_line(console, "ingest founder context", f"{len(ctx.files)} files")
    with console.status("[dim]briefing…[/dim]", spinner="dots"):
        _ = ctx.summary()
    ui.boot_line(console, "founder", f"{settings.founder_name} · {settings.founder_company or '—'}")
    ui.boot_line(console, "telegram gateway", "online — open the chat on your phone")
    ui.boot_line(console, "lip-sync", "D-ID" if lipsync else "off (macOS say)")
    ui.boot_ready(console)
    console.print(Text("  the founder now drives from Telegram. Ctrl-C to stop.\n",
                       style=ui.MUTED))

    locked = chat_id or (int(settings.telegram_chat_id)
                         if settings.telegram_chat_id else None)
    bot = RevenantBot(token, ctx, allowed_chat_id=locked, skip_lipsync=not lipsync)
    try:
        bot.run()
    except KeyboardInterrupt:
        console.print(Text("\n  ⌇ gateway closed", style=ui.MUTED))


if __name__ == "__main__":  # pragma: no cover
    app()
