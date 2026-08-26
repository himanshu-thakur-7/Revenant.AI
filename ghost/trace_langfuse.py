"""Langfuse backend for ghost/trace.py — implements the same shim
interface (span/record_usage/record_io/event/score/flush) against a real
Langfuse project, activated via REVENANT_TRACE_BACKEND=langfuse.

NOT LIVE-TESTED — this session had no LANGFUSE_PUBLIC_KEY/SECRET_KEY to
test against (see docs/evals-observability-design.md's founder-supplied
items). Written against the real installed SDK (langfuse==4.14.5, an
OTEL-based API meaningfully different from the v3 API the design doc
assumed — introspected directly via `inspect.signature`, not guessed from
memory) and exercises its actual code paths in OFFLINE mode (no network),
but the "does a real trace show up correctly in the Langfuse UI" question
is unverified. Flagging that boundary explicitly rather than implying
more confidence than earned.

Design note on nesting: langfuse v4's own span objects have their own
start_observation() method that nests automatically WHEN CALLED ON THE
PARENT OBJECT — this backend uses that (via a span_id -> LangfuseSpan
object map) rather than relying on langfuse's OTEL context-var auto-
propagation, because that has the exact same cross-thread gap ghost/
trace.py's jsonl backend already hit and fixed (contextvars, like
threading.local(), don't cross an anyio.to_thread.run_sync() boundary
without explicit propagation) — building on top of a mechanism with the
same known failure mode isn't worth it when explicit parent objects are
simpler and already proven not to have that gap.

Timestamp note: ghost/trace.py's span() emits a FULLY-FORMED record only
after the span has already closed (jsonl backend just appends a line).
Langfuse's start_observation()/generation "live" API doesn't accept
backfilled start/end timestamps the same way — this backend creates and
immediately ends each observation at emit() time, so Langfuse's own
displayed start/end will read as "when the batch was flushed", not "when
the real work happened". The REAL timing (started_at/ended_at/duration_s)
is preserved in metadata so it's not lost, just not what Langfuse's
timeline UI shows. A genuinely live (non-batched) integration would need
ghost/trace.py's span() itself restructured to open the Langfuse
observation at span-start and close it at span-end, not at emit() time —
a real follow-up, not done here.
"""

from __future__ import annotations

import os
import threading
from typing import Any

_client = None
_client_lock = threading.Lock()
# my span_id -> the LangfuseSpan object, so a child span can nest onto its
# parent by calling parent_obj.start_observation(...) directly.
_span_objects: dict[str, Any] = {}
# my trace_id -> a Langfuse-valid (32 hex char) trace id, generated once
# per trace and reused for every span in it.
_trace_id_map: dict[str, str] = {}


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            from langfuse import Langfuse
            _client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
        except Exception:
            _client = False  # sentinel: tried and failed, don't retry every call
    return _client or None


def _langfuse_trace_id(my_trace_id: str) -> str:
    """Langfuse/OTEL trace ids must be 32 lowercase hex chars; mine are a
    16-char uuid4 truncation. Derive deterministically so the same my_trace_id
    always maps to the same Langfuse trace id (needed so root + child spans
    emitted in separate _emit() calls land in the same Langfuse trace)."""
    if my_trace_id in _trace_id_map:
        return _trace_id_map[my_trace_id]
    import hashlib
    lf_id = hashlib.sha256(my_trace_id.encode()).hexdigest()[:32]
    _trace_id_map[my_trace_id] = lf_id
    return lf_id


_KIND_TO_AS_TYPE = {
    "llm": "generation", "llm.vision": "generation", "llm.wrapper": "span",
    "tool": "tool", "agent": "agent", "mcp": "span", "pipeline": "span", "tts": "span",
}


def emit(record: dict[str, Any]) -> None:
    """Called by ghost/trace.py's _emit() for both span records (has
    span_id) and score records (kind == 'score'). Never raises — mirrors
    every other function in this codebase that touches tracing."""
    client = _get_client()
    if not client:
        return
    try:
        if record.get("kind") == "score":
            client.create_score(
                name=record["name"], value=record["value"],
                trace_id=_langfuse_trace_id(record["trace_id"]) if record.get("trace_id") else None,
                comment=record.get("comment") or None,
            )
            return

        my_trace_id = record["trace_id"]
        my_span_id = record["span_id"]
        my_parent_id = record.get("parent_span_id")
        lf_trace_id = _langfuse_trace_id(my_trace_id)
        as_type = _KIND_TO_AS_TYPE.get(record.get("kind", ""), "span")

        usage = record.get("usage") or {}
        usage_details = None
        if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
            usage_details = {"input": usage.get("input_tokens", 0),
                            "output": usage.get("output_tokens", 0)}

        io = record.get("io") or {}
        metadata = {
            "revenant.git_sha": record.get("revenant.git_sha"),
            "revenant.git_dirty": record.get("revenant.git_dirty"),
            "revenant.release": record.get("revenant.release"),
            "revenant.mode": record.get("revenant.mode"),
            "revenant.env": record.get("revenant.env"),
            "started_at": record.get("started_at"),
            "ended_at": record.get("ended_at"),
            "duration_s": record.get("duration_s"),
            "events": record.get("events"),
            "prompt_sha": (record.get("attrs") or {}).get("prompt_sha"),
        }

        parent_obj = _span_objects.get(my_parent_id) if my_parent_id else None
        kwargs = dict(
            name=record.get("name", "span"), as_type=as_type,
            input=io.get("input"), output=io.get("output"),
            metadata={**(record.get("attrs") or {}), **metadata},
            model=usage.get("model") if as_type == "generation" else None,
            usage_details=usage_details if as_type == "generation" else None,
        )
        if parent_obj is not None:
            obj = parent_obj.start_observation(**kwargs)
        else:
            obj = client.start_observation(
                trace_context={"trace_id": lf_trace_id}, **kwargs)

        if record.get("error"):
            obj.update(level="ERROR", status_message=str(record["error"])[:500])
        obj.end()
        _span_objects[my_span_id] = obj
    except Exception:
        pass


def flush() -> None:
    client = _get_client()
    if client:
        try:
            client.flush()
        except Exception:
            pass
