# Revenant.AI — Evals + Observability + Versioning Architecture

> Design doc produced by an Opus-model research pass (2026-08-27), acted on directly
> in the same session. See git log from this date forward for what was actually
> implemented vs. deferred. Recommendation: Langfuse for tracing (not Arize Phoenix —
> see §2.1 for the reasoning), a new `evals/` package with 4 scoring tiers, and the
> eval framework doubling as the Hermes "critic" tool rather than a second scorer.

## 0. Findings that constrain the design

Verified by reading the code, not assumed:

| Fact | Evidence | Consequence |
|---|---|---|
| There are **four** raw LLM call sites, not three | `ghost/llm.py:97,168` (SDK), `agents/base.py:410` (SDK), `agents/engineer/planner.py:156` (httpx), **`agents/engineer/polish.py:102` (httpx, vision)** | `polish.py` was missed in the original brief. It is a second un-instrumented httpx call. |
| `planner.py` and `polish.py` never call `COST.record` | grep: `COST` appears only in `ghost/llm.py` and `agents/base.py` | Planner + vision-polish spend is **already invisible** to the existing cost panel. Tracing fixes an existing bug, not just adds telemetry. |
| `openai==3.3.1`, `httpx==0.28.1`, Python 3.11.15 | `./.venv/bin/python -c "import openai"` | Auto-instrumentation wrappers (`langfuse.openai` drop-in, `openinference-instrumentation-openai`) target openai 1.x/2.x. **Do not rely on them.** Manual spans only — which is required anyway for the two httpx sites. |
| MCP server is a **stdio** subprocess | `agents/mcp_server.py:1064` `mcp.run()`; `_quiet_stdout()` at :91 | Any tracing/eval code that prints to stdout corrupts the MCP framing and kills the server. Everything goes to stderr or a file. |
| There is a real, working specificity linter already | `agents/engineer/tools.py:96` `_specificity_warning()`, `_prospect_clues()` at :63 | Reuse it as an eval check. Do not write a second one. |
| There is a real prompt-contract test precedent | `scripts/engineer_specificity_prompt_test.py` | The T0 tier already half-exists; formalize it. |
| No CI exists | no `.github/` | CI job must be created from scratch. |
| `ffmpeg`/`ffprobe` present at `/opt/homebrew/bin` | `which` | Video deterministic checks are free. |
| `ghost/fixtures.py::CANNED_LEADS` is **seller-keyed** (`queuepilot`/`echodesk`/`ledgerloop`) with job-description/forensics shape | read in full | It fits the **ghost** pipeline, not the agents fleet (which needs `startup/startup_summary/merchant/merchant_domain/pain`). Reuse it for ghost-pipeline evals; a **new** golden set is needed for the agents fleet. |
| `~/.revenant/mcp_calls.log` already logs every tool call with timestamp | `_log_call()` at `agents/mcp_server.py:73` | Existing, poor-man's trace. Keep it; the new tracing supersedes it for analysis. |
| Artifacts already on disk from this session | `out/prototypes/{meesho,nykaa,snitch,lenskart,rigi}`, `out/walkthroughs/meesho`, `out/drafts/meesho` | The deterministic eval tier can be **validated against real artifacts within the first hour**, with zero new pipeline runs. |
| `Sales.draft()` accepts `extra_instruction`; `Engineer.build()` does not | `agents/sales/agent.py:74` vs `agents/engineer/agent.py:49` | The critic-retry loop needs `Engineer.build(extra_instruction=...)` added. |
| `.env.example` is missing ~15 live env vars | compared against `ghost/config.py::get_settings` + `os.getenv` grep | Missing: `APOLLO_API_KEY`, `DID_API_KEY`, `DID_PRESENTER_ID`, `DID_AGENT_*`, `DID_KNOWLEDGE_ID`, `STRONG_MODEL`, `STRONG_MODEL_KEY`, `STRONG_MODEL_URL`, `FOUNDER_NAME/EMAIL/COMPANY`, `CLOUDINARY_*`, `ENGINEER_MODEL`, `REVENANT_PLANNER_MODEL`, `REVENANT_ENGINEER_PLANNER`, `REVENANT_POLISH`, `REVENANT_POLISH_MODEL`, `REVENANT_REASONING_EFFORT`, `REVENANT_DIRECTOR_LIPSYNC`, `DIRECTOR_SKIP_LIPSYNC`, `REVENANT_TTS_VOICE`, `REVENANT_SAY_VOICE`, `REVENANT_DEMO`, `OPENAI_BASE_URL`. |

---

## 1. Evals framework

### 1.1 What "eval" means here — four tiers

The organizing principle comes straight from this session's nine bugs: **the failure mode is a tool that reports success while producing a dead artifact.** Therefore the cheap deterministic checks are the primary gate and the LLM judge is strictly secondary — it never runs on an artifact that failed a deterministic check.

