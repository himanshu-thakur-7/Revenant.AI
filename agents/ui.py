"""Terminal UI primitives — the look and feel of the Revenant CLI.

Rich-backed banner, boot sequence, status footer, and reply frames. Everything
here is presentation only; the agents never call these directly.
"""

from __future__ import annotations

import time
from typing import Iterable

from rich.align import Align
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text


# ── palette (mirrors console/src/theme.css) ────────────────────
WISP = "#52e0c4"
WISP_SOFT = "#7defd6"
EMBER = "#f5a623"
NECRO = "#b98cff"
BLOOD = "#ff5773"
SUMMON = "#5ef2a0"
INK = "#e8ecf4"
MUTED = "#7b869c"
FAINT = "#485064"


# ── the wordmark ───────────────────────────────────────────────
_BANNER = r"""
██████╗ ███████╗██╗   ██╗███████╗███╗   ██╗ █████╗ ███╗   ██╗████████╗
██╔══██╗██╔════╝██║   ██║██╔════╝████╗  ██║██╔══██╗████╗  ██║╚══██╔══╝
██████╔╝█████╗  ██║   ██║█████╗  ██╔██╗ ██║███████║██╔██╗ ██║   ██║
██╔══██╗██╔══╝  ╚██╗ ██╔╝██╔══╝  ██║╚██╗██║██╔══██║██║╚██╗██║   ██║
██║  ██║███████╗ ╚████╔╝ ███████╗██║ ╚████║██║  ██║██║ ╚████║   ██║
╚═╝  ╚═╝╚══════╝  ╚═══╝  ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝   """


# vertical gradient across banner lines — top bright, bottom faded
_BANNER_COLORS = ["#a7f3e2", "#7defd6", "#52e0c4", "#3ec5aa", "#2ea08a", "#1f7d6b"]


def _banner_text() -> Text:
    lines = _BANNER.strip("\n").splitlines()
    t = Text()
    for i, line in enumerate(lines):
        colour = _BANNER_COLORS[min(i, len(_BANNER_COLORS) - 1)]
        t.append(line + "\n", style=colour)
    return t


def render_banner(console: Console, model: str, ctx_source: str | None) -> None:
    """The big BOOT ASCII banner and identity line."""
    tag = Text(" the autonomous outbound engineer ", style=f"italic {MUTED}")
    ctx_line = Text(
        f"context: {ctx_source or '— none loaded —'}",
        style=f"{FAINT}",
    )
    model_line = Text(f"model: {model}", style=f"{FAINT}")
    body = Group(
        Align.left(_banner_text()),
        Padding(Align.left(tag), (0, 0, 0, 2)),
        Padding(Align.left(model_line), (1, 0, 0, 2)),
        Padding(Align.left(ctx_line), (0, 0, 0, 2)),
    )
    console.print()
    console.print(Panel(body, border_style=FAINT, padding=(1, 2)))


# ── boot sequence ──────────────────────────────────────────────
def boot_line(console: Console, label: str, detail: str = "", ok: bool = True) -> None:
    """A single `→ step ................. detail` boot line."""
    dots = "." * max(3, 42 - len(label))
    icon = Text("→ ", style=WISP)
    lbl = Text(label, style=INK)
    dot = Text(f" {dots} ", style=FAINT)
    det = Text(detail, style=SUMMON if ok else BLOOD)
    console.print(Text.assemble(icon, lbl, dot, det))
    time.sleep(0.05)  # subtle typewriter cadence


def boot_start(console: Console) -> None:
    console.print(Text("initiating", style=WISP), Text("session…", style=MUTED))
    console.print()


def boot_ready(console: Console) -> None:
    console.print()
    console.print(Text("  ready.", style=SUMMON))
    console.print()


# ── prompts / replies ──────────────────────────────────────────
def print_founder_prompt(console: Console) -> None:
    """The '>' the founder types after."""
    console.print(Text("founder ", style=f"bold {EMBER}"), end="")
    console.print(Text("▸ ", style=EMBER), end="")


def print_agent_reply_header(console: Console, agent: str = "revenant") -> None:
    console.print(Text(f"{agent} ", style=f"bold {WISP}"), end="")
    console.print(Text("▸ ", style=WISP), end="")


_AGENT_COLOR = {
    "orchestrator": WISP,
    "research": NECRO,
    "engineer": SUMMON,
    "director": EMBER,
    "sales": WISP_SOFT,
}


