# Revenant × Hermes — MCP integration

This is the **`hermes` branch** experiment: instead of running Revenant as a
standalone Telegram bot (that's `main`), we expose the outbound pipeline to
Hermes as an **MCP server** so Hermes drives it with native, structured tool
calls. No SOUL shell-command routing, no fork-and-async hacks, no
tool-timeout hallucination — the host calls a tool and waits for the artifacts.

## The server

`agents/mcp_server.py` — a FastMCP stdio server exposing 5 tools, each a thin
wrapper over the exact functions the standalone bot uses (so both front-ends
share the same `~/.revenant/*.json` state and stay interoperable):

| Tool | Wraps | Notes |
|------|-------|-------|
| `setup_startup(sources)` | `FounderContext.from_sources/github/folder` | onboard the founder's startup (repo / site / folder, one or many) |
| `find_prospects(brief, want)` | `runner.find_shortlist` | verified shortlist w/ real contact + fit rationale (~1 min) |
| `build_campaign(choice)` | `runner.build_campaign_for` | prototype + walkthrough video + deck + email (a few min) |
| `draft_email(to_email)` | `sales.gmail_draft.create_draft` | save to Gmail, never auto-send |
| `status()` | reads state files | what's currently loaded |

`build_campaign` returns `MEDIA:<path>` lines; the Hermes gateway extracts them
and delivers the walkthrough video + deck as attachments to the current chat.

## Registration (one-time on a machine)

```bash
# 1. install the mcp dep into the Revenant venv
UV_NO_EDITABLE=1 uv pip install "mcp>=1.2" --python ./.venv/bin/python

# 2. register the stdio server with Hermes (enable all tools when prompted)
hermes mcp add revenant \
  --connect-timeout 60 \
  --command /Users/little_beast/Revenant.AI/.venv/bin/python \
  --args   /Users/little_beast/Revenant.AI/agents/mcp_server.py

# 3. raise the per-call timeout (build_campaign runs for minutes) — in
#    ~/.hermes/config.yaml under mcp_servers.revenant:
#      timeout: 1200
#      idle_timeout_seconds: 3600
```

Verify: `hermes mcp test revenant` (should connect + discover 5 tools).

## Router model — REQUIRED

The Hermes router model must be a reliable tool-caller. **Hermes-4-405B does
NOT work** — it hallucinates an async "results will come later" reply and never
actually invokes long tools (verified: it fabricated a background job while the
tool ran orphaned). **gpt-4o works** — it invokes the tool and *waits* for the
result inline. In `~/.hermes/config.yaml`:

```yaml
model:
  default: openai/gpt-4o        # was Hermes-4-405B
  provider: openai-api
  base_url: https://openrouter.ai/api/v1
```

(This only affects the Hermes host/router. The 5 Revenant sub-agents run in the
Revenant venv on their own OpenAI config, untouched.)

## Routing nudge

`SOUL.revenant.md` is the snapshot of the `~/.hermes/SOUL.md` section that tells
Hermes to prefer the `revenant` tools for outbound requests (so the founder can
say "find me a healthcare customer" without naming the tool) and to WAIT for
synchronous tools rather than claim they run in the background. It's a light
nudge — the tool descriptions do the real routing. To apply, merge its content
into `~/.hermes/SOUL.md` and restart the gateway (`hermes gateway restart`).

## Verified (2026-07-11)

- `hermes mcp test revenant` → connected, 5 tools.
- `setup_startup(github.com/himanshu-thakur-7/shroud)` → called, waited, ctx=Shroud.
- `find_prospects("healthcare")` → called, waited ~45s, returned Oscar Health
  (mario@hioscar.com) + Cedar (florian@cedar.com) with Shroud-specific rationales.
- `status` routed correctly from "what's my outbound pipeline status?" (tool not named).
- `build_campaign` / `draft_email`: plumbed + callable; first live paid build left
  for the operator (real Cloudflare deploys; D-ID credits are 0 so lip-sync is
  skipped by default via `REVENANT_SKIP_LIPSYNC=1`).

## Telegram front door (follow-up, not done)

The Hermes gateway and the standalone bot can't both poll the same Telegram
token. To make Hermes+MCP the Telegram front door, stop the standalone service
(`launchctl bootout gui/$(id -u)/ai.revenant.bot`) and start `hermes gateway`.
Left for a deliberate switch — `main` keeps the standalone bot as the safe path.
