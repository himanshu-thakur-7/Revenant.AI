---
name: revenant-critic
description: >
  Score a Revenant campaign (prototype, walkthrough, deck, email) against
  the real quality bar instead of trusting the build fleet's own
  self-report. Deterministic checks (live URL, real audio/video, working
  demo) are hard gates; an LLM judge with citation verification scores
  prototype/email copy only after those pass. Use after
  build_full_outreach (or any individual build_prototype/film_walkthrough/
  draft_outreach) to get an honest PASS/FAIL, or when the founder asks
  "how good is this build", "critique the campaign", "run the QA check",
  or reviews a weekly recurring-failure proposal.
metadata:
  type: agent-skill
  runtime: python
---

# revenant-critic

The critic is not a second opinion from a chat sub-agent reading a
transcript — it is `agents/mcp_server.py`'s `critique_campaign` tool,
which calls the same eval engine (`evals/`) that gates CI and produces
`out/evals/history.jsonl`. One rubric, one code path. Never re-implement
scoring by reading the fleet's own success message and judging it from
there — that is exactly the self-reported-success pattern that produced
nine real bugs in this codebase before the eval framework existed (see
`docs/evals-observability-design.md`'s opening section).

## Running a critique

The `critique_campaign` MCP tool is already wired into the console's
manager prompt: after `build_full_outreach` returns, delegate one Critic
sub-agent whose only tool is `critique_campaign`, relay its verdict
alongside the artifacts. Call it directly for a named or the last
campaign:

```
critique_campaign(merchant="Meesho")   # or merchant="" for the last campaign
```

Returns `QA: PASS` or `QA: FAIL`, per-artifact T1 check results, and (for
prototype/email, only if T1 passed) the LLM judge's composite score with
the exact quotes it cited as evidence. Relay a FAIL verdict plainly —
never soften or omit it because the artifacts "look" fine in chat.

## Running it from the CLI (debugging, or outside a live campaign)

```bash
cd ~/Revenant.AI
make eval MERCHANT=Meesho            # T1 only, no LLM, fast/free
make eval-judge MERCHANT=Meesho      # T1 + T2 (LLM judge), gated behind T1
make eval-calibrate                  # confirms the judge still discriminates real vs generic
make eval-propose                    # see "Recurring failures" below
```

Every `make eval-judge` / `revenant-eval score` run appends one line to
`out/evals/history.jsonl` — this is what `eval-propose` reads.

## Recurring failures → proposals (NOT auto-applied)

`evals/improve.py::propose_patch()` reads the last 20 history entries,
clusters the LLM judge's `fail_reasons` by normalized text, and — when
one failure mode appears in **3 or more of the last 10 scored runs** —
writes a proposal to `out/evals/proposals/<timestamp>-...md`: which
prompt file is implicated (e.g. `agents/engineer/planner.py ::
_PLANNER_SYSTEM` for prototype specificity, `agents/sales/prompt.py ::
SALES_SYSTEM` for email grounding), the runs that showed it, and the
judge's verbatim reasons.

**A weekly cron runs this and messages the founder** (`revenant-eval
score --tier 2 --from-history` — actually just `make eval-propose`
today, the tiered variant is a documented future extension, not yet
built — then `propose_patch`, then relay the diff/proposal file path via
whatever channel the cron fires on).

**The approval protocol — read this before touching a proposal:**

- A proposal file is a **read-only recommendation**. Nothing in this repo
  ever edits `agents/*/prompt.py`, `agents/engineer/planner.py`, or any
  `SKILL.md` automatically. `evals/improve.py`'s own module docstring
  states this explicitly and it is a hard invariant, not a
  not-yet-implemented feature.
- To apply one: open the target file named in the proposal, read the
  judge's cited reasons, hand-write the prompt edit, then run `make
  eval-judge MERCHANT=<name>` again and confirm the specific criterion
  named in "Eval ids that must improve" actually moved. Commit as a
  normal git commit — do not skip re-scoring before committing.
- **Do not call `skill_manage` to rewrite a SKILL.md or a prompt module
  from a proposal file without the founder's explicit go-ahead in chat**,
  even if `~/.hermes/config.yaml`'s `skills:` block appears to permit
  agent-initiated skill writes. Rationale (from the design doc): prompt
  files are the entire quality surface of the product, and a judge that
  can itself regress must not be trusted to rewrite the prompts it
  grades — that closes a loop with no ground truth left in it. If the
  founder asks you to apply a proposal for them, that is the one case
  where doing the edit directly is appropriate; relay exactly what
  changed and re-run the eval to confirm.
- The one narrow standing exception, if the founder has separately
  authorized it: appending a newly-observed generic phrase to
  `_PLANNER_SYSTEM`'s "Forbidden generic phrases" list is additive,
  trivially revertible, and covered by a T0 test that asserts the list
  only grows. Nothing else qualifies for unattended application.

## What "PASS" actually means

`bundle_pass()` (`evals/runner.py`): every claimed artifact's T1 checks
all pass, AND (for prototype/email) the LLM judge composite is >= 70 or
the artifact wasn't judged. A missing/optional artifact (e.g. no
walkthrough built yet) does not fail the bundle. T1 is the hard floor —
"the URL is dead" or "the demo doesn't render" fails the bundle
regardless of what the judge would have said, because the judge is never
even called on it (`evals/runner.py::score_bundle`'s explicit gate).
