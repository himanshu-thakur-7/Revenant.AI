"""Orchestrator — Agent 0.

The founder's chat partner. Loaded with the startup context and a set of tools
that let it (a) read the founder's own repo and (b) delegate work to
specialist sub-agents. Delegation tools are stubs until each sub-agent lands.
"""

from __future__ import annotations

from ..base import Agent, set_founder_ctx
from ..bridge import bridge  # noqa: F401  (self-registers the Convex live mirror)
from ..context import FounderContext
from .prompt import BASE_SYSTEM, build_system_prompt
from .tools import context_tools, delegation_stubs


class Orchestrator(Agent):
    name = "orchestrator"
    system = BASE_SYSTEM  # replaced per-instance once a context is bound
    tools: list = []      # bound at __init__ so we can inject ctx-specific tools
    # Autopilot needs elbow room: research + engineer + director + sales are
    # each one tool call; add slack for context-tool lookups + retries.
    max_iters = 24

    def __init__(self, context: FounderContext | None = None) -> None:
        super().__init__()
        self._context = context
        set_founder_ctx(context)
        if context is not None:
            self.bind_context(context)
        else:
            # No context yet — still give it delegation stubs. The founder can
            # attach a repo later via /context in the REPL.
            self.add_tools(delegation_stubs())

    def bind_context(self, context: FounderContext) -> None:
        """Attach (or replace) the founder's startup context."""
        self._context = context
        set_founder_ctx(context)
        briefing = context.summary()
        self.memory.set_system(build_system_prompt(briefing, context.source))
        # rebuild tool registry with context tools first, then delegation
        self._registry = type(self._registry)()  # fresh
        self.add_tools(context_tools(context))
        self.add_tools(delegation_stubs())

    @property
    def context(self) -> FounderContext | None:
        return self._context
