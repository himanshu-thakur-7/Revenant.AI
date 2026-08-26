"""LLM router with cost logging.

Every model call in the codebase goes through :func:`complete` or
:func:`complete_json` — no agent calls an SDK directly (master-plan
non-negotiable). This is where offline mode, cost accounting, and a single
retry live. In offline mode the router returns a caller-supplied ``offline``
value, so the whole pipeline runs deterministically with zero network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .log import log

# Rough blended $/1M tokens for cost telemetry. NOT verified against a
# published price list (gpt-5.6-* isn't a public OpenAI model id — these
# are plausible placeholders, not billing data) — this table only feeds
# the in-app "cost per touch" estimate. Langfuse (once wired, see
# ghost/trace.py + docs/evals-observability-design.md §2.5) is the
# authority on ACTUAL cost from real usage+model. Added these entries
# because every real call in this codebase (gpt-4.1, gpt-5.6-*, gpt-5-mini)
# previously priced at the 'default' fallback — approximate is still
# strictly better than that, but treat these numbers as rough, not exact.
_PRICE_PER_MTOK = {
    "gpt-4o": 5.0,
    "gpt-4o-mini": 0.6,
    "gpt-4.1": 4.0,
    "gpt-4.1-mini": 0.8,
    "gpt-5-mini": 0.6,
    "gpt-5.6-luna": 3.0,
    "gpt-5.6-sol": 3.0,
    "gpt-5.6-terra": 3.0,
    "default": 1.0,
}


@dataclass
class CostLog:
    """Accumulates spend across a run for the unit-economics panel."""

    calls: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    cents: float = 0.0
    by_agent: dict[str, float] = field(default_factory=dict)

    def record(self, agent: str, model: str, n_in: int, n_out: int) -> None:
        rate = _PRICE_PER_MTOK.get(model, _PRICE_PER_MTOK["default"])
        cost = (n_in + n_out) / 1_000_000 * rate * 100  # cents
        self.calls += 1
        self.in_tokens += n_in
        self.out_tokens += n_out
        self.cents += cost
        self.by_agent[agent] = self.by_agent.get(agent, 0.0) + cost
        # Single-point bridge to the trace shim — every COST.record() call
        # site (including the two offline-stub paths in complete/
        # complete_strong that record cost with no LLM call) becomes
        # visible on whatever span is currently open, with zero extra
        # instrumentation at each call site.
        from . import trace
        trace.record_usage(model, n_in, n_out, agent=agent)


COST = CostLog()


def _estimate_tokens(text: str) -> int:
    # ~4 chars/token heuristic; good enough for telemetry.
    return max(1, len(text) // 4)


def _client():
    from openai import OpenAI

    return OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)


def _strong_client():
    from openai import OpenAI

    return OpenAI(base_url=settings.strong_base_url,
                  api_key=settings.strong_api_key or settings.openai_api_key)


def complete_strong(prompt: str, *, agent: str = "unknown",
                    system: str | None = None, offline: str = "",
                    temperature: float = 0.4, model: str | None = None) -> str:
    """Free-text completion via the *stronger* model (OpenAI gpt-4o by default).

    Use for quality-critical steps where Nous Hermes-4's flakiness costs more
    than the extra tokens: research fit-reasoning, outreach email drafting."""
    from . import trace

    model = model or settings.strong_model
    key = settings.strong_api_key or settings.openai_api_key
    with trace.span("llm.complete_strong", kind="llm", agent=agent, model=model,
                    prompt_sha=trace.prompt_fingerprint((system or "") + prompt)):
        if settings.offline or not key:
            COST.record(agent, model, _estimate_tokens(prompt), _estimate_tokens(offline))
            trace.event("offline_stub")
            log.dim(f"[llm:{agent}:strong] offline stub ({len(offline)} chars)")
            return offline

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # gpt-5 / o-series are reasoning models: they reject a custom temperature
        # and reason internally, so they need a large completion budget instead.
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if model.startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["max_completion_tokens"] = 16000
        else:
            kwargs["temperature"] = temperature

        try:
            resp = _strong_client().chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            n_in = getattr(usage, "prompt_tokens", _estimate_tokens(prompt))
            n_out = getattr(usage, "completion_tokens", _estimate_tokens(text))
            COST.record(agent, model, n_in, n_out)
            trace.record_io(inputs=prompt, outputs=text)
            return text
        except Exception as exc:  # pragma: no cover
            trace.event("strong_fallback", error=repr(exc))
            log.warn(f"[llm:{agent}:strong] error {exc!r}; falling back to weak model")
            return complete(prompt, agent=agent, system=system, offline=offline,
                            temperature=temperature)


def complete_strong_json(prompt: str, *, agent: str = "unknown",
                         system: str | None = None,
                         offline: dict[str, Any] | None = None) -> dict[str, Any]:
    """JSON completion via the stronger model. Same contract as complete_json."""
    from . import trace

    offline = offline or {}
    with trace.span("llm.complete_strong_json", kind="llm.wrapper", agent=agent):
        if settings.offline or not (settings.strong_api_key or settings.openai_api_key):
            COST.record(agent, settings.strong_model,
                        _estimate_tokens(prompt),
                        _estimate_tokens(json.dumps(offline)))
            return dict(offline)

        sys = (system or "") + "\nRespond with a single valid JSON object and nothing else."
        raw = complete_strong(prompt, agent=agent, system=sys,
                              offline=json.dumps(offline))
        try:
            start, end = raw.find("{"), raw.rfind("}")
            return json.loads(raw[start:end + 1]) if start >= 0 else dict(offline)
        except (json.JSONDecodeError, ValueError):
            trace.event("json_parse_failed", raw_preview=raw[:200])
            log.warn(f"[llm:{agent}:strong] JSON parse failed; using offline stub")
            return dict(offline)


def complete(
    prompt: str,
    *,
    agent: str = "unknown",
    system: str | None = None,
    offline: str = "",
    model: str | None = None,
    temperature: float = 0.4,
) -> str:
    """Free-text completion. Returns ``offline`` verbatim in offline mode."""
    from . import trace

    model = model or settings.llm_model
    with trace.span("llm.complete", kind="llm", agent=agent, model=model,
                    prompt_sha=trace.prompt_fingerprint((system or "") + prompt)):
        if settings.offline or not settings.llm_api_key:
            COST.record(agent, model, _estimate_tokens(prompt), _estimate_tokens(offline))
            trace.event("offline_stub")
            log.dim(f"[llm:{agent}] offline stub ({len(offline)} chars)")
            return offline

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Same reasoning-model guard as complete_strong(): gpt-5 / o-series reject
        # a custom temperature outright (400 Unsupported value). This matters here
        # more than there — llm_model falls back to Hermes's own configured
        # default (~/.hermes/config.yaml) when LLM_MODEL isn't set, so it silently
        # becomes a reasoning model (e.g. gpt-5.6-sol) whenever the founder hasn't
        # set up a dedicated Revenant .env. Without this, every complete() call —
        # headlines, pain quotes, most of the deterministic pipeline's copy —
        # 400s and quietly degrades to the offline stub in "live" mode.
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if model.startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["max_completion_tokens"] = 16000
        else:
            kwargs["temperature"] = temperature

        try:
            resp = _client().chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            n_in = getattr(usage, "prompt_tokens", _estimate_tokens(prompt))
            n_out = getattr(usage, "completion_tokens", _estimate_tokens(text))
            COST.record(agent, model, n_in, n_out)
            trace.record_io(inputs=prompt, outputs=text)
            return text
        except Exception as exc:  # pragma: no cover - network path
            trace.event("llm_error", error=repr(exc))
            log.warn(f"[llm:{agent}] error {exc!r}; falling back to offline stub")
            COST.record(agent, model, _estimate_tokens(prompt), _estimate_tokens(offline))
        return offline


def complete_json(
    prompt: str,
    *,
    agent: str = "unknown",
    system: str | None = None,
    offline: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """JSON completion with a hard structured-output contract. In offline mode
    (or on any parse failure) returns ``offline``."""
    from . import trace

    offline = offline or {}
    with trace.span("llm.complete_json", kind="llm.wrapper", agent=agent):
        if settings.offline or not settings.llm_api_key:
            model = model or settings.llm_model
            COST.record(agent, model, _estimate_tokens(prompt), _estimate_tokens(json.dumps(offline)))
            return dict(offline)

        sys = (system or "") + "\nRespond with a single valid JSON object and nothing else."
        raw = complete(prompt, agent=agent, system=sys, offline=json.dumps(offline), model=model)
        try:
            start, end = raw.find("{"), raw.rfind("}")
            return json.loads(raw[start : end + 1]) if start >= 0 else dict(offline)
        except (json.JSONDecodeError, ValueError):
            trace.event("json_parse_failed", raw_preview=raw[:200])
            log.warn(f"[llm:{agent}] JSON parse failed; using offline stub")
            return dict(offline)
