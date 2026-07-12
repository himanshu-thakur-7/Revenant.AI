# Revenant AI — Complete Plan & Handoff (for Codex to take over)

> **Read this top-to-bottom before touching anything.** This is the single
> source of truth for the state of the project as of **2026-07-12** (the day of
> the GrowthX × Hermes Buildathon, Bangalore, track: *AI as an Agency*).
> Longer narrative history lives in `~/Desktop/revenant-context/HANDOFF.md`
> (pre-revamp) and `REVAMP.md` (the Hermes web-console workstream). This file
> supersedes both for orientation.

---

## 0. TL;DR

Revenant is an **autonomous outbound-sales agency**: give it your startup, it
finds fit customers, builds each a **real, deployed, on-brand prototype**, films
an AI walkthrough, writes a pitch deck + email — human stays in control (drafts
only, never auto-sends).

There are **two deliverables, on two git branches, both working**:

| | Branch | What it is | When to use |
|---|---|---|---|
| **A. Stage bot** | `main` | Deterministic Telegram bot; scripted Razorpay→boAt demo that self-arms on `/setup razorpay`. Bulletproof, nothing breaks live. | The on-stage finale + safe fallback. |
| **B. Hermes console** | `hermes` | A web console (`website/console.html`) that drives a **real Hermes agent crew** (manager + parallel sub-agents) over Hermes' `api_server`, with a live observability trace. Real builds, real URLs. | Mentor eval / the floor — this is what scores the rubric. |

Both must survive Sunday. **Do not break `main`.** All revamp work is on `hermes`.

---

## 1. Environment & prerequisites

- **Repo:** `~/Revenant.AI` (GitHub `himanshu-thakur-7/Revenant.AI`, pushed over SSH).
- **Python:** `~/Revenant.AI/.venv/bin/python` (3.11). Always use this venv, never system python. uv-managed (no pip; use `uv pip install --python ./.venv/bin/python`).
- **Run tests:** `./.venv/bin/python -m pytest -q` → 17 passing.
- **Branch discipline:** the launchd bot + the Hermes MCP server both run from `~/Revenant.AI` and **follow the checked-out branch**. For the Hermes console demo, stay on `hermes` (it has `agents/mcp_server.py` + `website/console.html`). For the stage bot demo, `git checkout main` (or run from tag `checkpoint/pre-hermes-standalone`).
- **Safe checkpoint:** tag `checkpoint/pre-hermes-standalone` = the last known-good standalone system. Restore with `git checkout checkpoint/pre-hermes-standalone`.
- **Keys / model routing (critical, learned the hard way):**
  - The **working OpenAI key** is `OPENAI_API_KEY=sk-proj-…` in `~/Revenant.AI/.env`. The old `sk-nous-…` key is **DEAD**.
  - `~/.hermes/.env` must have `OPENAI_API_KEY=<that sk-proj key>` + `OPENAI_BASE_URL=https://api.openai.com/v1`. If it points at `inference-api.nousresearch.com` (Nous), every model call 404s ("requires available credits") and the agent looks "broken" (thrashes on memory/session_search). This was the #1 gotcha.
  - Revenant's own agents read keys from `~/Revenant.AI/.env` (also overlays `~/.hermes/.env`).

---

## 2. Repo map (the files that matter)

