"""Revenant evals — deterministic + LLM-judge scoring for produced campaigns.

See docs/evals-observability-design.md for the full architecture. This
package is intentionally separate from ghost/tests/ (offline, <5s, no
network) — evals hit real URLs and real files on disk by design; that's
the whole point after this session's ngrok-tunnel bugs.
"""

import os

# T2 (the LLM judge, evals/judge.py) unconditionally needs a REAL model
# call -- the tier table in docs/evals-observability-design.md marks T2
# "live: yes" with no offline mode. ghost/config.py's `settings` singleton
# is built ONCE, at whichever module first imports ghost.config, from
# REVENANT_MODE (default "offline" -- silent, zero-network canned
# fixtures). agents/mcp_server.py already forces this the same way at its
# own top (`os.environ.setdefault("REVENANT_MODE", "live")`) for the same
# reason. Caught live: running `revenant-eval score` from a bare shell
# with no REVENANT_MODE exported silently ran the judge in offline mode --
# ghost/llm.py's complete_json() returned its offline stub ({"scores":
# []}) with NO error, NO warning, and the judge machinery correctly
# computed a flat, fully-verified 0.0/100 composite from that empty
# response (raw_score 0 skips citation verification by design -- see
# judge.py's `verified = raw_score == 0 or _verify_citations(...)`) --
# indistinguishable from a real, harsh judge verdict unless you go looking
# for why every criterion is 0/4 with an empty fail_reasons list. Exactly
# the "the tool reports success and the artifact is dead" bug class this
# whole framework exists to catch, this time inside the eval tool itself.
# Must run at the evals PACKAGE's import, not evals/judge.py's own top --
# T1 checks (evals/runner.py's run_t1(), which always runs before T2)
# lazily import agents.engineer.tools for the specificity_lint check,
# which can pull in ghost.config first and lock the (cached) singleton to
# offline before evals.judge is ever imported.
os.environ.setdefault("REVENANT_MODE", "live")
