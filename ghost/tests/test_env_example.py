"""T0 guard: every env var actually read anywhere in the codebase must be
documented in .env.example. See docs/evals-observability-design.md §2.6.

.env.example was stale for most of this project's life — ~47 real vars
were missing when this test was written (2026-08-27). This test is what
keeps it from drifting again: it greps the same three patterns
(os.getenv("X"), os.environ["X"], os.environ.get("X")) directly rather
than hand-maintaining a second list, so it can't itself go stale the same
way.

To regenerate .env.example after a legitimate new var: run this test,
read the failure's list of missing names, add each with a one-line
comment saying what it's for and where it's read — don't just append the
bare name. HERMES_SESSION_* are the one exception: those are read-only
context Hermes injects into the process env, never user-set, so they're
listed in .env.example as commented-out lines for documentation, not as
fillable `VAR=` entries — this test only checks the name APPEARS in the
file (comment or not), not that it's an active line.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
SCAN_DIRS = ["ghost", "agents", "scripts", "evals"]

_PATTERNS = [
    re.compile(r'os\.getenv\(\s*["\']([A-Z_][A-Z0-9_]*)["\']'),
    re.compile(r'os\.environ\[\s*["\']([A-Z_][A-Z0-9_]*)["\']\s*\]'),
    re.compile(r'os\.environ\.get\(\s*["\']([A-Z_][A-Z0-9_]*)["\']'),
    re.compile(r'os\.environ\.setdefault\(\s*["\']([A-Z_][A-Z0-9_]*)["\']'),
]

# Env vars read indirectly (via a local wrapper function, not a literal
# os.getenv(...) call the regexes above can see) — each one checked by
# hand against its actual call site; add here ONLY with that check done,
# not as a blanket escape hatch.
_INDIRECT_VARS = {
    "REVENANT_TRACE",  # ghost/trace.py: _flag("REVENANT_TRACE", True)
}


def _find_all_env_vars() -> set[str]:
    found: set[str] = set(_INDIRECT_VARS)
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pat in _PATTERNS:
                found.update(pat.findall(text))
    return found


def test_env_example_covers_every_getenv():
    real_vars = _find_all_env_vars()
    doc_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = sorted(v for v in real_vars if v not in doc_text)
    assert not missing, (
        f".env.example is missing {len(missing)} env var(s) actually read "
        f"in the codebase: {missing}. Add each with a comment saying what "
        f"it's for and where it's read (see this test's module docstring)."
    )
