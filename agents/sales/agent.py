"""Sales — Agent 4. Drafts email + pitch deck, queues for founder review."""

from __future__ import annotations

from typing import Any

from ..base import Agent, EventSink
from ..context import FounderContext
from .draft import DraftState
from .prompt import SALES_SYSTEM
from .tools import action_tools, read_tools


class Sales(Agent):
    name = "sales"
    system = SALES_SYSTEM
    tools: list = []
    max_iters = 10
    temperature = 0.55           # a little warmth for the copy

    def __init__(
        self, *,
        founder_context: FounderContext | None,
        prospect: dict[str, Any],
        prototype_url: str,
        walkthrough_url: str,
    ) -> None:
        from ghost.config import settings

        identity = (
            "## Founder identity (NON-NEGOTIABLE)\n"
            f"You write on behalf of **{settings.founder_name}**"
            + (f" of **{settings.founder_company}**" if settings.founder_company else "")
            + ".\n"
            f"- Sign the email exactly: `{settings.founder_name.split()[0]}`\n"
            + (f"- Reply-to address: {settings.founder_email}\n" if settings.founder_email else "")
            + "- NEVER invent a different sender name. No 'Alex', no aliases."
        )
        super().__init__(system_extra=identity)
        self._prospect = prospect
        self._prototype_url = prototype_url
        self._walkthrough_url = walkthrough_url
        company = prospect.get("company_name") or "prospect"
        self._state = DraftState.for_prospect(company)

        self.add_tools(read_tools(prospect, prototype_url,
                                  walkthrough_url, founder_context))
        self.add_tools(action_tools(self._state, prospect=prospect,
                                    prototype_url=prototype_url,
                                    walkthrough_url=walkthrough_url))

    @property
    def state(self) -> DraftState:
        return self._state

    def draft(self, on_event: EventSink = None) -> dict[str, Any]:
        opening = (
            "Draft the outbound artifact stack for this prospect. Call the "
            "read tools first, then compose the deck outline and render it "
            "with write_pitch_deck, deploy it, compose the email, save the "
            "draft, and finalize. One clean pass — do not re-render the deck "
            "twice."
        )
        text = self.run_turn(opening, on_event=on_event)
        finalized = self._state.finalized or bool(self._state.convex_id)
        return {
            "prospect_slug": self._state.prospect_slug,
            "workspace": str(self._state.workspace),
            "campaign_id": self._state.convex_id or "",
            "email_subject": self._state.email_subject,
            "email_md_path": self._state.email_md_path or "",
            "deck_url": self._state.deck_url or "",
            "deck_pptx_path": self._state.deck_pptx_path or "",
            "prototype_url": self._prototype_url,
            "walkthrough_url": self._walkthrough_url,
            "finalized": finalized,
            "notes": text[:600] if not self._state.finalized else "",
        }