```
agents/
  cli.py              # `revenant` CLI entry (chat, research, engineer, director, sales, telegram, gmail-auth…)
  runner.py           # DETERMINISTIC pipeline: find_shortlist() + build_campaign_for() (Research→Engineer→Director→Sales). No orchestration LLM. Used by the bot + demo.
  base.py             # Agent base (LLM tool-loop). reasoning_effort attr + REVENANT_REASONING_EFFORT. gpt-5.* reasoning detection.
  context.py          # FounderContext.from_github/from_website/from_sources/from_folder + .summary() + .product_name
  mcp_server.py       # ⭐ FastMCP stdio server = the Hermes<->Revenant bridge. Tools: setup_startup, find_prospects, build_campaign, build_prototype, draft_email, status.
  demo_razorpay.py    # ⭐ STAGE demo: canned Razorpay ctx, fixed [boAt,Mamaearth,Lenskart] shortlist, staged build (~10s ingest/20s research/140s eng/40s dir), pinned prototype+walkthrough. Gated: activate() on `/setup razorpay` or REVENANT_DEMO=1.
  demo_razorpay_deck.py    # co-branded Razorpay×boAt .pptx builder (both wordmarks; real logo PNGs if dropped in demo_razorpay_assets/logos/)
  demo_razorpay_site/      # the pre-built Magic-Checkout-for-boAt prototype (index.html) → razorpay-magic-demo.pages.dev
  demo_razorpay_assets/    # boat-walkthrough.mp4 (pinned Fiona walkthrough) + razorpay-boat-deck.pptx
  dossier.py          # Live Deal Room (parallel pre-call brief + rick-roll diversion during the build)
  engineer/
    agent.py          # Engineer.build() → writes a single-page prototype + deploys. Now uses planner+author (below).
    planner.py        # ⭐ NEW (claude#2): strong-reasoning PLANNER (gpt-5.6-luna) makes a Markdown spec BEFORE the author. REVENANT_PLANNER_MODEL, REVENANT_ENGINEER_PLANNER=0 to disable.
    prompt.py         # Engineer author prompt. Element-id contract (#demo #demoInput #demoRun #demoOutput #code #cta). STRICT no-<img> rule.
    prototype.py      # PrototypeState + _harden_html() (injects overflow-prevention CSS). _slug().
    brand.py          # fetch_brand(domain) — pulls accent colours/fonts/wordmark from the prospect's homepage.
    cf_pages.py       # deploy_dir() → `npx wrangler@3 pages deploy` to *.pages.dev. PRIMARY hosting.
    local_host.py     # ⭐ NEW (claude): local HTTP server + ngrok tunnel (publish(slug,html)). FALLBACK hosting if CF fails.
    polish.py         # ⭐ NEW (claude): vision QA pass — render→screenshot→gpt-4o critique+fix. OPT-IN via REVENANT_POLISH=1 (planner+author made it +0 by default).
    fallback.py       # deterministic template prototype if the LLM ships nothing.
    tools.py          # Engineer tool defs (write_prototype_file, deploy, finalize).
  director/           # walkthrough film: tts.py (ElevenLabs→OpenAI TTS→macOS say chain; boAt→boat pronunciation fix), avatar.py (D-ID lip-sync, Fiona), muxer.py (composite bottom-right), tools.py.
  sales/              # deck.py (pptx), gmail_draft.py (installed-app OAuth), send.py (Resend, DRY_RUN), razorpay.py.
  research/           # apollo.py, email_guess.py, web.py, linkup.py (research helpers).
  telegram/
    bot.py            # ⭐ the standalone Telegram bot (RevenantBot). Deterministic routing, inline buttons, staged demo hooks, _deliver (deck-before-video when demo active).
    api.py            # Telegram Bot API client (long-poll get_updates).
website/
  index.html          # marketing landing page (ember cyber-assassin hero) → revenantai-app.vercel.app. "Summon a demo" → console.html.
  console.html        # ⭐ the Hermes "Revenant Live" console: chat panel (SSE message.delta) + "Agency Floor" trace (tool.started/completed). SYSTEM prompt = the crew manager. Phase ticker for build UX. HARD-STOP rules.
scripts/              # hermes_run.py, hermes_setup.py, run_bot_service.sh (launchd), sync_console.py, gen_demo_data.py
```

Hermes config lives OUTSIDE the repo: `~/.hermes/config.yaml` (model, mcp_servers.revenant, delegation, memory) + `~/.hermes/.env` (keys, API_SERVER_*). `~/.hermes/SOUL.md` = the raw-TUI base prompt (NOT used by the console — console passes its own `instructions`).

---

## 3. Version A — Stage bot (`main`) — DONE, demo-ready

**What it is:** a single-founder Telegram bot (`@bot_revenant_bot`) that runs the
deterministic `runner.py` pipeline and delivers artifacts with Approve/Amend/Discard
buttons. For the stage, it self-arms a **scripted Razorpay→boAt** demo.

