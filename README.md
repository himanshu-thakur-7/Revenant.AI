<div align="center">

# 🕯️ Revenant.AI

**The autonomous outbound engineer.**
It hunts pain, ships a working prototype, films an AI walkthrough of it, and follows up while you sleep.

*Built for the GrowthX × Hermes Buildathon — Track: AI-as-an-Agency.*

### ▸ Live console: **[revenantai-app.vercel.app](https://revenantai-app.vercel.app)**

</div>

---

## The one-liner

> SDR agencies charge **$15k/mo** to send emails people delete.
> Revenant is the agency that ships **working software** instead — a live, prospect-specific prototype deployed to its own URL, with an AI-recorded Loom-style walkthrough, delivered before the prospect has ever taken a call.

## The loop (Acts I–V)

1. **Reconnaissance** — a founder dictates their company identity (Wispr Flow → Hermes). Revenant uses **Linkup** when configured, or a credential-free public sweep across live job posts + public GitHub issues when it is not, to find companies leaking that exact pain and write *verbatim evidence* into the **Convex** truth ledger.
2. **Signal gate** — a cheap, deterministic filter kills HR boilerplate for ~$0.001 before a single expensive token is spent (see [`ghost/gate.py`](ghost/gate.py)).
3. **Just-in-time engineering** — for promoted leads, a builder agent generates a working prototype + personalized microsite and deploys it to **Cloudflare Pages**.
4. **The cinematic pitch** — a **Director agent** screen-records the live prototype while an **ElevenLabs**-narrated presenter explains *what it built and why it fits*, hosted on **Cloudflare Stream**. No human touches the recording.
5. **The conversion loop** — an outreach agent drafts the email into a realtime human-review console; approve sends it. A **Razorpay** "book a paid pilot" button on the microsite flips the deal to **WON** live when the prospect pays, and Hermes pings a human closer.

## Sponsor stack

| Partner | Role |
|---|---|
| **Hermes (Nous Research)** | The orchestrator — every stage is a Hermes skill; cron = the 3 AM loop + persistence engine; memory = long-term prospect context; Telegram = control channel |
| **OpenAI** | Codegen, copy, walkthrough script, persona tone-scoring, embeddings |
| **Linkup** | Web reconnaissance — pain signals, careers/status/blog forensics. The demo also has a no-key public recon path via live jobs + GitHub issues. |
| **Cloudflare** | Pages (microsites) · Stream (walkthrough videos) · Workers AI (classifier fallback) |
| **Convex** | Truth ledger + realtime HITL console via live queries + Razorpay webhook |
| **ElevenLabs** | Walkthrough narration + voice memo + embedded conversational agent (the live prototype) |
| **Razorpay** | Paid-pilot payment link; webhook flips the deal to WON |
| **Wispr Flow** | Voice onboarding — dictate the seller identity |

## Architecture

```
Founder (Wispr Flow / Telegram)
      │
 HERMES AGENT ── cron (3 AM loop, follow-ups) ── memory (prospect context)
      │  skills wrap the agent-independent ghost/ package
      ▼
 recon → gate → profiler → builder → deploy → director → voice → outreach
      │                                                            │
      ▼                                                            ▼
 Convex (truth ledger + state machine)  ◄── live queries ──  HITL Console (React)
      │                                                     approve / edit / kill
      ▼
 send (DRY_RUN default) → cron follow-ups → Razorpay webhook → deal WON → Telegram alert
```

## Design principles (inherited from the Master Plan)

- **Show, don't tell.** Every touch ships an artifact you can run, click, or listen to. If we can't generate one, we don't send.
- **Verify before you send.** Generated sites must build and return 200; every microsite claim cites a verbatim evidence excerpt.
- **Budget everything.** Cheap stages gate expensive ones. The gate kills boilerplate for a tenth of a cent.
- **Human in the loop by default.** Nothing sends without a click; `DRY_RUN=1` is on by default.
- **Offline-first, live-capable demo.** `REVENANT_MODE=offline` is deterministic for CI; `REVENANT_MODE=live` performs a real public-data recon sweep even before paid API keys are added.

## Quickstart

```bash
# 1. install (core deps only — no browser/media needed for the offline demo)
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. run the whole loop offline against the built-in QueuePilot AI seller config
PYTHONPATH=. python -m ghost.cli run --seller queuepilot --limit 3

# 2b. run live public recon (no Linkup key required; still DRY_RUN by default)
PYTHONPATH=. REVENANT_MODE=live python -m ghost.cli run --seller queuepilot --limit 1

# 3. prove the gate does its job
pytest ghost/tests/test_gate.py -q

# 4. go live: cp .env.example .env, fill keys, set REVENANT_MODE=live
```

Outputs land in `out/` — `ledger.json` (what the console renders), verified microsite HTML, a working support-triage prototype, a playable Loom-style walkthrough, and voice/video artifacts.

## Repo layout

| Path | What |
|---|---|
| `ghost/` | The agent-independent pipeline (recon → outreach). Every stage is a plain, testable module. |
| `skills/` | Hermes skills — thin wrappers that let the agent drive the pipeline. |
| `convex/` | Schema + mutations + Razorpay webhook. |
| `console/` | React + Convex realtime human-review console. |
| `templates/` | Personalized microsite templates. |
| `docs/` | Demo script, compliance notes. |

## Compliance posture

Reconnaissance uses **public data only**. Every send carries a legal-basis field and honors a suppression list. Voice cloning is restricted to voices we own or have written consent for — never a prospect or public figure. `DRY_RUN` is on by default and real sends are gated to team-owned inboxes during the buildathon.

---

*MIT licensed. This is a portfolio + buildathon project; see [`docs/`](docs/) for the full design lineage (Master Plan v1.0 + Addenda 001/002).*
