"""The Agent base class — one LLM loop, any tool set.

Every Revenant agent is:
* a name and a role (the system prompt)
* a model (Nous Hermes-4-405B by default)
* a :class:`~agents.tools.ToolRegistry`
* a loop that: LLM → tool_calls? → execute → LLM … → final text

We call the OpenAI SDK against whatever ``LLM_BASE_URL`` points at (Nous by
default). Function calling is OpenAI-compatible, so no vendor branching.

An ``on_event`` callback is invoked whenever something interesting happens —
the CLI uses it to render tool calls and status lines. The base class is
completely UI-agnostic; ``on_event`` may be ``None``.
"""

from __future__ import annotations

import json
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from ghost.config import settings
from ghost.llm import COST

from .memory import ConversationMemory
from .tools import Tool, ToolRegistry


# Nous Hermes-4 occasionally emits tool calls as inline text
# ``<tool_call>{"name":"…","arguments":{…}}</tool_call>`` instead of using
# the OpenAI-native ``tool_calls`` field. When we see this, we synthesise a
# ``ToolCall``-shaped object so the rest of the loop is unchanged.
_INLINE_TOOL_RX = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


@dataclass
class _InlineFn:
    name: str
    arguments: str  # JSON string, as OpenAI SDK returns


@dataclass
class _InlineToolCall:
    id: str
    function: _InlineFn
    type: str = "function"


def _extract_balanced_braces(s: str, start: int = 0) -> tuple[int, int] | None:
    """Given a string and a starting index at ``{``, return (start, end)
    covering the full balanced-brace substring including the closing ``}``.
    Naive brace count — good enough because inside these tool calls we
    don't have `{` or `}` inside string literals often, and when we do,
    the LLM tends to leave them unescaped anyway (which is the whole reason
    we're here)."""
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (start, i)
    return None


def _repair_nous_args(raw: str) -> dict | None:
    """Nous Hermes-4 sometimes emits `"prospect_json": "{...}"` where the
    inner value is an unescaped object literal — invalid JSON. Also
    `prototype_url` / `walkthrough_url` / `brief` may be present as plain
    quoted strings. Extract each field surgically instead of relying on a
    strict JSON parser.
    """
    result: dict[str, Any] = {}

    # prospect_json: may be a quoted string starting with `"{`, or a bare
    # object literal starting with `{`. Extract via balanced braces either way.
    m = re.search(r'"prospect_json"\s*:\s*', raw)
    if m:
        pos = m.end()
        # Skip a leading quote if present (Nous often opens the string but
        # never closes it).
        if pos < len(raw) and raw[pos] == '"':
            pos += 1
        span = _extract_balanced_braces(raw, pos)
        if span is not None:
            b0, b1 = span
            result["prospect_json"] = raw[b0:b1 + 1]

    # Simple string args — quoted strings via regex.
    for key in ("prototype_url", "walkthrough_url", "brief", "summary"):
        km = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*?)"', raw)
        if km:
            result[key] = km.group(1)

    # Numeric args.
    for key in ("max_prospects",):
        nm = re.search(rf'"{key}"\s*:\s*(-?\d+)', raw)
        if nm:
            result[key] = int(nm.group(1))

    return result or None


def _extract_inline_tool_calls(text: str) -> list[_InlineToolCall]:
    """Parse ``<tool_call>…</tool_call>`` blocks in an assistant message.

    Two paths: (1) strict `json.loads` on the entire block; (2) if that
    fails — because Nous emitted an unescaped nested object literal for a
    string parameter — extract the tool name via regex and the arguments
    via ``_repair_nous_args``. Returns objects with the same shape as the
    OpenAI SDK's real tool-call objects.
    """
    if not text or "<tool_call>" not in text:
        return []
    calls: list[_InlineToolCall] = []
    for i, block in enumerate(_INLINE_TOOL_RX.findall(text)):
        name: str = ""
        args_str: str = "{}"

        # Path 1 — strict parse.
        try:
            data = json.loads(block)
            name = data.get("name") or ""
            if not name:
                continue
            args = data.get("arguments", data.get("parameters", {}))
            args_str = args if isinstance(args, str) else json.dumps(args)
        except json.JSONDecodeError:
            # Path 2 — Nous quirk repair. Name via regex, args via surgical
            # extraction that survives unescaped inner braces/quotes.
            name_m = re.search(r'"name"\s*:\s*"([^"]+)"', block)
            if not name_m:
                continue
            name = name_m.group(1)

            args_m = re.search(r'"arguments"\s*:\s*', block)
            if not args_m:
                continue
            # Args value should start at the next `{`.
            after = args_m.end()
            while after < len(block) and block[after] in ' \n\t':
                after += 1
            if after >= len(block) or block[after] != "{":
                continue
            repaired = _repair_nous_args(block[after:])
            if repaired is None:
                continue
            args_str = json.dumps(repaired)

        calls.append(_InlineToolCall(
            id=f"inline_{i}_{uuid.uuid4().hex[:8]}",
            function=_InlineFn(name=name, arguments=args_str),
        ))
    return calls


