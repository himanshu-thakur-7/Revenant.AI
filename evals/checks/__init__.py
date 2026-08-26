"""Deterministic (T1) checks — no LLM, no judge, ~10s per bundle.

These are the hard gates: docs/evals-observability-design.md §1.5-1.6 is
explicit that a T1 FAIL zeroes the artifact's score and the LLM judge is
never called on it. This tier is the direct answer to this session's
actual bug class — "the tool reported success and the artifact was dead"
— so every check here re-fetches or re-opens the real artifact. None of
them read Bundle fields and trust them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    measured: Any = None

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        tail = f" — {self.detail}" if self.detail else ""
        return f"[{mark}] {self.name}{tail}"


def run_checks(checks: list) -> list[Check]:
    """Run a list of zero-arg check callables, catching exceptions as
    FAILs rather than letting one broken check crash the whole run —
    a check that can't run is not the same as skipping it silently."""
    out = []
    for fn in checks:
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001
            out.append(Check(name=getattr(fn, "__name__", "check"), passed=False,
                             detail=f"check raised: {exc!r}"))
    return out
