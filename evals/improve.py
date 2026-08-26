"""evals/improve.py::propose_patch() — the feedback path from failing
evals back to a prompt/skill fix, per docs/evals-observability-design.md
§3.3.

Deliberately NOT an LLM-authored patch generator. Clustering is exact
string matching on normalized fail_reasons text, and the "which file"
mapping is a fixed lookup table below — mechanical and auditable, the
same discipline T1's deterministic checks already apply to artifacts,
now applied to the meta-question of "should the prompt change". A judge
that grades itself is the thing this whole framework exists to avoid;
a proposal generator that free-writes its own diff would have exactly
that shape (it reads its own scored failures and would be describing,
in its own words, why it should be trusted to fix them).

Auto-apply is explicitly OUT OF SCOPE here — see the design doc's
"Trigger policy" section. This module only ever writes a markdown file
under out/evals/proposals/; nothing it does touches agents/*/prompt.py,
agents/engineer/planner.py, or any SKILL.md. A human applies the patch
as a normal git commit.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from evals.history import HISTORY_PATH, load_history

REPO_ROOT = Path(__file__).resolve().parent.parent
PROPOSALS_DIR = REPO_ROOT / "out" / "evals" / "proposals"

# Recur >= this many times in the last WINDOW runs to trigger a proposal.
_THRESHOLD = 3
_WINDOW = 10
_HISTORY_READ = 20

# (kind, reason-substring match) -> target file, in priority order —
# first match wins. Substring match on the normalized fail_reason text,
# not exact — judge-authored reasons vary in wording run to run, but
# consistently mention the criterion they're failing (RUBRICS' own
# criterion names in evals/judge.py are threaded into the judge prompt,
# so they tend to surface here too).
_TARGET_MAP: list[tuple[str, str, str]] = [
    ("prototype", "specific", "agents/engineer/planner.py :: _PLANNER_SYSTEM "
                              "(prototype spec is generic instead of merchant-specific — "
                              "see its 'Forbidden generic phrases' list)"),
    ("prototype", "demo", "agents/engineer/planner.py :: _PLANNER_SYSTEM "
                          "(interactive #demo isn't realistic/working)"),
    ("prototype", "brand", "agents/engineer/prompt.py :: ENGINEER_SYSTEM (brand fit)"),
    ("prototype", "value prop", "agents/engineer/planner.py :: _PLANNER_SYSTEM (value prop)"),
    ("email", "evidence", "agents/sales/prompt.py :: SALES_SYSTEM (evidence grounding)"),
    ("email", "claim", "agents/sales/prompt.py :: SALES_SYSTEM (product claim accuracy)"),
    ("email", "template", "agents/sales/prompt.py :: SALES_SYSTEM (voice reads templated)"),
    ("email", "ask", "agents/sales/prompt.py :: SALES_SYSTEM (specificity of the ask)"),
]


@dataclass
class Cluster:
    kind: str
    normalized_reason: str
    example_reasons: list[str]
    run_bundle_ids: list[str] = field(default_factory=list)
    eval_ids: list[str] = field(default_factory=list)  # criterion keys that must improve

    @property
    def count(self) -> int:
        return len(self.run_bundle_ids)

    def target_file(self) -> str:
        for kind, needle, target in _TARGET_MAP:
            if kind == self.kind and needle in self.normalized_reason:
                return target
        return (f"(no specific prompt constant matched — needs manual triage; "
                f"kind={self.kind}, reason={self.normalized_reason!r})")


def _normalize(reason: str) -> str:
    """Collapse whitespace/case/trailing punctuation so 'Demo doesn't
    update on click.' and 'demo doesn't update on click' cluster
    together; deliberately NOT fuzzy beyond that — a looser match risks
    merging two genuinely different failure modes into one proposal."""
    s = re.sub(r"\s+", " ", (reason or "").strip().lower())
    return s.rstrip(".! ")


def _clusters_from_history(entries: list[dict]) -> dict[tuple[str, str], Cluster]:
    clusters: dict[tuple[str, str], Cluster] = {}
    for entry in entries:
        bundle_id = entry.get("bundle_id", "?")
        for kind, info in (entry.get("artifacts") or {}).items():
            reasons = info.get("fail_reasons") or []
            seen_this_run: set[tuple[str, str]] = set()
            for reason in reasons:
                key = (kind, _normalize(reason))
                if not key[1] or key in seen_this_run:
                    continue  # count each (kind, reason) once per RUN, not per occurrence
                seen_this_run.add(key)
                c = clusters.setdefault(
                    key, Cluster(kind=kind, normalized_reason=key[1], example_reasons=[]))
                if len(c.example_reasons) < 3:
                    c.example_reasons.append(reason)
                c.run_bundle_ids.append(bundle_id)
    return clusters


def _render_proposal(c: Cluster, *, window: int, threshold: int) -> str:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        f"# Eval failure proposal — {c.kind}: {c.normalized_reason}",
        "",
        f"Generated: {ts}",
        f"Recurrence: {c.count} of the last {window} scored runs "
        f"(threshold: {threshold})",
        f"Target: {c.target_file()}",
        "",
        "## Runs exhibiting this failure",
        "",
    ]
    for bid in c.run_bundle_ids:
        lines.append(f"- {bid}")
    lines += [
        "",
        "## What the judge actually said (up to 3 examples, verbatim)",
        "",
    ]
    for r in c.example_reasons:
        lines.append(f"- {r!r}")
    lines += [
        "",
        "## Suggested diff",
        "",
        "NOT auto-generated — this proposal identifies WHERE and WHY a "
        "prompt patch is warranted; a human writes the actual diff. "
        "Rationale (docs/evals-observability-design.md §3.3): prompt files "
        "are the entire quality surface of the product, and an LLM judge "
        "that can itself regress must not be trusted to rewrite the "
        "prompts it grades — that is a closed loop with no ground truth "
        "in it. Open the target file above, read the judge's verbatim "
        "reasons, and edit by hand.",
        "",
        "## Eval ids that must improve for this patch to be kept",
        "",
        f"- {c.kind}.composite (rerun `revenant-eval score --merchant <name> "
        f"--from-disk` after the edit and confirm the specific criterion "
        f"tied to this failure mode moved)",
        "",
        "---",
        "This file is a PROPOSAL ONLY. Nothing in this repo applies it "
        "automatically. Apply by hand as a normal git commit, or discard.",
        "",
    ]
    return "\n".join(lines)


def propose_patch() -> list[Path]:
    """Reads the last _HISTORY_READ entries from out/evals/history.jsonl,
    clusters fail_reasons by normalized text, and for every cluster that
    recurs in >= _THRESHOLD of the last _WINDOW runs, writes a proposal
    file under out/evals/proposals/. Returns the paths written (empty
    list if nothing crossed the threshold, or if there's no history
    yet — both are normal, not errors)."""
    entries = load_history(_HISTORY_READ)
    if not entries:
        return []

    windowed = entries[-_WINDOW:]
    clusters = _clusters_from_history(windowed)

    written: list[Path] = []
    if not clusters:
        return written

    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for i, (key, c) in enumerate(sorted(clusters.items(), key=lambda kv: -kv[1].count)):
        if c.count < _THRESHOLD:
            continue
        fname = f"{stamp}-{i:02d}-{c.kind}-{re.sub(r'[^a-z0-9]+', '-', c.normalized_reason)[:40]}.md"
        p = PROPOSALS_DIR / fname
        p.write_text(_render_proposal(c, window=len(windowed), threshold=_THRESHOLD),
                     encoding="utf-8")
        written.append(p)
    return written


if __name__ == "__main__":
    import sys

    paths = propose_patch()
    if not paths:
        n = len(load_history(_HISTORY_READ))
        print(f"No recurring failure mode crossed the threshold "
              f"({_THRESHOLD} of last {_WINDOW} runs; {n} run(s) in history "
              f"at {HISTORY_PATH}).")
        sys.exit(0)
    print(f"Wrote {len(paths)} proposal(s):")
    for p in paths:
        print(f"  {p}")
