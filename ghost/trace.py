"""ghost/trace.py — the tracing shim. See docs/evals-observability-design.md §2.

Pluggable backend behind four functions: span(), record_usage(), score(),
flush(). Default backend is `jsonl` (writes out/traces/<date>.jsonl, zero
external dependency, works today); a `langfuse` backend can be added
later behind the same interface once LANGFUSE_PUBLIC_KEY/SECRET_KEY exist
(see REVENANT_TRACE_BACKEND).

THREE HARD RULES, because agents/mcp_server.py runs this as a stdio MCP
server (agents/mcp_server.py:1064 mcp.run()) and this module is on nearly
every hot path in the codebase:
  1. NEVER write to stdout. A stray print corrupts the MCP framing and
     kills the server. Everything goes to stderr or a file.
  2. NEVER raise. Every public function is wrapped in try/except Exception:
     pass — mirrors the existing _emit() pattern at agents/base.py:441.
     Tracing must not be able to break a build.
  3. NEVER block meaningfully. The jsonl backend appends under a lock;
     writes are small and local.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


TRACE_ENABLED = _flag("REVENANT_TRACE", True)
TRACE_BACKEND = os.getenv("REVENANT_TRACE_BACKEND", "jsonl").strip().lower()
TRACE_DIR = Path(os.getenv("REVENANT_TRACE_DIR", str(REPO_ROOT / "out" / "traces")))
_MAX_IO_BYTES = 20_000


def _git_sha() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


_GIT_SHA = _git_sha()          # computed once at import, not per-span
_GIT_DIRTY = _git_dirty()
_RELEASE = os.getenv("REVENANT_RELEASE") or _GIT_SHA
_RUN_MODE = os.getenv("REVENANT_MODE", "offline")
# 'mcp' when running as the stdio MCP server (argv[0] check is fragile
# across invocation styles, so this is a best-effort label, not load-bearing)
_ENV_LABEL = "mcp" if "mcp_server" in (sys.argv[0] if sys.argv else "") else "local"

_lock = threading.Lock()
_local = threading.local()   # per-thread span stack, for parent_span_id


def _truncate(v: Any) -> Any:
    try:
        s = v if isinstance(v, str) else json.dumps(v, default=str)
    except Exception:
        s = str(v)
    if len(s) > _MAX_IO_BYTES:
        return s[:_MAX_IO_BYTES] + f"...<truncated {len(s) - _MAX_IO_BYTES} bytes>"
    return s


def _stack() -> list[dict[str, Any]]:
    if not hasattr(_local, "stack"):
        _local.stack = []
    return _local.stack


def current_trace_id() -> str | None:
    try:
        stack = _stack()
        return stack[0]["trace_id"] if stack else None
    except Exception:
        return None


def current_span_id() -> str | None:
    try:
        stack = _stack()
        return stack[-1]["span_id"] if stack else None
    except Exception:
        return None


def _write_jsonl(record: dict[str, Any]) -> None:
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        fname = time.strftime("%Y-%m-%d") + ".jsonl"
        with _lock, (TRACE_DIR / fname).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def _emit(record: dict[str, Any]) -> None:
    if not TRACE_ENABLED or TRACE_BACKEND == "none":
        return
    if TRACE_BACKEND == "langfuse":
        # Not implemented yet — needs LANGFUSE_PUBLIC_KEY/SECRET_KEY, task
        # 9 in docs/evals-observability-design.md. Fall through to jsonl
        # so tracing degrades rather than silently vanishing if someone
        # sets the backend before the keys/implementation exist.
        _write_jsonl(record)
        return
    _write_jsonl(record)


@contextlib.contextmanager
def span(name: str, *, kind: str, **attrs: Any) -> Iterator[dict[str, Any]]:
    """kind: llm | tool | agent | mcp | pipeline | tts | llm.vision |
    llm.wrapper. Nests via the current thread's span stack — a span opened
    inside another span's `with` block is its child. Yields a mutable dict
    the caller can add to before the span closes (record_usage/record_io
    below are the typical way; direct dict mutation also works)."""
    stack = _stack()
    trace_id = stack[0]["trace_id"] if stack else uuid.uuid4().hex[:16]
    parent_id = stack[-1]["span_id"] if stack else None
    span_id = uuid.uuid4().hex[:16]
    rec: dict[str, Any] = {
        "trace_id": trace_id, "span_id": span_id, "parent_span_id": parent_id,
        "name": name, "kind": kind, "started_at": time.time(),
        "attrs": dict(attrs),
        "revenant.git_sha": _GIT_SHA, "revenant.git_dirty": _GIT_DIRTY,
        "revenant.release": _RELEASE, "revenant.mode": _RUN_MODE, "revenant.env": _ENV_LABEL,
        "events": [], "usage": None, "io": None, "error": None,
    }
    stack.append(rec)
    t0 = time.monotonic()
    try:
        yield rec
    except Exception as exc:  # noqa: BLE001 — record, then re-raise; tracing never SWALLOWS a real error
        try:
            rec["error"] = repr(exc)
        except Exception:
            pass
        raise
    finally:
        try:
            rec["duration_s"] = round(time.monotonic() - t0, 3)
            rec["ended_at"] = time.time()
            stack.pop()
            _emit(rec)
        except Exception:
            pass


def record_usage(model: str, n_in: int, n_out: int, *, agent: str = "unknown") -> None:
    try:
        stack = _stack()
        if not stack:
            return
        stack[-1]["usage"] = {"model": model, "input_tokens": n_in, "output_tokens": n_out,
                              "agent": agent}
    except Exception:
        pass


def record_io(inputs: Any = None, outputs: Any = None) -> None:
    try:
        stack = _stack()
        if not stack:
            return
        io: dict[str, Any] = {}
        if inputs is not None:
            io["input"] = _truncate(inputs)
        if outputs is not None:
            io["output"] = _truncate(outputs)
        stack[-1]["io"] = io
    except Exception:
        pass


def event(name: str, **attrs: Any) -> None:
    """A point-in-time note inside the current span — e.g. 'strong_fallback',
    'planner_returned_empty', 'json_parse_failed' — the silent-degradation
    paths this session found are exactly what these mark."""
    try:
        stack = _stack()
        if not stack:
            return
        stack[-1]["events"].append({"name": name, "at": time.time(), **attrs})
    except Exception:
        pass


def prompt_fingerprint(text: str) -> str:
    import hashlib
    try:
        return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:12]
    except Exception:
        return "unknown"


def score(name: str, value: float, *, comment: str = "", trace_id: str | None = None) -> None:
    """Attach an eval score to a trace (own trace if in one, or an
    explicit trace_id for scoring after the fact — e.g. evals/runner.py
    scoring a bundle recorded minutes earlier)."""
    try:
        tid = trace_id or current_trace_id()
        _emit({"kind": "score", "trace_id": tid, "name": name, "value": value,
              "comment": comment, "at": time.time(),
              "revenant.git_sha": _GIT_SHA, "revenant.release": _RELEASE})
    except Exception:
        pass


def capture_context() -> dict[str, str]:
    """Call from the thread that holds the current span context (e.g. the
    asyncio event-loop thread inside an @mcp.tool()) BEFORE spawning a
    worker thread. Returns a token for propagate_into() to use inside
    that worker thread. Empty dict if there's no open span to capture."""
    try:
        stack = _stack()
        if not stack:
            return {}
        return {"trace_id": stack[0]["trace_id"], "parent_span_id": stack[-1]["span_id"]}
    except Exception:
        return {}


