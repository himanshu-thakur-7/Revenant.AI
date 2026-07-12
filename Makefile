.PHONY: help install test run demo console sync clean

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## create venv + install python deps and console deps
	uv venv --python 3.11 .venv
	. .venv/bin/activate && uv pip install -e ".[dev]"
	cd console && npm install

test:  ## run the python test suite (offline, no network)
	. .venv/bin/activate && python -m pytest -q

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
