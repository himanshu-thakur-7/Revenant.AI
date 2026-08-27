.PHONY: help install test test-web test-ui test-all run demo console sync clean eval eval-judge eval-live eval-calibrate eval-propose console-test

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## create venv + install python deps and console deps
	uv venv --python 3.11 .venv
	. .venv/bin/activate && uv pip install -e ".[dev]"
	cd console && npm install

test:  ## run the python test suite (offline, no network)
	. .venv/bin/activate && python -m pytest -q

test-web:  ## run the website/ API + session tests (node built-in runner, no deps)
	cd website && node --test "test/*.test.mjs"

test-all:  ## every offline suite: python + web + the console render check
	$(MAKE) test
	$(MAKE) test-web
	$(MAKE) test-ui
	$(MAKE) console-test

run:  ## run the full loop for the default seller (queuepilot), offline
	. .venv/bin/activate && PYTHONPATH=. python -m ghost.cli run --seller queuepilot --limit 3

demo:  ## run the loop + publish results for the console
	. .venv/bin/activate && PYTHONPATH=. python -m ghost.cli run --seller queuepilot --limit 3 && PYTHONPATH=. python scripts/sync_console.py
	@echo "→ now run 'make console' and open http://localhost:5175"

sync:  ## publish out/ledger.json + sites into the console
	. .venv/bin/activate && PYTHONPATH=. python scripts/sync_console.py

console:  ## start the review console dev server
	cd console && npm run dev

clean:  ## remove generated artifacts
	rm -rf out console/public/ledger.json console/public/sites console/public/walkthroughs console/public/voice

# ── evals (see docs/evals-observability-design.md) ─────────────────────
# MERCHANT picks which bundle: `make eval MERCHANT=Meesho`. Defaults to a
# --from-disk lookup so these work against artifacts already on disk with
# no live pipeline run required.
MERCHANT ?= Meesho

eval:  ## T1 deterministic checks only, no LLM (fast, cheap)
	. .venv/bin/activate && python -m evals.cli check --merchant "$(MERCHANT)" --from-disk

eval-judge:  ## T1 + T2 (LLM judge, gated behind T1) for one bundle
	. .venv/bin/activate && python -m evals.cli score --merchant "$(MERCHANT)" --from-disk

eval-live:  ## full live pipeline run for MERCHANT, then score it (real $ spend)
	. .venv/bin/activate && REVENANT_MODE=live python -c \
		"import asyncio; from agents.mcp_server import build_prototype; \
		print(asyncio.run(build_prototype(startup='Razorpay', merchant='$(MERCHANT)', \
		merchant_domain='', pain='')))"
	. .venv/bin/activate && python -m evals.cli score --merchant "$(MERCHANT)"

eval-calibrate:  ## judge calibration set (evals/golden/labeled/) — confirms the judge still discriminates
	. .venv/bin/activate && python -m evals.cli calibrate

eval-propose:  ## cluster out/evals/history.jsonl for recurring failures, write proposals (never auto-applies)
	. .venv/bin/activate && python -m evals.cli propose

console-test:  ## regression check: console.html actually renders a playable <video> for a walkthrough URL in chat
	.venv/bin/python -m http.server 8790 --directory website >/dev/null 2>&1 & \
	SERVER_PID=$$!; \
	trap "kill $$SERVER_PID 2>/dev/null" EXIT; \
	sleep 1; \
	.venv/bin/python scripts/console_render_test.py

test-ui:  ## console UI suite (Playwright + Chromium, offline; needs `playwright install chromium`)
	. .venv/bin/activate && python -m pytest ui_tests/ -q