def _strip_inline_tool_calls(text: str) -> str:
    """Remove the ``<tool_call>…</tool_call>`` blocks from an assistant
    message so we don't record them twice into the conversation."""
    return _INLINE_TOOL_RX.sub("", text or "").strip()


# ── event types (for the CLI to render) ────────────────────────
@dataclass
class AgentEvent:
    kind: str          # "think" | "tool_call" | "tool_result" | "final" | "error"
    agent: str
    text: str = ""
    tool: str = ""
    args: dict[str, Any] | None = None
    result: str = ""


EventSink = Callable[[AgentEvent], None] | None


# ── nested delegation: a contextvar so sub-agents inherit the sink ─
_current_sink: ContextVar[EventSink] = ContextVar("_current_sink", default=None)

# The Orchestrator's founder context — set by Orchestrator.bind_context /
# __init__; read by delegation tools that need to pass it to sub-agents
# (e.g. Engineer needs to read the founder's docs).
_current_founder_ctx: ContextVar[Any] = ContextVar("_current_founder_ctx", default=None)


def current_sink() -> EventSink:
    """The event sink of the outermost Agent.run_turn() currently on the stack.

    A tool that spawns a sub-agent should pass this to the sub-agent's
    ``run_turn`` — that's how the founder sees nested Research/Engineer/…
    tool calls stream into the same terminal.
    """
    return _current_sink.get()


def current_founder_ctx() -> Any:
    """The FounderContext the Orchestrator is currently bound to, or None."""
    return _current_founder_ctx.get()


def set_founder_ctx(ctx: Any) -> None:
    """Called by Orchestrator when a context is attached."""
    _current_founder_ctx.set(ctx)


