"""Revenant evals — deterministic + LLM-judge scoring for produced campaigns.

See docs/evals-observability-design.md for the full architecture. This
package is intentionally separate from ghost/tests/ (offline, <5s, no
network) — evals hit real URLs and real files on disk by design; that's
the whole point after this session's ngrok-tunnel bugs.
"""
