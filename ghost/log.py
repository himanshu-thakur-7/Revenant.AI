"""Tiny rich-backed logger with a consistent narrator voice.

Doubles as the source of the Telegram-style narration lines the demo shows —
each stage calls ``log.stage()`` so the operator (and, wired up, Hermes on
Telegram) sees the pipeline think out loud.
"""

from __future__ import annotations

from rich.console import Console

_c = Console()


class _Log:
    def stage(self, msg: str) -> None:
        _c.print(f"[bold cyan]▸[/bold cyan] {msg}")

    def ok(self, msg: str) -> None:
        _c.print(f"[bold green]✓[/bold green] {msg}")

    def warn(self, msg: str) -> None:
        _c.print(f"[bold yellow]![/bold yellow] {msg}")

    def kill(self, msg: str) -> None:
        _c.print(f"[bold red]✗[/bold red] {msg}")

    def dim(self, msg: str) -> None:
        _c.print(f"[dim]{msg}[/dim]")

    def info(self, msg: str) -> None:
        _c.print(msg)


log = _Log()