def print_tool_event(
    console: Console,
    kind: str,
    tool: str,
    detail: str,
    agent: str = "orchestrator",
) -> None:
    colour = _AGENT_COLOR.get(agent, MUTED)
    if kind == "call":
        tag = Text(f"  [{agent}] ", style=colour)
        name = Text(tool, style=colour)
        args = Text(f"({detail})", style=FAINT)
        console.print(Text.assemble(tag, name, args))
    elif kind == "result":
        icon = Text("    ← ", style=FAINT)
        text = Text(detail, style=FAINT)
        console.print(Text.assemble(icon, text))
    elif kind == "error":
        console.print(Text(f"  ! [{agent}] {detail}", style=BLOOD))


def print_status_bar(
    console: Console,
    *,
    model: str,
    tools: int,
    ctx_files: int,
    cost_cents: float,
) -> None:
    """A dim status line under a reply — model / tools / context / cost."""
    parts: list[Text] = []
    parts.append(Text("●", style=WISP))
    parts.append(Text(f" {model} ", style=MUTED))
    parts.append(Text("│", style=FAINT))
    parts.append(Text(f" {tools} tools ", style=MUTED))
    parts.append(Text("│", style=FAINT))
    parts.append(Text(f" ctx: {ctx_files} files ", style=MUTED))
    parts.append(Text("│", style=FAINT))
    parts.append(Text(f" ${cost_cents/100:.4f}", style=WISP))
    console.print(Text.assemble(*parts))


def print_slash_hint(console: Console) -> None:
    console.print(
        Text("  ", style=FAINT)
        + Text("/context", style=WISP)
        + Text("  ", style=FAINT)
        + Text("/tools", style=WISP)
        + Text("  ", style=FAINT)
        + Text("/reset", style=WISP)
        + Text("  ", style=FAINT)
        + Text("/quit", style=WISP)
        + Text("   " + "─" * 8, style=FAINT)
    )


# ── séance-mode animation (Live widget driver) ─────────────────
# Multi-line ASCII creature — a proper wispy Revenant ghost sprite with a
# domed head, curious eyes, and a fluttering tail. The tail cycles between
# two wave patterns (peaks up vs peaks down) so it looks like fabric
# rippling. Eyes drift left/right and blink at intervals. 12 frames total,
# rendered at 10 fps (100 ms per frame) via ``rich.live``.
_CREATURE_FRAMES = [
    # frame 0 — eyes center, tail peaks down
    ["     ╭───╮     ",
     "    ╱ ◕ ◕ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╲╱╲╱╲╱     "],
    # frame 1 — eyes center, tail peaks up
    ["     ╭───╮     ",
     "    ╱ ◕ ◕ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╱╲╱╲╱╲     "],
    # frame 2 — glance right, tail down
    ["     ╭───╮     ",
     "    ╱  ◕◕ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╲╱╲╱╲╱     "],
    # frame 3 — glance right, tail up
    ["     ╭───╮     ",
     "    ╱  ◕◕ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╱╲╱╲╱╲     "],
    # frame 4 — eyes center again
    ["     ╭───╮     ",
     "    ╱ ◕ ◕ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╲╱╲╱╲╱     "],
    # frame 5 — BLINK
    ["     ╭───╮     ",
     "    ╱ ─ ─ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╱╲╱╲╱╲     "],
    # frame 6 — eyes reopen
    ["     ╭───╮     ",
     "    ╱ ◕ ◕ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╲╱╲╱╲╱     "],
    # frame 7 — glance left, tail up
    ["     ╭───╮     ",
     "    ╱ ◕◕  ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╱╲╱╲╱╲     "],
    # frame 8 — glance left, tail down
    ["     ╭───╮     ",
     "    ╱ ◕◕  ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╲╱╲╱╲╱     "],
    # frame 9 — center, subtle mouth twitch
    ["     ╭───╮     ",
     "    ╱ ◕ ◕ ╲    ",
     "   │   ‿   │   ",
     "    ╲_____╱    ",
     "    ╱╲╱╲╱╲     "],
    # frame 10 — center, tail down
    ["     ╭───╮     ",
     "    ╱ ◕ ◕ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╲╱╲╱╲╱     "],
    # frame 11 — quick blink
    ["     ╭───╮     ",
     "    ╱ ⌒ ⌒ ╲    ",
     "   │   ▽   │   ",
     "    ╲_____╱    ",
     "    ╱╲╱╲╱╲     "],
]

# Bobbing offset per frame — every third frame the creature is one row lower,
# giving a subtle floating cadence at 10 fps.
_BOB_OFFSETS = [0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0]

_CANDLE_FRAMES = ["🕯️  ", " 🕯️ ", "  🕯️", " 🕯️ "]