**Scripted flow (on stage):** onboard "Razorpay" → `demo_razorpay.activate()` →
10s staged ingest → "find merchants" → shortlist **[boAt, Mamaearth, Lenskart]**
revealed one-by-one over ~20s → tap **boAt** → ~140s staged "engineer" build
(Live Deal Room brief + rick-roll run in parallel) → pinned prototype
(`razorpay-magic-demo.pages.dev`) → ~40s "director" → **deck delivered BEFORE
the video** → Fiona-narrated walkthrough (`demo_razorpay_assets/boat-walkthrough.mp4`,
natural OpenAI-nova voice + D-ID lip-sync) → email draft to Aman Gupta.

**Everything is pre-built & deterministic** — no live LLM/deploy/Apollo variance
on stage. Fires ONLY for Razorpay (any other startup → normal live pipeline).

**Run it:** `git checkout main` → the launchd service `ai.revenant.bot` runs it
(`launchctl kickstart -k gui/$(id -u)/ai.revenant.bot`; log `out/bot-service.log`).
Bot self-arms on the word "Razorpay" — no env flag needed.

**Status: COMPLETE and verified.** Deck (co-branded, both logos-as-wordmarks),
delivery order (deck→video), director 40s, natural voice, lip-sync all done.

---

## 4. Version B — Hermes web console (`hermes`) — the rubric play

### 4a. Architecture
```
website/console.html  (chat UI + "Agency Floor" trace; served on Vercel or localhost)
   │  POST /v1/runs {input, instructions:SYSTEM, session_id}     ← founder's brief
   │  GET  /v1/runs/{id}/events   (SSE; events keyed "event", NOT "type")
   ▼
Hermes api_server  :8642   (Bearer API_SERVER_KEY from /tmp/hermes_api_key.txt)
   → Hermes AIAgent = the MANAGER (model gpt-5.6-sol)
       ├─ delegate_task([tasks])  → spawns parallel Research sub-agents (knowledge-mode)
       ├─ (build) delegates an Engineer sub-agent → calls MCP tool build_prototype
       └─ memory + session continuity (session_id)
   → MCP server `revenant` (agents/mcp_server.py) provides build_prototype/etc.
```
The api_server is enabled in `~/.hermes/.env` (`API_SERVER_ENABLED=true`,
`API_SERVER_KEY`, `API_SERVER_PORT=8642`, `API_SERVER_CORS_ORIGINS=*`). Gateway
must be running: `hermes gateway status|restart`.

### 4b. The manager SYSTEM prompt (in `website/console.html`, const SYSTEM)
- Manager works as a **CREW**: `delegate_task` with a tasks array → parallel sub-agents. Never grinds solo.
- **HARD STOP:** manager may ONLY `delegate_task` or reply. Banned: `terminal/shell/curl/execute_code/browser*/web_extract/session_search/memory-search`. After a sub-agent returns a URL → **report it, do NOT verify** (this fixed a 301s curl hang).
- **FIND:** spawn 3 parallel Research analysts (knowledge-mode, no heavy tools) → numbered shortlist (1/2/3) with why-fit + buyer role.
- **BUILD:** founder picks one → ONE Engineer sub-agent calls `build_prototype(startup, merchant, merchant_domain, pain)` → returns LIVE URL. Multi-build is **sequential** ("one at a time, say next"), NOT parallel.
- At most ONE `delegate_task` per turn.

### 4c. `build_prototype` (agents/mcp_server.py) — the "real output" proof (20x rubric)
- Signature: `build_prototype(startup, merchant, merchant_domain="", pain="", startup_summary="")`. Takes the merchant EXPLICITLY (no prior shortlist needed → the knowledge-crew can build).
- Canned Razorpay context for speed; else a minimal context from `startup_summary`.
- Runs the Engineer (planner `gpt-5.6-luna` → author `gpt-4.1`) → ~85s, ~22kB tailored on-brand HTML.
- **async** (anyio.to_thread) so it doesn't block the MCP event loop.
- Optional vision polish (`REVENANT_POLISH=1`). Deploys to **CF Pages (primary)**; ngrok is fallback if wrangler fails.
- Verified live across Nykaa/Tata 1mg/Zomato/Snitch/Meesho, all <90s, working demos.

