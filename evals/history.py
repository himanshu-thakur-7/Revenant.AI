"""out/evals/history.jsonl — the append-only run log Task 10's
propose_patch() reads to decide whether a failure mode is real and
recurring, not a one-off flake.

One JSON line per scored bundle: bundle_id, git_sha, prompt_versions,
per-artifact {t1_pass, judge composite, fail_reasons}, and the overall
pass/fail. Also pushes each judged criterion's score to the trace
backend via ghost.trace.score() (per docs/evals-observability-design.md
§3.3 step 1) so Langfuse (once configured) carries the same numbers —
best-effort: if there's no open trace (e.g. a bare `revenant-eval score`
CLI run outside any MCP tool call), the score record still gets written
with trace_id=None rather than silently dropped, exactly as ghost/
trace.py's own score() already behaves.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = REPO_ROOT / "out" / "evals" / "history.jsonl"


def _entry_from_scored(bundle: Any, scored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from evals.runner import artifact_pass, bundle_pass

    artifacts: dict[str, Any] = {}
    for kind, info in scored.items():
        checks = info["checks"]
        judge = info.get("judge")
        entry: dict[str, Any] = {
            "t1_pass": artifact_pass(checks),
            "t1_failed_checks": [c.name for c in checks if not c.passed],
        }
        if judge is not None:
            entry["judge_composite"] = judge.composite
            entry["judge_unstable"] = judge.unstable
            entry["fail_reasons"] = [r for c in judge.criteria for r in c.fail_reasons]
        artifacts[kind] = entry

    return {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bundle_id": bundle.bundle_id,
        "git_sha": bundle.git_sha,
        "merchant": bundle.merchant,
        "prompt_versions": dict(bundle.prompt_versions or {}),
        "artifacts": artifacts,
        "bundle_pass": bundle_pass(scored),
    }


def record_run(bundle: Any, scored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Append one entry to out/evals/history.jsonl and push judge scores
    to the trace backend. Never raises — a broken history write must not
    take down whatever caller triggered the scoring (mirrors ghost/
    trace.py's own never-raise contract, which this shares a spirit with
    even though history.jsonl itself is evals-owned, not ghost-owned)."""
    entry = _entry_from_scored(bundle, scored)
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass

    try:
        from ghost import trace

        # Anchor the score to the campaign's session. Scoring runs from the
        # eval CLI, outside any span, so there is no current trace_id — and a
        # score with neither trace nor session is rejected outright by the
        # backend, which is how every eval score was silently failing to land.
        # Same session key the span path uses (see ghost/trace_langfuse.py's
        # _trace_dims), so scores and spans meet on the same campaign.
        session_id = None
        try:
            from agents import tenancy
            tenant = tenancy.resolve(getattr(bundle, "startup", "") or "")
            merchant = (getattr(bundle, "merchant", "") or "").lower()
            if tenant and merchant:
                session_id = f"{tenant}:{merchant}"
        except Exception:
            session_id = None

        for kind, info in scored.items():
            judge = info.get("judge")
            if judge is not None:
                trace.score(f"{kind}.composite", judge.composite,
                           session_id=session_id,
                           comment=f"bundle={bundle.bundle_id} git_sha={bundle.git_sha}")
    except Exception:
        pass

    return entry


def load_history(limit: int = 20) -> list[dict[str, Any]]:
    """Most recent `limit` entries, oldest first (so callers can iterate
    in run order). Empty list if the file doesn't exist yet — no history
    recorded is not an error."""
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
