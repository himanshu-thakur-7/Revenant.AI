"""Engineer — Agent 2. Reads founder code, ships a per-prospect prototype."""

from __future__ import annotations

import json
import os
from typing import Any

from ..base import Agent, EventSink
from ..context import FounderContext
from .prompt import ENGINEER_SYSTEM
from .prototype import PrototypeState
from .tools import founder_tools, prospect_tool, workspace_tools


class Engineer(Agent):
    name = "engineer"
    system = ENGINEER_SYSTEM
    tools: list = []           # bound per-instance
    max_iters = 14
    temperature = 0.5          # a bit warmer — we want creative copy inside guardrails
    # The prototype is the first thing the prospect sees — quality matters
    # more than token cost. Run it on a reasoning model (gpt-5-mini) via the
    # strong route: better layout/styling reasoning for complex prototypes.
    # Override with ENGINEER_MODEL. gpt-5 models reject temperature (handled
    # in base.py) so the class temperature is ignored for them.
    use_strong_model = True
    model = os.getenv("ENGINEER_MODEL", "gpt-5-mini")

    def __init__(self, *, founder_context: FounderContext, prospect: dict[str, Any]) -> None:
        super().__init__()
        self._ctx = founder_context
        self._prospect = prospect
        company = prospect.get("company_name") or "prospect"
        self._state = PrototypeState.for_prospect(company)

        self.add_tools(founder_tools(founder_context))
        self.add_tools(prospect_tool(prospect))
        self.add_tools(workspace_tools(self._state))

    @property
    def state(self) -> PrototypeState:
        return self._state

    def build(self, on_event: EventSink = None) -> dict[str, Any]:
        """Run one autonomous build for the bound prospect. Returns the
        finalized payload (URL + summary), regardless of whether the LLM
        called finalize_prototype explicitly."""
        # Pre-fetch the prospect's live brand (colours/fonts/wordmark) so the
        # prototype can match their site instead of a generic template. Baked
        # into the opening so the LLM can't skip it (best-effort; empty on fail).
        brand_brief = ""
        domain = (self._prospect.get("company_domain")
                  or self._prospect.get("domain") or "")
        if domain:
            try:
                from .brand import fetch_brand
                brand_brief = fetch_brand(domain)
            except Exception:
                brand_brief = ""

        opening = (
            "Build the prototype for the prospect. Start by calling "
            "`read_prospect_brief`, then study the founder's product, then "
            "write the single-page prototype and deploy."
        )
        if brand_brief:
            opening += (
                "\n\n## The prospect's live brand — MATCH IT\n"
                "I pulled these signals straight from their homepage. Make the "
                "prototype look like it belongs on THEIR site: use their accent "
                "colours, echo their font choices (via Google Fonts if needed), "
                "and mirror their wordmark styling in the hero. This is what "
                "makes it feel custom-built for them.\n\n"
                f"{brand_brief}"
            )
        text = self.run_turn(opening, on_event=on_event)

        finalized = self._state.finalized or bool(self._state.deployment_url)
        return {
            "prospect_slug": self._state.prospect_slug,
            "workspace": str(self._state.workspace),
            "url": self._state.deployment_url or "",
            "deployer": self._state.deployer or "none",
            "files": self._state.paths(),
            "finalized": finalized,
            "notes": text[:600] if not self._state.finalized else "",
        }