# Themed narrator lines the CLI picks per tool_call so the founder sees
# something poetic and specific instead of a generic "thinking...".
_STAGE_MESSAGES = {
    # orchestrator level
    "spawn_research_agent":  "consulting the detective…",
    "spawn_engineer_agent":  "waking the engineer at the bench…",
    "spawn_director_agent":  "rolling film on the séance…",
    "spawn_sales_agent":     "drafting the missive + the deck…",
    # research
    "web_search":            "hunting on the wire…",
    "fetch_page":            "reading the prospect's own words…",
    "extract_pain_signals":  "distilling their pain into a shape we can pitch…",
    "guess_emails":          "guessing the founder's inbox by ritual…",
    "add_prospect":          "chalking another name into the circle…",
    "list_prospects":        "reviewing the circle so far…",
    "finalize_shortlist":    "sealing the shortlist…",
    # engineer
    "list_founder_files":    "reading your own repo…",
    "read_founder_file":     "reading your own repo…",
    "search_founder_context":"grep-ing your soul…",
    "read_prospect_brief":   "studying who we're building for…",
    "write_prototype_file":  "conjuring the prototype in HTML…",
    "list_prototype_files":  "checking the workshop…",
    "deploy_prototype":      "pushing to Cloudflare's edge…",
    "finalize_prototype":    "the prototype is live…",
    # director
    "read_prototype_url":    "aiming the camera…",
    "read_prospect_context": "reading the tone we need to match…",
    "render_walkthrough":    "narrating, filming, and muxing the Loom…",
    "finalize_walkthrough":  "the walkthrough is uploaded…",
    # sales
    "read_walkthrough_url":  "grabbing the video URL for the missive…",
    "read_founder_pitch":    "recalling your product's pitch…",
    "write_pitch_deck":      "assembling the pitch deck…",
    "deploy_deck":           "pushing the deck to Cloudflare…",
    "save_draft":            "queuing the draft for your review…",
    "finalize_sales":        "sealed. the missive awaits your click…",
}


def stage_message(tool_name: str) -> str:
    """A poetic status string for a tool call. Falls back to a generic line."""
    return _STAGE_MESSAGES.get(tool_name, f"working on {tool_name}…")


class SeanceStatus:
    """A ``rich.live.Live`` wrapper that shows the ASCII Revenant creature
    plus a cycling status line.

    Update the message with ``update(msg)`` when a new sub-agent tool fires.
    The creature keeps bobbing and blinking on its own via
    ``rich.live``'s ``refresh_per_second`` — no explicit ticks required.
    """

    def __init__(self, console: Console) -> None:
        from rich.live import Live  # deferred
        self._console = console
        self._msg = "the office is dark. the loop begins at 03:00…"
        self._frame = 0
        # 10 fps → one frame every 100 ms. `rich.live` handles the refresh
        # itself, so the ghost animates even while a slow tool call is
        # blocking the loop.
        self._live = Live(
            self._render(), console=console, refresh_per_second=10,
            transient=True,
        )

    def _render(self):
        from rich.console import Group
        from rich.padding import Padding
        from rich.text import Text as T

        idx = self._frame % len(_CREATURE_FRAMES)
        creature_lines = _CREATURE_FRAMES[idx]
        bob = _BOB_OFFSETS[idx]

        # The tail (last line) picks up an ember tint so it looks like the
        # wisp fabric catches candlelight while the body stays teal-cool.
        creature_body = T()
        if bob == 0:
            head_lines = creature_lines[:-1]
            tail_line = creature_lines[-1]
        else:
            creature_body.append("               \n", style=FAINT)
            head_lines = creature_lines[:-1]
            tail_line = creature_lines[-1]
        for line in head_lines:
            creature_body.append(line + "\n", style=WISP)
        creature_body.append(tail_line + "\n", style=EMBER)
        if bob == 0:
            creature_body.append("               \n", style=FAINT)

        candle = _CANDLE_FRAMES[self._frame % len(_CANDLE_FRAMES)]
        status = (T(candle, style=EMBER) + T(" ", style=FAINT)
                  + T(self._msg, style=MUTED))

        self._frame += 1
        return Group(creature_body, Padding(status, (0, 0, 0, 1)))

    def start(self) -> None:
        self._live.__enter__()

    def stop(self) -> None:
        self._live.__exit__(None, None, None)

    def update(self, message: str) -> None:
        self._msg = message
        self._live.update(self._render(), refresh=True)

    def __enter__(self) -> "SeanceStatus":
        self.start(); return self

    def __exit__(self, *exc) -> None:
        self.stop()


# ── util ───────────────────────────────────────────────────────
def typewriter(console: Console, text: str, per_char: float = 0.008) -> None:
    """Print text one char at a time. Use SPARINGLY (boot lines only)."""
    for ch in text:
        console.file.write(ch)
        console.file.flush()
        if per_char:
            time.sleep(per_char)
    console.file.write("\n")


def short(v, n: int = 60) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def joined_args(args: dict | None) -> str:
    if not args:
        return ""
    return ", ".join(f"{k}={short(v, 40)}" for k, v in args.items())
