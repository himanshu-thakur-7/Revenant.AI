#!/usr/bin/env python3
"""Run the full autopilot chain from a single scripted prompt.

Used for demos / regression recordings. Streams the same UI (banner, boot
lines, ghost creature, per-tool status) the interactive REPL shows, so we
can capture a real terminal recording without the founder having to type.

Usage:
    python scripts/autopilot_demo.py            # canned healthtech prompt
    python scripts/autopilot_demo.py "your prompt here"

Environment:
    DIRECTOR_SKIP_LIPSYNC=1 skips the D-ID lip-sync step (saves credits).
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("REVENANT_MODE", "live")
    # Deferred imports so we can set env vars first.
    from ghost.config import get_settings
    get_settings.cache_clear()
    from ghost.config import settings
    from ghost.llm import COST

    from agents.base import AgentEvent
    from agents.context import FounderContext
    from agents.orchestrator import Orchestrator
    from agents import ui
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.text import Text

    console = Console()
    prompt = (sys.argv[1] if len(sys.argv) > 1 else
              "Find one US healthtech prospect and run the whole outbound chain.")

    ctx_path = os.path.expanduser("~/shroud")

    # ── banner + boot ─────────────────────────────────────────
    ui.render_banner(console, model=settings.llm_model, ctx_source=ctx_path)
    console.print()
    ui.boot_start(console)

    with console.status("[dim]ingesting founder context…[/dim]", spinner="dots"):
        ctx = FounderContext.from_folder(ctx_path)
    ui.boot_line(console, "ingest founder context", f"{len(ctx.files)} files")

    with console.status("[dim]briefing the orchestrator…[/dim]", spinner="dots"):
        _ = ctx.summary()
    ui.boot_line(console, "brief orchestrator", "briefed")

    ui.boot_line(console, "register agent tools", "8 tools")
    ui.boot_line(console, "wire full agent fleet",
                 "research + engineer + director + sales")
    ui.boot_line(console, "director lip-sync",
                 "SKIPPED (saving credits)" if settings.skip_lipsync
                 else "D-ID Fiona",
                 ok=not settings.skip_lipsync or True)
    ui.boot_ready(console)

    # ── founder prompt echo ───────────────────────────────────
    ui.print_founder_prompt(console)
    console.print(Text(prompt, style="white"))

    # ── sink: per-tool event printer with the ghost creature ─
    def make_sink():
        def sink(ev: AgentEvent):
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

    agent = Orchestrator(context=ctx)
    seance = ui.SeanceStatus(console)
    base_sink = make_sink()

    def wrapped_sink(ev: AgentEvent):
        if ev.kind == "tool_call":
            seance.update(f"[{ev.agent}]  {ui.stage_message(ev.tool)}")
        elif ev.kind == "final":
            seance.update("thinking…")
        base_sink(ev)

    with seance:
        final = agent.run_turn(prompt, on_event=wrapped_sink)

    # ── final reply ──────────────────────────────────────────
    console.print()
    ui.print_agent_reply_header(console, "revenant")
    console.print(Markdown(final.strip()))
    console.print()
    ui.print_status_bar(
        console,
        model=settings.llm_model,
        tools=len(agent.registry),
        ctx_files=len(ctx.files),
        cost_cents=COST.cents,
    )


if __name__ == "__main__":
    main()