@contextlib.contextmanager
def propagate_into(ctx: dict[str, str]) -> Iterator[None]:
    """Call from INSIDE a worker thread (e.g. the target of
    anyio.to_thread.run_sync) to adopt a trace/span context captured via
    capture_context() in a DIFFERENT thread.

    Needed because threading.local() — what the span stack is built on —
    by design does not share state across threads. agents/mcp_server.py's
    build_prototype/film_walkthrough/draft_outreach/build_full_outreach
    all run their real work via anyio.to_thread.run_sync(), a genuinely
    different OS thread from the one that opened the @mcp.tool() root
    span. Caught live: without this, every span opened inside that worker
    thread (planner, every agent.llm_step, ...) started its own brand-new
    root trace with parent_span_id=None — the exact correlation this
    tracing effort exists to provide was silently missing even though
    every individual span recorded correctly.
    """
    if not ctx:
        yield
        return
    stack = _stack()
    # Not a real span (never emitted) — a placeholder so span() below
    # inherits trace_id from ctx and treats ctx['parent_span_id'] as ITS
    # parent, then span() pops its own entry and this one remains for any
    # sibling span in the same thread call, popped in `finally` below.
    placeholder = {"trace_id": ctx["trace_id"], "span_id": ctx["parent_span_id"]}
    stack.append(placeholder)
    try:
        yield
    finally:
        try:
            stack.remove(placeholder)
        except ValueError:
            pass


def traced_tool(name: str | None = None, *, kind: str = "mcp"):
    """Decorator for async functions — opens a root span for the call
    duration, no reindentation of the wrapped function needed. Used for
    agents/mcp_server.py's @mcp.tool() functions, which are large enough
    (each is the whole build_prototype/film_walkthrough/... body) that
    wrapping their bodies in `with span():` directly would mean
    reindenting 50-150 lines per function — this decorator gets the same
    span tree with a single added line per tool."""
    def deco(fn):
        import asyncio
        import functools

        span_name = name or fn.__name__

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                # Log the call's own kwargs as attrs (startup/merchant/etc
                # are exactly what you want visible without opening the
                # trace) — skip mcp_ctx, not JSON-serializable, not useful.
                attrs = {k: v for k, v in kwargs.items() if k != "mcp_ctx"}
                with span(span_name, kind=kind, **attrs):
                    result = await fn(*args, **kwargs)
                    record_io(outputs=result)
                    return result
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            attrs = {k: v for k, v in kwargs.items() if k != "mcp_ctx"}
            with span(span_name, kind=kind, **attrs):
                result = fn(*args, **kwargs)
                record_io(outputs=result)
                return result
        return sync_wrapper
    return deco


def flush() -> None:
    """No-op for the jsonl backend (writes are synchronous); real work
    once a batching backend (langfuse) exists. Call at process exit and
    at the end of any CLI entry point."""
    pass


import atexit  # noqa: E402
atexit.register(flush)
