---
name: ghost-hunt
description: >
  Run the full Revenant outbound loop for a seller — hunt prospects, score them
  through the signal gate, engineer a personalized prototype + microsite, film an
  AI walkthrough, synthesize a voice memo, and park the result for human review.
  Use when the founder says "go find customers", "hunt for <seller>", or "run the
  loop". Takes an optional seller slug (default ring-ai) and a lead limit.
metadata:
  type: agent-skill
  runtime: python
---

# ghost-hunt

This skill is the Hermes-facing wrapper over the Revenant pipeline. Hermes owns
the *conversation and the schedule*; the heavy lifting lives in the
agent-independent `ghost/` package, which Hermes invokes as a script.

## When to use

- The founder asks Revenant to go find and engage customers.
- The nightly cron fires (the "3 AM loop").

## How to run

From the repo root:

```bash
ghost run --seller {{seller_slug|default:ring-ai}} --limit {{limit|default:3}}
```

Then publish results for the review console:

```bash
python scripts/sync_console.py
```

## What to tell the founder

Narrate the funnel as it happens — this is the demo's Act II. For each lead,
report the gate verdict in plain language:

- **killed** → "Skipped {company} — boilerplate posting, cost us $0.001 to reject."
- **warm_only** → "{company} looks real but thin — queued a soft intro, no prototype."
- **promote/corroborate** → "{company} is a strong signal. Building a prototype,
  filming a walkthrough, and drafting the outreach now."

End with: "N campaigns are in the review console awaiting your approval."

## Output

- `out/ledger.json` — every campaign and its state (the console reads this).
- `out/sites/<domain>/index.html` — the deployed microsite (CF Pages URL in live mode).
- `out/walkthroughs/<id>.storyboard.json` — the AI walkthrough (MP4 on CF Stream in live mode).

## Guardrails

- Never send email from this skill. Sending is a separate, human-approved action
  (`ghost approve <id> --to <inbox>`), and `DRY_RUN` is on by default.
- The gate's budget guard is inviolable — do not attempt to force a killed lead
  through the swarm.