### 4d. Run / test the console locally
1. Gateway up: `hermes gateway status` (restart if needed).
2. Serve the site: `cd website && ../.venv/bin/python -m http.server 8790` → open `http://127.0.0.1:8790/console.html`. (Or use the deployed `revenantai-app.vercel.app/console.html`.)
3. Console auto-connects to `http://127.0.0.1:8642` with the baked key (⚙ gear overrides via localStorage). **Chrome only** on the floor (Safari blocks HTTPS→http://localhost).
4. Drive it headless (same channel as the console) with a script: `POST /v1/runs {input, instructions:SYSTEM, session_id}` then stream `/v1/runs/{id}/events` (events use key `"event"`). See REVAMP.md for a ready snippet.

---

## 5. What's DONE ✅

**Stage bot (`main`):** deterministic Razorpay→boAt flow; self-arm; staged pacing
(10/20/140/40s); Live Deal Room + rick-roll; pinned prototype; Fiona walkthrough
(natural voice + D-ID lip-sync, "boat" pronunciation fix); co-branded deck delivered
before video; email draft. Verified.

**Hermes console (`hermes`):**
- Transport spike (web UI ↔ api_server SSE) — proven.
- `website/console.html` — chat + Agency-Floor trace, phase ticker, HARD-STOP rules, hard-coded demo endpoint+key.
- "Summon a demo" on the landing page → console.
- Real multi-agent crew: manager → 3 parallel research sub-agents → shortlist. Verified.
- `build_prototype` MCP tool: explicit-merchant real build+deploy, async, ~85s. Verified across 5 categories.
- Engineer planner+author architecture (141s→85s, quality up, no-<img> rule).
- Vision QA polish pass (opt-in).
- Local+ngrok hosting fallback.
- Model routing fixed (OpenAI-direct + gpt-5.6-sol/gpt-5-mini reasoning models; Nous key was dead).
- Deployed to `revenantai-app.vercel.app` (+ `/console.html`).

---

## 6. What's TO BE DONE ⏳ (prioritized for the floor)

1. **[HIGH] Full dress rehearsal of BOTH surfaces on the actual demo laptop** — stage bot (Telegram) AND console (Chrome). Confirm Chrome→localhost works; grant Telegram/mic as needed. Nothing here has been run on the real floor hardware together.
2. **[HIGH] Console BUILD step through the UI, end-to-end, watched live** — pick #N → Engineer sub-agent → `build_prototype` → live CF URL rendered in the trace + chat. Verified via api_server script; verify once more in the actual console UI (the 20x proof).
3. **[MED] Rubric power-ups made VISIBLE (each +25):** Convex (event store the console reads), Linkup (live web_search — currently NO search key set, sub-agents use model knowledge; set `EXA/TAVILY/LINKUP` key in `~/.hermes/.env` to unlock), Wispr (dictate a brief into the console), ElevenLabs (voice — quota out; OpenAI-nova is the working substitute). Cloudflare already used (hosting).
4. **[MED] Cron "while you sleep" loop** — the console agent already has the `cronjob` toolset; have it schedule an overnight hunt→build→draft that delivers to Telegram (home channel `8135896882`). Demo: `hermes cron list` + trigger one live. (See REVAMP "HERMES-NATIVE DIFFERENTIATION".)
5. **[MED] Memory recall shown** — the manager remembers the founder/prospects across turns; demonstrate it recalling a prior session.
6. **[LOW] Eval / self-improvement loop** — a critic sub-agent scores each artifact; show pass-rate climbing (rubric 5x).
7. **[LOW] Public hosting for remote judges** — console→`127.0.0.1:8642` only works on the founder's laptop. For remote, tunnel the gateway (cloudflared/ngrok) + tighten `API_SERVER_CORS_ORIGINS`.
8. **[LOW] Push `hermes` to origin** — 21 local commits unpushed. `git push origin hermes` when ready.
9. **[HOUSEKEEPING]** untracked `uv.lock`, `website/.gitignore` — decide whether to commit.

---

## 7. Hard-won gotchas (do NOT relearn these)

- **Model routing is the #1 failure mode.** If Hermes "thrashes on memory/session_search and never acts," the model call is 404ing — check `~/.hermes/.env` OPENAI_BASE_URL/KEY point at OpenAI (not dead Nous). OpenAI-direct uses the **Responses API with encrypted reasoning** → use a **reasoning model** (gpt-5-mini / gpt-5.6-*). Non-reasoning gpt-4o → `400: Encrypted content not supported`.
- **FastMCP over stdio cannot do N-concurrent tool calls.** "Build all 3 in parallel" killed the MCP server (64 restarts). Keep multi-build **sequential**. `build_prototype` is **async** to not block the event loop.
- **Hermes SSE does not forward MCP `ctx.info()`/progress** to the console → use the client-side phase ticker for build UX. SSE events are keyed `"event"`, not `"type"`.
- **Manager will "helpfully" verify URLs via curl and hang** → HARD-STOP rules ban terminal/curl/browser and say "trust the sub-agent's URL." Hermes' terminal approval gate (`pending_approval`) is the belt-and-suspenders backstop.
- **LLMs guess CDN `<img>` URLs that 404** → STRICT no-`<img>` rule (CSS gradients / inline SVG / emoji only; Google Fonts is the only allowed external asset).
- **CORS:** the api_server only allows `Authorization`+`Content-Type` headers → pass `session_id` in the BODY, not `X-Hermes-Session-*` headers. There's a **local patch** to `~/.hermes/hermes-agent/gateway/platforms/api_server.py` (`_handle_run_events` adds CORS headers to the SSE `StreamResponse`) — it lives OUTSIDE the repo and **reverts on `hermes update`; re-apply it** (see REVAMP CORS section).
- **ngrok free** shows a one-click browser interstitial (a request header skips it, browsers can't). That's why **CF Pages is primary** (no interstitial + sponsor points).
- **Two parallel agents edit this repo** (coordinate via `~/Desktop/revenant-context/*.md`). Don't revert each other's work; `main` is frozen for the stage bot.
- **iCloud Desktop** breaks editable installs (`.pth` hidden flag) — not relevant here since the repo is in `~/Revenant.AI`, but the founder's other projects live on Desktop.

---

## 8. Commands cheat sheet

```bash
# tests
./.venv/bin/python -m pytest -q

# stage bot (main)
git checkout main
launchctl kickstart -k gui/$(id -u)/ai.revenant.bot     # restart; log: out/bot-service.log

# hermes console (hermes)
git checkout hermes
hermes gateway status|restart                            # api_server :8642
cd website && ../.venv/bin/python -m http.server 8790    # → http://127.0.0.1:8790/console.html

# build one prototype directly (validate the Engineer)
./.venv/bin/python -c "from agents.mcp_server import build_prototype; \
  print(build_prototype(startup='Razorpay', merchant='Nykaa', merchant_domain='nykaa.com', pain='COD-heavy beauty checkout'))"

# deploy landing+console to Vercel (from website/)
npx vercel@latest deploy --prod --yes --scope himanshuthakur7s-projects

# D-ID credits / walkthrough re-record (main demo)
DIRECTOR_SKIP_LIPSYNC=1 ./.venv/bin/python -m agents.cli director <PROTOTYPE_URL> --company boAt ...
```

## 9. Env knobs (Revenant)
- `ENGINEER_MODEL` (author, default `gpt-4.1`) · `REVENANT_PLANNER_MODEL` (default `gpt-5.6-luna`) · `REVENANT_ENGINEER_PLANNER=0` (disable planner)
- `REVENANT_POLISH=1` (vision QA, ~9s, opt-in) · `REVENANT_POLISH_MODEL` (default gpt-4o)
- `REVENANT_REASONING_EFFORT` (minimal/low/medium/high)
- `REVENANT_DEMO=1` or onboard "Razorpay" → arms the stage demo · `DIRECTOR_SKIP_LIPSYNC=1`
- `REVENANT_TTS_VOICE` (default nova) · `REVENANT_SAY_VOICE` (default Samantha)
- Hermes model: `~/.hermes/config.yaml` `model.default` (currently `gpt-5.6-sol`)
- Model availability (our key, Jan 2026): works — gpt-4.1(-mini), gpt-4o, gpt-5(-mini), gpt-5.5, gpt-5.6-luna/sol/terra. Codex family = Responses API only (not usable in the current Chat-Completions tool loop). gpt-5.6-* reject `temperature`.

## 10. First moves for Codex
1. `git checkout hermes`; `hermes gateway restart`; confirm `curl -s localhost:8642/health` = ok.
2. Run the console find→build once, watch the Agency-Floor trace, confirm a live CF URL.
3. Then pick from §6 (start with the dress rehearsal + the visible power-ups).
4. Keep `main` untouched; keep `REVAMP.md` / this file updated at the end of each session.
