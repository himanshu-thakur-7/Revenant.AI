---
name: revenant-outbound
description: >
  Run Revenant's autonomous outbound-engineering fleet for the founder's
  startup: Research hunts a fit prospect (Linkup + Apollo), Engineer builds
  and deploys a personalized working prototype to Cloudflare Pages, Director
  films a Loom-style AI walkthrough, Sales drafts a pitch deck + personal
  email into the review queue. Use when the founder says "find me customers",
  "run outbound", "hunt prospects", "run the whole chain", or names a target
  vertical to pursue. Returns the artifact bundle: prototype URL, walkthrough
  video URL, pitch-deck URL, email draft + campaign id.
metadata:
  type: agent-skill
  runtime: python
---

# revenant-outbound

Revenant is a five-agent outbound fleet living at `~/Revenant.AI`. This
skill makes Hermes the front door: the founder speaks to Hermes (chat,
Telegram, cron), Hermes runs the fleet, and relays the artifact bundle back.

## When to use

- The founder asks to find/pursue prospects, run outbound, or build a
  campaign for a vertical.
- A cron fires the nightly "3 AM loop".

## Onboarding a startup (the /setup flow)

Before hunting, the founder points Revenant at their startup ONCE. When they
share a GitHub repo / URL / path with setup intent ("set up
github.com/you/startup", "sell for this repo", or a bare repo link):

```bash
cd ~/Revenant.AI && ./.venv/bin/python scripts/hermes_setup.py "<repo-or-path>"
```

This ingests the repo + docs and persists it as the active context
(`~/.revenant/active_context.json`). Every later `hermes_run.py` call sells on
that startup's behalf. Relay the confirmation stdout verbatim.

## How to run

From any directory (the venv is self-contained):

```bash
cd ~/Revenant.AI && ./.venv/bin/python scripts/hermes_run.py "<the founder's ask, verbatim>"
```

- Takes 3–6 minutes: it performs real web research, deploys a real site,
  records + muxes a real video. Do not kill it early; stream stderr for
  live progress.
- The script runs the deterministic chain: Research shortlists 3 verified
  prospects (real contact + email), auto-picks the strongest, then Engineer
  → Director → Sales build for it.
- **Interactive, three phases** (the script returns in <1s each time — relay
  the ack and STOP; a detached worker does the slow part + delivers to the
  chat via `hermes send`, addressed to whoever triggered it):
  1. `hermes_run.py "<ask>"` → worker posts **3 verified fits with fit
     rationales** to choose from.
  2. `hermes_run.py --build "<choice>"` (e.g. "build 1", "build Brex") →
     worker builds + delivers the full campaign (video, deck, brief with
     approve/tweak/switch options).
  3. `hermes_run.py --send "<email>"` → drafts the email into the founder's
     Gmail (synchronous, prints result). Never auto-sends.
- `DIRECTOR_SKIP_LIPSYNC=1` is the default (saves D-ID credits). Export
  `DIRECTOR_SKIP_LIPSYNC=0` only if the founder explicitly wants the
  lip-synced avatar.

## Delivering the walkthrough video yourself

If you need to (re)send an artifact to the founder's Telegram outside the
script — e.g. they ask "send me the video again":

```bash
hermes send --to telegram --subject "🎬 Walkthrough" "MEDIA:/Users/little_beast/Revenant.AI/out/walkthroughs/<slug>/walkthrough.mp4"
```

## Approve & send (human in the loop)

The email never sends itself. When the founder replies with an approval and
a recipient ("send it to me@example.com"):

```bash
cd ~/Revenant.AI && ./.venv/bin/python -c "
from agents.sales import send
print(send.send('<campaign_id from the brief>', '<recipient email>'))"
```

`DRY_RUN=1` (default) logs instead of sending — say so honestly.

## Environment

Keys live in `~/Revenant.AI/.env` (gitignored). The fleet degrades
gracefully: missing Apollo → pattern-guessed emails; missing ElevenLabs →
macOS `say` voice; missing Razorpay → no payment link. Never block on a
missing key — report what was skipped.