| Tier | Name | Network | LLM | Runtime | Runs where |
|---|---|---|---|---|---|
| **T0** | Contract / unit | none | none | <5 s | `pytest -q`, CI on every push |
| **T1** | Artifact deterministic | HTTP + local files | none | ~10 s/bundle | CLI, post-run autoscore, CI-optional |
| **T2** | LLM-judge rubric | yes | yes (judge) | ~30 s/bundle | CLI, nightly |
| **T3** | Live pipeline e2e | yes | yes (full fleet) | ~5 min/golden | on-demand + weekly cron |

### 1.2 Repo layout — a new top-level `evals/` package

Not `ghost/tests/` (that's offline unit tests and must stay <5 s and network-free), not `scripts/` (already a junk drawer of one-off probes).

```
evals/
  __init__.py
  goldens.py            # GOLDEN_BRIEFS + LABELED_ARTIFACTS
  bundle.py             # Bundle dataclass + load/save of out/evals/bundles/*.json
  runner.py             # score_bundle(), run_suite()
  judge.py              # rubric call + CITATION VERIFICATION
  report.py             # markdown + rich table + baseline diff
  cli.py                # typer app -> `revenant-eval`
  checks/
    __init__.py
    http_.py            # url_alive, content_type, byte_size, final_url
    html_.py            # element-id contract, no-external-img, console errors, specificity
    video_.py           # ffprobe: has video stream, has audio stream, duration
    deck_.py            # python-pptx structural checks
    email_.py           # subject/evidence/banned-phrase checks
  rubrics/
    prototype.md  walkthrough.md  email.md  deck.md
  baselines/
    agents_fleet.json   ghost_pipeline.json
  golden/
    labeled/            # 3 hand-labeled bundles for judge calibration
```

Plus:
- `ghost/tests/test_evals_offline.py` — T0 wrappers so `pytest -q` covers the checks themselves.
- `pyproject.toml`: `[project.scripts] revenant-eval = "evals.cli:app"`, and `testpaths = ["ghost/tests", "agents", "evals"]`.
- `Makefile`: `eval` (T0+T1), `eval-judge` (T2), `eval-live` (T3).

### 1.3 The bundle — the unit of evaluation

Nothing can be scored until artifacts are addressable. **This is the first thing to build.**

```python
# evals/bundle.py
@dataclass
class Bundle:
    bundle_id: str          # ts + merchant slug
    created_at: str
    git_sha: str
    startup: str
    startup_summary: str
    merchant: str
    merchant_domain: str
    pain: str
    prototype_url: str
    prototype_html_path: str      # out/prototypes/<slug>/index.html
    walkthrough_url: str
    walkthrough_mp4_path: str
    walkthrough_storyboard_path: str   # out/walkthroughs/<slug>/*.storyboard.json
    deck_url: str
    deck_pptx_path: str
    email_md_path: str
    email_subject: str
    durations_s: dict[str, float]      # per stage
    prompt_versions: dict[str, str]    # agent -> "name@sem+sha12"
    models: dict[str, str]             # agent -> model id
```

Written by a new `_record_bundle()` in `agents/mcp_server.py`, called at the end of `build_prototype`, `film_walkthrough`, `draft_outreach` (merging by `bundle_id`) and by `build_full_outreach`. Persisted to `out/evals/bundles/<bundle_id>.json`. A `--from-disk` reconstructor handles the artifacts already sitting in `out/` from this session.

### 1.4 Golden set

`evals/goldens.py`. Ten briefs. Chosen for coverage, not volume — T3 costs ~5 min and ~$0.50 each.

```python
GOLDEN_BRIEFS = [
  # Verified-live this session (regression anchors)
  G("razorpay-meesho",  startup="Razorpay", merchant="Meesho",  domain="meesho.com",
    pain="COD-heavy social commerce checkout; RTO and prepaid conversion"),
  G("razorpay-nykaa",   ... "nykaa.com", pain="beauty checkout, loyalty + refunds disconnected"),
  G("razorpay-snitch",  ... "snitch.co.in", pain="D2C menswear, fast-fashion returns"),
  G("razorpay-lenskart",... "lenskart.com", pain="omnichannel try-at-home to checkout handoff"),
  G("razorpay-tata1mg", ... "1mg.com", pain="pharma prescription verification blocks checkout"),
  # Non-Razorpay startup — exercises _context_for_startup's non-canned path
  G("shroud-oscarhealth", startup="Shroud",
    startup_summary="PII/PHI redaction gate for support and analytics pipelines",
    merchant="Oscar Health", domain="hioscar.com",
    pain="PHI leaks into support transcripts before analytics handoff"),
  G("shroud-cedar", ... "cedar.com", ...),
  # Adversarial cases
  G("razorpay-nodomain", merchant="Bombay Shaving Company", domain="",   # no domain -> brand fetch fails
    pain="subscription razor refills, COD share"),
  G("razorpay-unknown",  merchant="Zeptolane Logistics", domain="zeptolane.example",  # fake -> must not hallucinate confirmed facts
    pain="B2B freight settlement"),
  G("razorpay-thinpain", merchant="Zomato", domain="zomato.com", pain=""),  # empty pain -> must still specialize
]
```

For the **ghost** pipeline (recon→gate→builder→outreach), reuse `ghost/fixtures.py::CANNED_LEADS` unchanged as the golden set — it already produces a deliberate tier spread and runs fully offline. `evals/goldens.py` re-exports it as `GHOST_GOLDENS` so both suites share the runner and report.

### 1.5 Deterministic checks (T1) — the load-bearing tier

Each returns `Check(name, passed: bool, detail: str, measured: Any)`. These are **hard gates**: any FAIL zeroes that artifact's score and skips the judge.

**Prototype**
1. `url_alive` — `httpx.get(url, follow_redirects=True, timeout=20)` → status 200. *This is the ngrok/dead-URL bug class. It is the single highest-value check in the entire framework.*
2. `content_type_html` — response `content-type` starts with `text/html`.
3. `not_ngrok_interstitial` — body does not contain `ngrok-free.app` warning markers / `You are about to visit`.
4. `body_size` — ≥ 8 KB (a real build is ~15–25 KB; a 4 KB skeleton is the known gpt-4o failure mode).
5. `element_id_contract` — fetched HTML contains `id="demo"`, `demoInput`, `demoRun`, `demoOutput`, `#code`, `#cta`. This is the Engineer's own documented contract (`agents/engineer/prompt.py`).
6. `no_external_img` — no `<img src="http`. Enforces the hard-won no-CDN-image rule.
7. `demo_input_prefilled` — `#demoInput` has non-whitespace content > 40 chars.
8. `specificity_lint` — call the existing `agents.engineer.tools._specificity_warning(html, prospect)`; PASS iff it returns `""`.
9. `renders_clean` — Playwright loads the page, collects `console` errors and `pageerror`; PASS iff zero errors. Also clicks `#demoRun` and asserts `#demoOutput` textContent changes. *This proves the interactive demo actually works — currently nothing verifies that.*

**Walkthrough**
10. `video_url_alive` + `content_type_video` (`video/mp4`).
11. `ffprobe_has_video_stream` and `ffprobe_has_audio_stream` — `ffprobe -v error -show_streams`. *Catches the silent-video failure class directly.*
12. `duration_between` — 25 s ≤ duration ≤ 180 s.
13. `audio_not_silent` — `ffmpeg -af volumedetect` mean_volume > −60 dB.
14. `mp4_size` — ≥ 300 KB.

**Deck**
15. `pptx_opens` — `python-pptx` `Presentation(path)` without exception.
16. `slide_count` — 5 ≤ n ≤ 7.
17. `slide_arc` — slide 1 contains both `startup` and `merchant` strings; last slide contains a CTA verb.
18. `copy_limits` — every title ≤ 8 words, every bullet ≤ 15 words (the Sales prompt's own stated contract at `agents/sales/prompt.py`).
19. `no_placeholder` — no `Lorem`, `TODO`, `{`, `<company>`.

**Email**
20. `md_exists_nonempty` — ≥ 400 chars.
21. `subject_len` — ≤ 60 chars, non-empty.
22. `no_banned_openers` — none of `quick question`, `reaching out`, `circling back`, `hope this finds you`, `touching base`, `I wanted to`.
23. `evidence_grounding` — at least 2 distinct clue tokens from `_prospect_clues(prospect)` appear in the body. Reuses existing code.
24. `links_present_and_alive` — the prototype URL and (if produced) walkthrough URL appear in the body **and** both resolve 200.
25. `no_placeholder_name` — no `[Name]`, `{{`, `<recipient>`.

That is 25 deterministic checks, ~10 seconds, no LLM, and it would have caught at least four of this session's nine bugs.

### 1.6 LLM judge (T2) — and how it is kept honest

Six mechanisms, all mechanical:

**M1 — Fetch, never trust.** The judge input is never the tool's return string. `evals/judge.py` re-downloads the HTML from `prototype_url`, reads the `.md` and `.pptx` off disk, and extracts the narration script from `out/walkthroughs/<slug>/*.storyboard.json`. The agent's own `summary`/`notes` fields are explicitly stripped from the bundle before it reaches the judge.

**M2 — Deterministic gate short-circuits.** If any T1 check fails, score = 0 and the judge is never called. No rubric can rescue a dead URL.

**M3 — Verified citations (the key mechanism).** Every rubric criterion requires:
```json
{"criterion":"account_specificity","score":0-4,
 "evidence":["<exact substring copied verbatim from the artifact>", "..."],
 "fail_reasons":["..."]}
```
The harness then runs, for each cited string, `normalize(cite) in normalize(artifact_text)` (whitespace-collapsed, case-folded). **Any criterion whose citations do not all verify is forced to 0** and flagged `UNVERIFIED_CITATION`. A judge that hallucinates quality evidence scores itself to zero. This is the direct structural answer to "how do we avoid the judge being fooled the way self-reported tool success fooled us."

**M4 — Blinding.** The judge prompt contains the artifact + the ground-truth brief only. It does **not** contain: the model that produced it, the prompt version, the git SHA, the baseline score, or any prior judgement. For A/B comparisons, candidate order is shuffled per call.

**M5 — n=2 with conservative aggregation.** `EVAL_JUDGE_N=2`; report `mean`, gate on `min`. Spread > 1.5 on any criterion emits `JUDGE_UNSTABLE` in the report.

**M6 — Judge calibration meta-eval.** `evals/golden/labeled/` holds three frozen bundles with hand-assigned expected bands:
- `good-meesho/` — this session's verified Meesho build → expect composite 75–100
- `generic-strawman/` — a deliberately logo-swappable prototype (write it by hand) → expect 0–40
- `dead-url/` — a bundle whose prototype_url points at a killed ngrok tunnel → expect T1 FAIL, score 0

`evals/cli.py calibrate` asserts all three land in band. If the judge stops separating good from generic, the judge is broken and the suite says so before it reports on real work.

**Judge model.** `EVAL_JUDGE_MODEL=gpt-5.6-sol` (default). Rationale: it is a different model from the producers (`gpt-4.1` author, `gpt-5.6-luna` planner, `gpt-4o` polish), it is a reasoning model well-suited to rubric adherence, and it works with the OpenAI key already in `.env` — so this tier is buildable today with zero founder input. Document `EVAL_JUDGE_MODEL=claude-sonnet-4-5` + `ANTHROPIC_API_KEY` as the preferred cross-family upgrade when the founder is willing to add one key; cross-family judging is meaningfully more robust but is not a blocker.

The visual criterion (`renders_polished`) uses a **vision** judge on a Playwright full-page screenshot — reuse `agents/engineer/polish.py::_screenshot()` verbatim, model `gpt-4o`. This is the one criterion that cannot be text-cited; it is capped at 20% weight and is advisory-only (never gates alone).

**Rubric criteria and weights**

Prototype (composite /100): account_specificity 30, demo_realism 25, brand_fit 15, value_prop_correctness 20, visual_polish 10.
Walkthrough: narration_specificity 40, beats_match_page 35 (each beat must cite a section heading that verifiably exists in the fetched HTML — a deterministic assist on a judged criterion), pacing_and_length 25.
Email: evidence_grounding 35, product_claim_accuracy 30 (judge gets the founder-context summary; hallucinated capabilities score 0), specificity_of_ask 20, voice_not_template 15.
Deck: narrative_coherence 60, slide_copy_quality 40 (all structural checks already deterministic in T1).

Each criterion is 0–4 with written anchors in `evals/rubrics/*.md` (0 = "would work for any competitor by swapping the name" — lifted verbatim from `planner.py::_PLANNER_SYSTEM`'s own stated bar, so the eval measures exactly what the prompt demands).

### 1.7 Pass/fail contract and regression surfacing

```
artifact_pass  := all T1 checks PASS  AND  composite >= 70
bundle_pass    := every produced artifact passes (a missing optional artifact = skip, not fail)
suite_pass     := >= 80% of goldens bundle_pass
                  AND no golden's composite regressed > 10 pts vs baseline
                  AND judge calibration in band
```

- Baseline: `evals/baselines/agents_fleet.json`, keyed by golden id, storing composite + per-criterion + git SHA + date. Updated only via explicit `revenant-eval accept-baseline`.
- Exit codes: `0` pass, `1` regression, `2` hard failure (dead artifact), `3` harness/judge broken.
- Report: `out/evals/<ts>/report.md` — a table of golden × artifact × T1 pass × composite × Δbaseline, plus a `fail_reasons` frequency histogram (this histogram is the input to the self-improving loop, §3).
- Dashboard: scores are pushed to Langfuse via `create_score()` attached to the production trace id (§2), so the trend over time is visible next to the traces that produced it. No separate dashboard is built.
- CI (`.github/workflows/ci.yml`, new): on push run `ruff check`, `pytest -q`, and `revenant-eval run --tier 0`. **T1–T3 are not in CI** — they need paid keys and network; they run via `make eval` locally and a weekly `hermes cron` job.

---

## 2. Observability, tracing, versioning

### 2.1 Recommendation: Langfuse, one system

**Primary: Langfuse (cloud free tier, `langfuse>=3.0`).** Not Arize Phoenix. Reasons, in priority order for a solo founder shipping a real product:

1. **Prompt versioning is an explicit ask and Langfuse has it as a first-class primitive** with version numbers, labels, and automatic trace↔prompt-version linkage. Phoenix's prompt management is thinner and its story is centered on notebook-driven experimentation.
2. **Scores are first-class and attach to traces.** The eval framework's output lands on the same object as the trace that produced it, which is exactly the "how do regressions get surfaced" requirement — one place, not two.
3. **Persistence without a babysat process.** Phoenix self-hosted is either an in-process app that dies with the run, or `phoenix serve` — a local SQLite server the founder must remember to keep running on a laptop that sleeps. The MCP server is a long-lived subprocess spawned by Hermes and runs unattended (including the "while you sleep" cron); traces from a 3 AM run must exist in the morning. Langfuse Cloud gives that for free.
4. **Cost tracking built in**, from its own model price table — which retires the stale, hand-maintained `_PRICE_PER_MTOK` in `ghost/llm.py` (it has no entry for `gpt-4.1`, `gpt-5.6-*`, or `gpt-5-mini`, so nearly every real call currently prices at the `default: 1.0` fallback).

**Cost of the recommendation:** it needs two API keys from a free account — about five minutes of the founder's time. That is the only external dependency in this entire design.

**How work starts today anyway (and why this is still one system, not two):** `ghost/trace.py` is a thin internal shim with a pluggable backend. Backend `jsonl` (default, ~40 lines, writes `out/traces/<date>.jsonl`) needs nothing external, is what the implementer builds and verifies against for the first six hours, and doubles as the offline input for the eval runner. Backend `langfuse` is a second implementation of the same four functions. **Phoenix is explicitly not adopted** — do not stand up a second observability product. If the founder later wants local-only traces, the `jsonl` backend already covers it.

### 2.2 The shim

```python
# ghost/trace.py
REVENANT_TRACE          # "1"|"0"        default "1"
REVENANT_TRACE_BACKEND  # jsonl|langfuse|none   default "jsonl"

def span(name, *, kind, **attrs) -> ContextManager[Span]   # kind: llm|tool|agent|mcp|pipeline
def record_usage(model, n_in, n_out, *, agent)             # annotates the current span
def record_io(inputs, outputs)                             # truncated to 20 KB each
def score(name, value, *, comment="", trace_id=None)       # eval results -> backend
def current_trace_id() -> str | None
def flush()                                                # atexit + explicit in CLIs
```

Hard rules for the implementation:
- **Never write to stdout.** stderr or file only. A stray print kills the MCP stdio server.
- **Never raise.** Every public function is wrapped in `try/except Exception: pass`. Tracing must not be able to break a build. Mirror the existing `_emit()` pattern at `agents/base.py:441`.
- **Never block.** Langfuse SDK batches on a background thread; the `jsonl` backend appends with a lock. Cap per-span payloads.
- Attach on every span: `revenant.git_sha` (from `git rev-parse --short HEAD` at import, cached), `revenant.git_dirty`, `revenant.release` (`REVENANT_RELEASE` or the SHA), `revenant.mode` (`settings.mode`), `revenant.env` (`local`/`mcp`/`cron`).

### 2.3 Instrumentation call sites — the complete list

Every one of these is required. Wrapping "the OpenAI client" covers only two of eight.

| # | File | Function | Span kind | Notes |
|---|---|---|---|---|
| 1 | `ghost/llm.py` | `complete()` | `llm` | leaf. Wrap the whole body including the offline-stub early return (`settings.offline or not api_key`) — that path currently returns silently and is a live-mode degradation bug worth seeing. |
| 2 | `ghost/llm.py` | `complete_strong()` | `llm` | leaf. Record the `except` fallback-to-weak-model branch as span event `strong_fallback`. |
| 3 | `ghost/llm.py` | `complete_json()` | `llm.wrapper` | parent of #1. Record `json_parse_failed` when it returns the offline stub — a silent failure today. |
| 4 | `ghost/llm.py` | `complete_strong_json()` | `llm.wrapper` | parent of #2, same. |
| 5 | `ghost/llm.py` | `CostLog.record()` | — | Add one line: `trace.record_usage(model, n_in, n_out, agent=agent)`. Single-point coverage for everything already calling COST. |
| 6 | `agents/base.py` | `_llm_step()` | `llm` | attrs: resolved `model`, `base_url` host, `use_strong_model`, `temperature`/`reasoning_effort`, `len(tool_schemas)`, retry attempt number, `system_sha`. Record the retry loop as span events. |
| 7 | `agents/base.py` | `run_turn()` | `agent` | trace root when an agent is driven directly (CLI/runner). attrs: `agent.name`, `max_iters`. |
| 8 | `agents/base.py` | `_loop()` — `tool.call(raw_args)` at :324 | `tool` | one span per tool invocation, name = tool name, io = args + result (truncated). This is what makes the trace readable. |
| 9 | **`agents/engineer/planner.py`** | `build_prototype_spec()` | `llm` | raw `httpx.post` at :156. Also add the missing `COST.record`. attrs: `model` (`REVENANT_PLANNER_MODEL`), `is_reasoning`, token budget, returned spec length. Record `planner_returned_empty` (the silent `return ""` failure path) as an event. |
| 10 | **`agents/engineer/polish.py`** | `polish_html()` | `llm.vision` | raw `httpx.post` at :102. Also add the missing `COST.record`. attrs: `_VISION_MODEL`, screenshot bytes, passes, `accepted` (whether the fix survived the `len(fixed) > 0.5*len(html)` guard). |
| 11 | `agents/director/tts.py` | OpenAI `/audio/speech` caller (`_OPENAI_TTS_URL`, :129) | `tts` | not chat-completions, still spend + a known failure surface (dead ElevenLabs key → 401 per beat). Record provider chain: elevenlabs → openai → macOS `say`. |
| 12 | `agents/mcp_server.py` | `build_prototype`, `film_walkthrough`, `draft_outreach`, `build_full_outreach`, `draft_email`, `status` | `mcp` (root) | **The most important site.** Each `@mcp.tool()` opens the root span for the whole production request; everything below nests. attrs: all tool args, elapsed, returned URL, and the `bundle_id`. Fold `_log_call()` into this. |
| 13 | `ghost/pipeline.py` | the deterministic run | `pipeline` (root) | root for ghost-pipeline runs so both surfaces are comparable. |
| 14 | `agents/runner.py` | `find_shortlist`, `build_campaign_for` | `pipeline` | roots for the Telegram-bot/deterministic surface. |

Not instrumented (deliberate): `agents/research/{web,linkup,apollo}.py` (search/enrichment APIs, no LLM — add later if quota debugging demands), `website/api/*.mjs` (JS proxy; see §2.6).

### 2.4 What gets versioned, and how a trace maps back to a prompt

**Git is the source of truth for prompts. Langfuse stores the link, not the prompt.** Do not adopt Langfuse prompt *hosting* — it would put a network dependency between the founder and a build, for a solo product where prompts change in the same commit as the code that consumes them.

Mechanism — two identifiers, both automatic-ish:

1. **`prompt_sha`** — `sha256(prompt_text)[:12]`, computed at call time by `trace.prompt_fingerprint(text)`. Cannot drift, needs no bookkeeping, and uniquely identifies the exact bytes that produced an output.
2. **`prompt_version`** — a human-readable `PROMPT_VERSION` constant added to the top of each prompt module, bumped by hand on meaningful edits:

| File | Constant | Prompt |
|---|---|---|
| `agents/engineer/prompt.py` | `PROMPT_VERSION = "engineer.author@1"` | Engineer author system |
| `agents/engineer/planner.py` | `PROMPT_VERSION = "engineer.planner@1"` | `_PLANNER_SYSTEM` |
| `agents/engineer/polish.py` | `PROMPT_VERSION = "engineer.polish@1"` | `_PROMPT` |
| `agents/sales/prompt.py` | `PROMPT_VERSION = "sales@1"` | `SALES_SYSTEM` |
| `agents/director/prompt.py` | `PROMPT_VERSION = "director@1"` | |
| `agents/research/prompt.py` | `PROMPT_VERSION = "research@1"` | |
| `agents/orchestrator/prompt.py` | `PROMPT_VERSION = "orchestrator@1"` | |
| `website/console.html` | `SYSTEM` const | Manager prompt — extract its sha in `scripts/sync_console.py` and stamp it into a `website/console.prompt.json` so console-driven traces carry it |

A T0 test (`test_prompt_versions_declared`) asserts every module in that table exports `PROMPT_VERSION` and that its `@N` was bumped whenever the sha changed relative to a checked-in `evals/baselines/prompt_shas.json` — so an un-versioned prompt edit fails CI. That is the concrete "correlate a trace back to which prompt version produced it" answer: `(git_sha, prompt_version, prompt_sha, model)` is on every LLM span, and the eval report groups regressions by that tuple.

Model/params versioning is free: `model`, `temperature` or `reasoning_effort`, `max_completion_tokens`/`max_tokens`, `base_url` host — all already resolved in code at the four call sites and go on the span as attributes.

### 2.5 Cost

Keep `CostLog` as the in-process unit-economics number the console renders. Do **not** extend `_PRICE_PER_MTOK`. Instead:
- `CostLog.record()` gains the one-line `trace.record_usage(...)` bridge (site #5), so the two offline-stub paths that record cost without an LLM call are still visible.
- Langfuse computes actual cost from usage + model id.
- Add the two missing `COST.record` calls in `planner.py` and `polish.py` — currently unaccounted spend.
- Add a `_PRICE_PER_MTOK` entry for `gpt-4.1`, `gpt-5-mini`, `gpt-5.6-luna/sol` anyway so the in-app number stops being fiction, with a comment pointing at Langfuse as the authority.

### 2.6 Env vars and `.env.example`

Append to `.env.example`:
```bash
# ── Observability (Langfuse — cloud.langfuse.com, free tier) ──
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
REVENANT_TRACE=1
REVENANT_TRACE_BACKEND=jsonl        # jsonl | langfuse | none
REVENANT_TRACE_DIR=out/traces
REVENANT_RELEASE=                   # defaults to git short SHA

# ── Evals ──
EVAL_JUDGE_MODEL=gpt-5.6-sol
EVAL_JUDGE_N=2
EVAL_VISION_JUDGE_MODEL=gpt-4o
REVENANT_EVAL_AUTOSCORE=1           # score every production bundle inline
REVENANT_EVAL_AUTOFIX=0             # 1 = allow one self-heal retry (see §3)
```

And fix the staleness properly rather than patching it: add T0 test `test_env_example_covers_every_getenv()` that greps `ghost/ agents/ scripts/ evals/` for `os.getenv("X")` / `os.environ["X"]` and asserts every name appears in `.env.example`. It will fail immediately on the ~20 missing vars listed in §0; fixing it is a 15-minute mechanical task and it can never go stale again.

Note for a follow-up (not this session): `website/api/runs.mjs` could forward a `X-Revenant-Trace-Id` into the Hermes run body so console-initiated runs share a trace id with the Python-side MCP spans. Requires a Hermes-side passthrough; scope it only if the founder asks for end-to-end console→MCP trace stitching.

---

## 3. Connection to the Hermes self-improving loop

### 3.1 The eval framework **is** the critic. Not a separate scorer.

Decision: implement the critic as a **7th MCP tool** that calls the eval runner. One rubric, one code path, no drift, and the LLM judge stays inside its deterministic guardrails (citation verification, T1 short-circuit) — which a free-form Hermes sub-agent judging from a chat transcript would not have. A Hermes critic sub-agent that "reads the result and scores it" would be fooled by exactly the self-reported-success pattern that cost this session nine bugs.

```python
# agents/mcp_server.py
@mcp.tool()
async def critique_campaign(merchant: str = "", bundle_id: str = "") -> str:
    """Score the last (or a named) campaign against Revenant's quality bar.
    Returns PASS/FAIL per artifact, a 0-100 composite, and concrete fix
    instructions. Deterministic checks (live URL, real audio, working demo)
    run first and are hard gates."""
```

It returns a compact, LLM-readable verdict — per-artifact PASS/FAIL, composite, and the top three `fail_reasons` — so the Hermes manager can delegate a **Critic sub-agent** whose only tool is `critique_campaign`, and relay the verdict to the founder. Update `website/console.html`'s `SYSTEM` prompt with a QA step: after `build_full_outreach` returns, delegate one Critic sub-agent, report the verdict alongside the artifacts. Update `skills/revenant-outbound/SKILL.md` and `~/.hermes/skills/revenant-outbound/SKILL.md` the same way.

### 3.2 Inline autoscore + one bounded self-heal

`build_full_outreach` calls `evals.runner.score_bundle()` before returning (gated on `REVENANT_EVAL_AUTOSCORE=1`) and appends a `QA:` block to its return string. Two self-heal rules, both bounded to a single retry and gated on `REVENANT_EVAL_AUTOFIX=1`:

1. **Deterministic deploy failure** (`url_alive` FAIL) → re-run the deploy step (`cf_pages.deploy_dir` then the `local_host.publish` fallback) once and re-check. *This session's ngrok bug would have self-healed twice.* This one is safe to default ON — it is a retry, not a rewrite.
2. **`account_specificity < 2`** → re-run `Engineer.build(extra_instruction=<judge fail_reasons>)` once. Requires adding `extra_instruction` to `Engineer.build()` (Sales already has it). Costs ~90 s and ~$0.20; keep it opt-in.

### 3.3 Failing evals → skill/prompt patches

Feedback path, concretely:

1. Every scored bundle appends to `out/evals/history.jsonl` (bundle_id, git_sha, prompt_versions, per-criterion scores, `fail_reasons[]`) and pushes `trace.score(...)` to Langfuse.
2. New file `evals/improve.py::propose_patch()` — reads the last 20 entries, clusters `fail_reasons` by normalized text, and when one mode appears in **≥ 3 of the last 10** runs, emits a proposal to `out/evals/proposals/<ts>.md`: the failure mode, the runs that exhibited it, the target file (`agents/engineer/planner.py::_PLANNER_SYSTEM`, `agents/sales/prompt.py::SALES_SYSTEM`, or a `SKILL.md`), a concrete diff, and the eval ids that must improve for the patch to be kept.
3. New Hermes skill `skills/revenant-critic/SKILL.md` (mirrored to `~/.hermes/skills/revenant-critic/`) describing: how to run `revenant-eval`, how to read a proposal, and the approval protocol.
4. A weekly `hermes cron` job runs `revenant-eval run --tier 2 --from-history`, then `propose_patch`, then messages the founder's Telegram with the diff.

**Trigger policy — human-in-the-loop, with one narrow exception.**

- Set `skills.write_approval: true` under the `skills:` block in `~/.hermes/config.yaml` (today it contains only `creation_nudge_interval: 15`, so this key must be added — **verify the exact key name against `hermes skills --help` / the running Hermes version before relying on it**, and treat a config edit as requiring the founder's explicit go-ahead since it lives outside the repo).
- The `skill_manage` call shape is approximately `skill_manage(action="update", name="revenant-outbound", content=<full SKILL.md text>)` — **read the tool's own description from the live Hermes tool list before writing the caller**; do not hardcode against this guess.
- Default: fully manual. `propose_patch()` writes a file and sends a Telegram message; a human applies it as a normal git commit. Rationale: prompt files are the entire quality surface of the product, and an LLM judge that can itself regress must not be allowed to rewrite the prompts it grades. That is a closed loop with no ground truth in it.
- The single auto-apply exception: **appending a newly-observed generic phrase to `_PLANNER_SYSTEM`'s "Forbidden generic phrases" list.** It is additive, trivially revertible, covered by a T0 test that asserts the list only grows, and it is the highest-frequency, lowest-risk patch class. Everything else — restructuring a prompt, changing the element-id contract, changing models — requires the founder.

---

## 4. Prioritized task list (~8 hours, one autonomous Claude session)

Rigor bar for every task: run it, curl it, ffprobe it, open the file. No task is "done" because the code compiles.

| # | Task | Time | Needs from founder | Why here |
|---|---|---|---|---|
| **1** | `evals/bundle.py` + `_record_bundle()` in `agents/mcp_server.py` + a `--from-disk` reconstructor for the existing `out/prototypes/*`, `out/walkthroughs/meesho`, `out/drafts/meesho` artifacts | 0:20 | none | Nothing can be scored until artifacts are addressable. Everything downstream blocks on this. |
| **2** | `evals/checks/*` — all 25 deterministic checks + `evals/cli.py check --bundle`. **Validate by running it against the real artifacts already on disk from this session** and confirming it correctly flags the known-dead ngrok URLs. | 1:10 | none | Highest value per minute in the entire plan. Catches the exact bug class that cost this session nine fixes, works today, needs no keys, no new pipeline runs. |
| **3** | `ghost/trace.py` shim + `jsonl` backend + wire all 14 instrumentation sites incl. the two httpx ones + the two missing `COST.record` calls. Verify: run `make run` (offline ghost pipeline) and one live `build_prototype`, then read `out/traces/*.jsonl` and confirm a nested mcp→agent→tool→llm tree with token counts. | 1:15 | none | Unblocks versioning, cost, and the eval↔trace link. Backend-agnostic, so no waiting on keys. |
| **4** | `PROMPT_VERSION` constants in the 7 prompt modules + `prompt_fingerprint` on every LLM span + `evals/baselines/prompt_shas.json` + the `test_prompt_versions_declared` guard | 0:30 | none | Cheap; makes every trace from here on attributable. Do it before the judge exists so the first eval run is already versioned. |
| **5** | `.env.example` regeneration + `test_env_example_covers_every_getenv()` | 0:20 | none | Known-stale, listed as needing a fix regardless, and it is a 20-minute mechanical task that permanently self-enforces. |
| **6** | `evals/goldens.py` (10 briefs) + `evals/judge.py` with citation verification + `evals/rubrics/*.md` + the 3 labeled calibration bundles + `revenant-eval calibrate` | 1:15 | none (uses the existing `OPENAI_API_KEY`) | The judge is worthless without M3/M6; build them together or not at all. |
| **7** | `evals/runner.py` + `report.py` + baselines + thresholds + `Makefile` targets + `.github/workflows/ci.yml` (T0 only). Run the full T1+T2 suite over the on-disk bundles, write the first baseline. | 1:00 | none | Turns the checks into a gate with a number. First real regression surface. |
| **8** | `critique_campaign` MCP tool + inline autoscore in `build_full_outreach` + the deploy-retry self-heal + `Engineer.build(extra_instruction=...)` + console `SYSTEM` and `SKILL.md` QA step. **Live-test: drive one real `build_full_outreach` through Hermes end-to-end and confirm the QA block appears and is correct.** | 1:00 | none (~$1–2 of API spend) | The critic ships as soon as the eval engine exists. This is the visible product win. |
| **9** | **Langfuse cutover**: `uv pip install "langfuse>=3.0" --python ./.venv/bin/python`, implement the `langfuse` backend behind the same four shim functions, flip `REVENANT_TRACE_BACKEND=langfuse`, push eval scores via `create_score()`, verify a real trace + its scores render in the UI. | 0:45 | **`LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`** (free account, ~5 min) | The only externally-blocked task in the plan. Deliberately last so a missing key costs nothing. If keys are unavailable, spend this slot on task 11 instead. |
| **10** | `evals/improve.py::propose_patch()` + `skills/revenant-critic/SKILL.md` + the weekly cron. Leave auto-apply OFF; ship the proposal generator only. | 0:45 | founder decision on `skills.write_approval` in `~/.hermes/config.yaml` (outside the repo) | Real value but the loop is only safe once the eval numbers are trusted, which requires tasks 6–8 to have produced at least one baseline. |
| **11** | **T3 live golden run**: `revenant-eval run --tier 3 --goldens 3` against Meesho + one Shroud golden + one adversarial. Record the first honest end-to-end pass rate. | 0:40 | none (~$2 spend, ~15 min wall clock) | The proof the whole thing works. Also the fallback slot if Langfuse keys never arrive. |

**Total: ~8:40 of work in an 8-hour window** — tasks 10 and 11 are the compressible tail. If time runs short, the non-negotiable core is **1, 2, 3, 6, 7** — that is a working, versioned, gated eval suite with tracing, entirely autonomous, needing nothing from the founder.

**Founder-supplied items, complete list:**
1. `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` — free account at cloud.langfuse.com. The only true blocker, and only for task 9.
2. Approval to add `skills.write_approval: true` to `~/.hermes/config.yaml` (outside the repo) — task 10.
3. Optional: `ANTHROPIC_API_KEY` for a cross-model-family judge, a meaningful robustness upgrade over the `gpt-5.6-sol` default but not required.

Everything else — all deterministic checks, the tracing layer, prompt versioning, the golden set, the LLM judge, the critic tool, the self-heal, CI — runs on keys already present in `~/Revenant.AI/.env` and can start immediately.
