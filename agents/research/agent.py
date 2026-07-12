"""Research — Agent 1. Turns a brief into a prospect shortlist."""

from __future__ import annotations

from typing import Any

from ..base import Agent, EventSink
from .prompt import RESEARCH_SYSTEM
from .tools import ShortlistState, shortlist_tools, world_tools


class Research(Agent):
    name = "research"
    system = RESEARCH_SYSTEM
    tools: list = []           # bound per-instance so shortlist state is scoped
    max_iters = 10             # keep the loop cheap — 2-3 search+fetch cycles is plenty
    temperature = 0.3          # slightly cooler — this agent must be factual

    def __init__(self) -> None:
        super().__init__()
        self._state = ShortlistState()
        self.add_tools(world_tools())
        self.add_tools(shortlist_tools(self._state))

    @property
    def state(self) -> ShortlistState:
        return self._state

    def run_brief(self, brief: str, on_event: EventSink = None) -> dict[str, Any]:
        """Run the agent to completion on a brief; return the structured
        shortlist regardless of whether the LLM's final message is prose."""
        final_text = self.run_turn(brief, on_event=on_event)
        # If the LLM ran out of iterations (or ended with prose) after adding
        # prospects, treat the shortlist as final so the caller gets a clean
        # payload rather than `finalized: false` with real data attached.
        finalized = self._state.finalized or bool(self._state.prospects)
        return {
            "count": len(self._state.prospects),
            "prospects": self._state.snapshot(),
            "finalized": finalized,
            "summary": final_text[:800] if not self._state.finalized else "",
        }