# ── Agent ──────────────────────────────────────────────────────
class Agent:
    """Base class — subclass to declare a persistent role.

    Subclasses override ``name``, ``system``, ``tools``, ``model``.
    ``run_turn`` handles the loop generically. Instance-level tool/system
    additions are supported so the Orchestrator can bolt on founder-context
    tools without editing the subclass.
    """

    name: str = "agent"
    system: str = "You are a helpful assistant."
    tools: list[Tool] = []
    model: str | None = None       # None → use settings.llm_model
    max_iters: int = 10
    temperature: float = 0.4
    # When True the agent uses settings.strong_* (OpenAI gpt-4o by default)
    # instead of the default Nous Hermes-4 route. Reserve for quality-
    # critical agents (Sales copy, Research fit reasoning).
    use_strong_model: bool = False

    def __init__(
        self,
        *,
        system_extra: str = "",
        extra_tools: list[Tool] | None = None,
    ) -> None:
        sys_full = self.system if not system_extra else f"{self.system}\n\n{system_extra}"
        self._system = sys_full
        self._registry = ToolRegistry(list(self.tools) + list(extra_tools or []))
        self._memory = ConversationMemory(sys_full)

    # ── public API ────────────────────────────────────────────
    @property
    def memory(self) -> ConversationMemory:
        return self._memory

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def add_tools(self, extra: list[Tool]) -> None:
        self._registry.extend(extra)

    def reset(self) -> None:
        self._memory.reset()

    def run_turn(self, user_message: str, on_event: EventSink = None) -> str:
        """Run one conversational turn to completion. Returns the final text.

        The ``on_event`` sink is stashed in a contextvar so any tool that
        spawns a sub-agent can grab it via :func:`current_sink` — that keeps
        nested tool calls streaming to the same terminal the founder is
        watching.
        """
        self._memory.add_user(user_message)
        # Only the outermost run sets the contextvar; nested run_turns inherit.
        if _current_sink.get() is None and on_event is not None:
            token = _current_sink.set(on_event)
            try:
                return self._loop(on_event)
            finally:
                _current_sink.reset(token)
        return self._loop(on_event)

    # ── the loop ──────────────────────────────────────────────
    def _loop(self, on_event: EventSink) -> str:
        for _ in range(self.max_iters):
            try:
                message = self._llm_step()
            except Exception as exc:
                err = f"[llm-error] {exc.__class__.__name__}: {exc}"
                _emit(on_event, AgentEvent(kind="error", agent=self.name, text=err))
                return err

            tool_calls = getattr(message, "tool_calls", None) or []
            content = message.content or ""

            # Nous fallback: if the model emitted inline <tool_call> tags in
            # the message text instead of using the native tool_calls field,
            # parse them here so the loop keeps working. Strip the tags from
            # the recorded content so we don't confuse the next turn.
            if not tool_calls and content:
                inline = _extract_inline_tool_calls(content)
                if inline:
                    tool_calls = inline
                    content = _strip_inline_tool_calls(content)

            if not tool_calls:
                self._memory.add_assistant(content)
                _emit(on_event, AgentEvent(kind="final", agent=self.name, text=content))
                return content

            # The assistant asked for tools — record + execute.
            self._memory.add_assistant_tool_calls(content, tool_calls)
            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args_dict = json.loads(raw_args)
                except json.JSONDecodeError:
                    args_dict = {"_raw": raw_args}
                _emit(on_event, AgentEvent(
                    kind="tool_call", agent=self.name, tool=name, args=args_dict,
                ))

                tool = self._registry.get(name)
                if tool is None:
                    result = f"[tool-error] no such tool: {name}"
                else:
                    result = tool.call(raw_args)

                self._memory.add_tool_result(tc.id, name, result)
                _emit(on_event, AgentEvent(
                    kind="tool_result", agent=self.name, tool=name, result=result,
                ))

        # ran out of iterations
        fallback = "[agent-error] max iterations reached without a final answer"
        self._memory.add_assistant(fallback)
        _emit(on_event, AgentEvent(kind="error", agent=self.name, text=fallback))
        return fallback

    # ── LLM call ──────────────────────────────────────────────
    _LLM_RETRIES = 2          # extra attempts after the first
    _RETRY_BACKOFF_S = (1.5, 4.0)

    def _llm_step(self) -> Any:
        """One raw call to the LLM with retry — a venue-wifi blip or a 429
        must not kill a 4-minute autopilot chain. Returns the assistant
        message object."""
        # Strong-model agents (Sales) run on OpenAI gpt-4o for copy quality;
        # everyone else defaults to the shared Nous Hermes-4 route.
        if self.use_strong_model and (settings.strong_api_key
                                      or settings.openai_api_key):
            model = self.model or settings.strong_model
            base_url = settings.strong_base_url
            api_key = settings.strong_api_key or settings.openai_api_key
        else:
            model = self.model or settings.llm_model
            base_url = settings.llm_base_url
            api_key = settings.llm_api_key

        # Chat agents always run live. Offline mode is a pipeline-testing
        # affordance; a chat with no LLM is not a chat.
        if not api_key:
            raise RuntimeError(
                "No LLM_API_KEY / OPENAI_API_KEY in env — agent chat requires a live model."
            )

        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=180.0)
        tool_schemas = self._registry.schemas()

        # gpt-5 / o-series are reasoning models: they reject a custom
        # `temperature` (default only) and reason internally before emitting
        # output, so they need a much larger completion budget. Detect and
        # adapt so an agent can be pointed at gpt-5-mini without 400s.
        _reasoning = model.startswith(("gpt-5", "o1", "o3", "o4"))
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._memory.messages(),
        }
        if _reasoning:
            kwargs["max_completion_tokens"] = 16000  # room for reasoning + long HTML
            # Optional per-agent effort control: reasoning="low" cuts thinking
            # tokens drastically (30-60s → 10-20s on long-HTML authoring).
            # Agents can set `reasoning_effort = "low"|"medium"|"high"` as a
            # class attr, or override via env for demo tuning.
            import os as _os
            _eff = _os.getenv("REVENANT_REASONING_EFFORT") or getattr(
                self, "reasoning_effort", None)
            if _eff:
                kwargs["reasoning_effort"] = _eff
        else:
            kwargs["temperature"] = self.temperature
        if tool_schemas:
            kwargs["tools"] = tool_schemas
            kwargs["tool_choice"] = "auto"

        import time as _time
        last_exc: Exception | None = None
        for attempt in range(1 + self._LLM_RETRIES):
            try:
                resp = client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # httpx timeouts, 429s, 5xx — all retryable
                last_exc = exc
                if attempt >= self._LLM_RETRIES:
                    raise
                _time.sleep(self._RETRY_BACKOFF_S[min(attempt, len(self._RETRY_BACKOFF_S) - 1)])
        else:  # pragma: no cover
            raise last_exc or RuntimeError("llm call failed")

        msg = resp.choices[0].message

        # crude cost telemetry
        usage = getattr(resp, "usage", None)
        n_in = getattr(usage, "prompt_tokens", 0)
        n_out = getattr(usage, "completion_tokens", 0)
        COST.record(self.name, model, n_in, n_out)
        return msg


# Global sinks — receive EVERY agent event regardless of which sink the
# caller passed. Used by the Convex live bridge to mirror runs into the
# deployed console. Register with register_global_sink().
_GLOBAL_SINKS: list[Callable[[AgentEvent], None]] = []


def register_global_sink(fn: Callable[[AgentEvent], None]) -> None:
    if fn not in _GLOBAL_SINKS:
        _GLOBAL_SINKS.append(fn)


def _emit(sink: EventSink, ev: AgentEvent) -> None:
    for gs in _GLOBAL_SINKS:
        try:
            gs(ev)
        except Exception:  # pragma: no cover
            pass
    if sink is not None:
        try:
            sink(ev)
        except Exception:  # pragma: no cover - a broken sink must not kill the agent
            pass
