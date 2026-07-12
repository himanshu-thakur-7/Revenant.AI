"""The mission log — structured events for every move an agent makes.

This is what makes the storyline *visible*. Every pipeline stage emits typed
events tagged with an act (II–V) and an agent name; the console replays them
as a live feed, so a judge literally watches the Detective formulate queries,
the truth ledger fill with verbatim evidence, the Developer's code pass the
sandbox, the Director film beat by beat, and the Closer hand off to a human.

Events accumulate on a module singleton and are flushed to the ledger (and, in
live mode, to Convex) alongside campaigns. ``at`` is seconds from run start —
the replay clock the console uses for cinematic pacing.
"""

from __future__ import annotations

from typing import Any

from .models import new_id

# Agent display names, fixed vocabulary the console colors by.
DETECTIVE = "Detective"
LEDGER = "Truth Ledger"
GATEKEEPER = "Gatekeeper"
PROFILER = "Profiler"
DEVELOPER = "Developer"
SANDBOX = "Sandbox"
SITE_WEAVER = "Site Weaver"
COPYWRITER = "Copywriter"
VOICE = "Voice Director"
DIRECTOR = "Director"
OUTREACH = "Outreach"
PERSISTENCE = "Persistence"
CLOSER = "Closer"
HUMAN = "Human Closer"
PAYMENT = "Razorpay"


class MissionLog:
    """Accumulates events with an auto-advancing replay clock."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._clock: float = 0.0

    def emit(
        self,
        act: int,
        agent: str,
        message: str,
        *,
        campaign_id: str = "",
        company: str = "",
        kind: str = "info",  # info|query|evidence|verdict|code|artifact|film|voice|mail|alert|payment|state
        dwell: float = 1.6,   # seconds the replay lingers before the next event
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._clock += dwell
        self._events.append(
            {
                "id": new_id("ev_"),
                "at": round(self._clock, 1),
                "act": act,
                "agent": agent,
                "kind": kind,
                "message": message,
                "campaign_id": campaign_id,
                "company": company,
                "payload": payload or {},
            }
        )

    def all(self) -> list[dict[str, Any]]:
        return list(self._events)

    def reset(self) -> None:
        self._events.clear()
        self._clock = 0.0


mission = MissionLog()
