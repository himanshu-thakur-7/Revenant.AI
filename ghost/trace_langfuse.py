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

import contextlib
import json
import os
import re
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


# Redaction for the "sensitive data masked" baseline requirement. Revenant's
# spans carry real prospect contact details and real founder source code, and
# span io is truncated-but-verbatim — so without this, live traces would ship
# a named decision-maker's work email to a third-party service.
#
# Deliberately conservative and structural (regex over the serialized payload)
# rather than field-aware: a field-aware masker only redacts the keys someone
# remembered to list, and this codebase's whole lesson has been that the
# dangerous case is the one nobody remembered.
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_APIKEY_RX = re.compile(r"\b(?:sk|pk|rzp|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b")
# Note the optional `bearer` after the separator. Without it, the header
# shape `Authorization: Bearer <token>` matched only through the word
# "Bearer" (it satisfied the trailing \S+), and the actual token survived
# masking in the clear — caught by
# ghost/tests/test_trace_langfuse.py::test_masks_bearer_and_authorization_pairs.
_BEARER_RX = re.compile(
    r"(?i)\b(bearer|authorization|api[_-]?key|secret|token)\b"
    r"[\"']?\s*[:=]?\s*(?:bearer\s+)?[\"']?\S+"
)


def _mask(data: Any) -> Any:
    """Langfuse calls this on every input/output/metadata payload before it
    leaves the process. Must never raise: an exception here would drop the
    observation, and losing a trace is a worse outcome than an unmasked one
    being caught by the next layer — so a failure returns a hard-redacted
    placeholder rather than the original."""
    try:
        if isinstance(data, str):
            s = data
        else:
            s = json.dumps(data, default=str)
        s = _EMAIL_RX.sub("<email-redacted>", s)
        s = _APIKEY_RX.sub("<key-redacted>", s)
        s = _BEARER_RX.sub("<credential-redacted>", s)
        return s
    except Exception:
        return "<masking-failed:redacted>"


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        # Refuse to construct a client without credentials. The SDK will
        # happily initialise "disabled", then its background exporter still
        # attempts real HTTP and logs `Failed to export span batch: 401` on
        # every flush — noise on every single process, plus pointless network
        # calls, for a backend that cannot work. Caught exactly this way: a
        # .env with REVENANT_TRACE_BACKEND=langfuse and blank keys made the
        # whole test suite emit 401s. jsonl still records everything, so
        # returning None here loses no tracing.
        if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
            _client = False
            return None
        try:
            from langfuse import Langfuse
            _client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                # Both LANGFUSE_HOST and LANGFUSE_BASE_URL are read natively by
                # this SDK; BASE_URL is what langfuse-cli documents, so accept
                # either rather than making the two tools disagree about which
                # region they point at.
                host=(os.getenv("LANGFUSE_HOST")
                      or os.getenv("LANGFUSE_BASE_URL")
                      or "https://cloud.langfuse.com"),
                # Keeps offline/dev traffic out of the production dashboard.
                environment=os.getenv("REVENANT_MODE", "offline"),
                release=os.getenv("REVENANT_RELEASE") or _git_release(),
                mask=_mask,
            )
        except Exception:
            _client = False  # sentinel: tried and failed, don't retry every call
    return _client or None


def _git_release() -> str:
    try:
        from ghost import trace
        return getattr(trace, "_GIT_SHA", "") or "unknown"
    except Exception:
        return "unknown"


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


