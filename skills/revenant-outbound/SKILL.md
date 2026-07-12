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

## How to run

From any directory (the venv is self-contained):

```bash
cd ~/Revenant.AI && DIRECTOR_SKIP_LIPSYNC=1 ./.venv/bin/python scripts/autopilot_demo.py "<the founder's ask, verbatim>"
```

- Takes 3–6 minutes: it performs real web research, deploys a real site,
  records + muxes a real video. Do not kill it early; stream the output.
- `DIRECTOR_SKIP_LIPSYNC=1` preserves scarce D-ID credits. Drop it only when
  the founder explicitly asks for the lip-synced avatar.
- The final stanza of stdout is the founder-facing brief (prospect, three
  URLs, email subject, analysis). Relay it VERBATIM — the URLs are real
  deployments; never paraphrase or re-format them.

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
