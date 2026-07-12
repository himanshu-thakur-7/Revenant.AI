---
name: ghost-followup
description: >
  The persistence engine. Scan the prospect memory ledger for commitments and
  deferrals whose window has closed, and re-open those campaigns into the
  re-engagement lane — with the outreach forced to reference the specific prior
  commitment (never a generic "just circling back"). Use on a nightly cron, or
  when the founder asks "who should we follow up with?".
metadata:
  type: agent-skill
  runtime: python
  schedule: "daily 03:00"
---

# ghost-followup

This is the feature that separates an engineered system from a duct-taped one:
managing month-long context across replies. Anyone can send a first touch.

## When to use

- Nightly, via Hermes cron (the master plan's re-engagement scheduler).
- On demand when the founder asks about pending follow-ups.

## What it does

1. Scans `memories` in the ledger for rows with a due `re_ping_at` (a prospect
   said "ping me in Q3" → we remembered, and Q3 is here).
2. Re-opens each such campaign into `awaiting_review`, loading the prior thread
   context into the copywriter's prompt.
3. The follow-up **must** cite the specific commitment ("you asked me to re-share
   the benchmark after your Q3 planning"), and threads into the same email
   conversation (In-Reply-To), not a fresh cold touch.

## How it runs

In live mode this is a Convex cron (`convex/crons.ts::scanDueMemories`). The
Hermes skill mirrors it for on-demand use and narrates the result to the founder
on Telegram:

> "3 prospects came due tonight. Re-opened Meridian (they asked to revisit after
> budget unlocked) — draft is in your review console."

## Guardrail

A won deal is never re-pinged. A prospect who said `unsubscribe`/`not_relevant`
is suppressed, not re-engaged.