# Langfuse's baseline asks for the MOST SPECIFIC observation type, not a
# generic span — the type drives the Agent Graph and per-type analytics.
# Full set (per langfuse.com/docs/observability/features/observation-types):
# event, span, generation, agent, tool, chain, retriever, evaluator,
# embedding, guardrail.
_KIND_TO_AS_TYPE = {
    "llm": "generation",
    "llm.vision": "generation",
    # A thin wrapper that delegates to another LLM call is the link between
    # steps, not a generation itself — double-counting it as a generation
    # would inflate token/cost analytics for every wrapped call.
    "llm.wrapper": "chain",
    "tool": "tool",
    "agent": "agent",
    # Each MCP tool call is one self-contained unit of work that orchestrates
    # the fleet — it decides flow and spawns sub-work, which is Langfuse's
    # definition of an agent rather than a plain span.
    "mcp": "agent",
    "pipeline": "chain",
    "tts": "tool",
    # Reading the founder's repo/docs is a pure lookup — the retriever type
    # exists exactly for this and is what makes retrieval visible separately
    # from generation in the UI.
    "context": "retriever",
    # The eval judge scores outputs; "evaluator" is its literal purpose.
    "eval": "evaluator",
    "judge": "evaluator",
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
            # A score must attach to SOMETHING — Langfuse rejects one with no
            # trace_id/session_id/observation_id as a 400 Bad Request. Caught
            # on the first live run: evals/history.py::record_run() scores a
            # bundle from the eval CLI, which runs outside any span, so
            # trace_id was None and every eval score was silently failing to
            # reach Langfuse (the API error is logged by the SDK, then
            # swallowed here). Prefer the trace, fall back to the campaign
            # session, and skip entirely rather than send a known-invalid
            # request.
            tid = record.get("trace_id")
            sid = record.get("session_id")
            if not tid and not sid:
                return
            client.create_score(
                name=record["name"], value=record["value"],
                trace_id=_langfuse_trace_id(tid) if tid else None,
                session_id=sid or None,
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

        # Trace-level dimensions, per Langfuse's "beyond the baseline" table.
        # Revenant's multi-tenancy maps onto these almost exactly:
        #   user_id    <- the tenant (which STARTUP this work is for), so cost
        #                 and quality break down per customer
        #   session_id <- one campaign (startup+merchant), grouping the whole
        #                 Engineer -> Director -> Sales chain into one session
        #   tags       <- tenant + run mode, for dashboard filtering
        # Attributes come from the span's own attrs, which traced_tool()
        # already populates from each MCP tool's kwargs (startup, merchant).
        dims = _trace_dims(record)

        with _propagate(dims):
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


def _trace_dims(record: dict[str, Any]) -> dict[str, Any]:
    """Derive user_id / session_id / tags for one span record."""
    attrs = record.get("attrs") or {}
    startup = str(attrs.get("startup") or "").strip()
    merchant = str(attrs.get("merchant") or "").strip()

    tenant = ""
    if startup:
        try:
            from agents import tenancy
            tenant = tenancy.resolve(startup)
        except Exception:
            tenant = startup.lower()

    tags = [t for t in (
        f"tenant:{tenant}" if tenant else "",
        f"mode:{record.get('revenant.mode') or ''}" if record.get("revenant.mode") else "",
        f"env:{record.get('revenant.env') or ''}" if record.get("revenant.env") else "",
    ) if t]

    return {
        "user_id": tenant or None,
        # A campaign is the self-contained unit a founder thinks in; grouping
        # by startup+merchant puts the prototype, the walkthrough and the
        # email for one prospect in a single Langfuse session.
        "session_id": (f"{tenant}:{merchant.lower()}" if tenant and merchant else None),
        "tags": tags or None,
    }


@contextlib.contextmanager
def _propagate(dims: dict[str, Any]):
    """Apply trace-level dimensions to the observation created inside.

    propagate_attributes() is the SDK's supported way to set these; it
    applies to the active span and any created within the context, which
    is exactly the scope of one emit(). No-ops cleanly when there is
    nothing to set (a span with no startup attr, e.g. a bare ghost
    pipeline run) so unattributed spans still record normally.
    """
    kw = {k: v for k, v in dims.items() if v}
    if not kw:
        yield
        return
    try:
        from langfuse import propagate_attributes
        with propagate_attributes(**kw):
            yield
    except Exception:
        yield


def flush() -> None:
    client = _get_client()
    if client:
        try:
            client.flush()
        except Exception:
            pass
