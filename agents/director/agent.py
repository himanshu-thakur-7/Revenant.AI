"""Director — Agent 3. Films a Loom-style walkthrough of the prototype."""

from __future__ import annotations

from typing import Any

from ..base import Agent, EventSink
from .prompt import DIRECTOR_SYSTEM
from .tools import action_tools, read_tools
from .walkthrough import WalkthroughState


class Director(Agent):
    name = "director"
    system = DIRECTOR_SYSTEM
    tools: list = []
    max_iters = 8
    temperature = 0.6           # some warmth for the narration copy

    def __init__(self, *, prototype_url: str, prospect: dict[str, Any]) -> None:
        super().__init__()
        self._prototype_url = prototype_url
        self._prospect = prospect
        company = prospect.get("company_name") or "prospect"
        self._state = WalkthroughState.for_prospect(company)

        self.add_tools(read_tools(prototype_url, prospect))
        self.add_tools(action_tools(self._state, prototype_url))

    @property
    def state(self) -> WalkthroughState:
        return self._state

    def film(self, on_event: EventSink = None) -> dict[str, Any]:
        opening = (
            "Film the walkthrough for this prospect. Start with "
            "`read_prototype_url` and `read_prospect_context`, compose "
            "4-6 beats, then call `render_walkthrough` ONCE. Finalize."
        )
        text = self.run_turn(opening, on_event=on_event)
        finalized = self._state.finalized or bool(self._state.stream_iframe_url)
        return {
            "prospect_slug": self._state.prospect_slug,
            "workspace": str(self._state.workspace),
            "video_url": self._state.stream_iframe_url or "",
            "mp4_path": self._state.mp4_path or "",
            "duration_s": round(sum(self._state.mp3_durations), 2),
            "beats": len(self._state.beats),
            "finalized": finalized,
            "notes": text[:600] if not self._state.finalized else "",
        }
