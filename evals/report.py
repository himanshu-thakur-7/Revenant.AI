"""Markdown report generation + baseline read/write/diff.

Baselines are per-golden composite scores, keyed by golden id, so a
regression shows up as "this golden's score dropped since the last
accepted baseline" rather than needing a human to remember what "good"
used to look like. See docs/evals-observability-design.md §1.7.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from evals.bundle import git_sha

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINES_DIR = REPO_ROOT / "evals" / "baselines"
REPORTS_DIR = REPO_ROOT / "out" / "evals"


def baseline_path(suite: str) -> Path:
    return BASELINES_DIR / f"{suite}.json"


def load_baseline(suite: str) -> dict[str, Any]:
    p = baseline_path(suite)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def accept_baseline(suite: str, golden_id: str, scored: dict[str, dict[str, Any]]) -> None:
    """Explicit opt-in write — a baseline is only ever updated by calling
    this, never implicitly by a report run. Stores composite + per-
    criterion scores + git_sha + date, per artifact kind present."""
    data = load_baseline(suite)
    entry: dict[str, Any] = {"git_sha": git_sha(), "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                             "kinds": {}}
    for kind, info in scored.items():
        judge = info.get("judge")
        entry["kinds"][kind] = {
            "t1_pass": info["t1_pass"],
            "composite": judge.composite if judge is not None else None,
            "criteria": {c.criterion: c.score for c in judge.criteria} if judge is not None else {},
        }
    data[golden_id] = entry
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path(suite).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def diff_against_baseline(suite: str, golden_id: str, scored: dict[str, dict[str, Any]],
                          *, regression_threshold: float = 10.0) -> list[str]:
    """Returns a list of human-readable regression descriptions — empty
    if nothing regressed (including if there's no baseline yet, since
    there's nothing to regress against)."""
    baseline = load_baseline(suite).get(golden_id)
    if not baseline:
        return []
    problems = []
    for kind, info in scored.items():
        judge = info.get("judge")
        if judge is None:
            continue
        base_kind = baseline.get("kinds", {}).get(kind)
        if not base_kind or base_kind.get("composite") is None:
            continue
        delta = judge.composite - base_kind["composite"]
        if delta < -regression_threshold:
            problems.append(f"{kind}: composite {base_kind['composite']} -> {judge.composite} "
                           f"(Δ{delta:+.1f}, baseline git_sha={baseline.get('git_sha', '?')})")
    return problems


def render_report(golden_id: str, scored: dict[str, dict[str, Any]],
                  regressions: list[str] | None = None) -> str:
    from evals.runner import score_summary

    lines = [f"# Eval report — {golden_id}", "", f"git_sha: {git_sha()}",
            f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    if regressions:
        lines += ["## ⚠ Regressions", ""] + [f"- {r}" for r in regressions] + [""]
    lines += ["## Detail", "", "```", score_summary(scored), "```"]
    return "\n".join(lines)


def save_report(golden_id: str, text: str) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = REPORTS_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{golden_id}.md"
    p.write_text(text)
    return p
