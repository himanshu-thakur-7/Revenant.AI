"""Golden-set eval for the signal-quality gate.

The gate is a classifier; it gets tested like one. Cases mirror Addendum 001
§8 and the three additions in Addendum 002 §2. Runs fully offline (Stage 1b
returns neutral 0.5) so CI never needs a network or an LLM.
"""

from __future__ import annotations

import pytest

from ghost.gate import classify, combine, evaluate, score_jd
from ghost.models import Evidence, SignalScore, Tier

# ── boilerplate → expect KILL ────────────────────────────────
BOILERPLATE = [
    "Looking for a Senior React Developer to join our fast-paced, collaborative "
    "team. Must be passionate about clean code and stakeholder communication.",
    "We're hiring a rockstar full-stack ninja! Team player, self-starter, wear "
    "many hats. Proactive communicator who thrives in a fast-paced environment.",
    "Backend engineer wanted. Cross-functional collaboration, excellent "
    "communication, passionate about building great products with synergy.",
]

# ── specific pain → expect PROMOTE ───────────────────────────
SPECIFIC = [
    "Seeking an SRE to fix our alert fatigue — p99 latency on the checkout "
    "service has cascading failures during peak. Migrating from Redis to "
    "Redis Cluster to handle throughput.",
    "DBA needed for vacuum tuning; our Postgres deadlocks under write-heavy "
    "load and QPS spikes cause hot partition issues on the OrderService.",
    "Voice infra engineer: our Twilio call volume causes 30s hold times and a "
    "high call drop rate at the front desk. Migrating to Genesys.",
]


@pytest.mark.parametrize("jd", BOILERPLATE)
def test_boilerplate_kills(jd: str) -> None:
    score = evaluate(jd)
    assert score.tier == Tier.KILL, f"expected kill, got {score.tier} ({score.combined:.2f})"


@pytest.mark.parametrize("jd", SPECIFIC)
def test_specific_promotes_with_corroboration(jd: str) -> None:
    # A specific JD plus strong external corroboration should promote.
    forensics = SignalScore(
        careers_score=0.8, github_score=0.7, status_score=0.6, eng_blog_score=0.5
    )
    score = evaluate(jd, forensics)
    assert score.tier == Tier.PROMOTE, f"expected promote, got {score.tier} ({score.combined:.2f})"


def test_kill_precision_high() -> None:
    """Of everything we kill, none should be a labelled-specific JD."""
    killed_specific = [jd for jd in SPECIFIC if evaluate(jd).tier == Tier.KILL]
    assert killed_specific == [], "false kills waste high-quality leads"


# ── Addendum 002 §2 single-source override cases ─────────────
def test_isolated_jd_strong_reaches_corroborate() -> None:
    # JD conf ~0.90, all else 0 → must NOT fall to warm/kill.
    s = SignalScore(jd_confidence=0.90)
    s.combined = combine(s)
    assert classify(s.combined) == Tier.CORROBORATE


def test_isolated_status_strong_reaches_corroborate() -> None:
    s = SignalScore(status_score=0.85, jd_confidence=0.10)
    s.combined = combine(s)
    assert classify(s.combined) == Tier.CORROBORATE


def test_multi_weak_stays_warm_only() -> None:
    # Everything at 0.40, no single-source trigger → linear 0.40 → warm_only.
    s = SignalScore(
        jd_confidence=0.40, careers_score=0.40, github_score=0.40,
        status_score=0.40, eng_blog_score=0.40,
    )
    s.combined = combine(s)
    assert classify(s.combined) == Tier.WARM_ONLY


def test_strong_multisource_still_promotes() -> None:
    # The single-source floor must never hold back a strong multi-source signal.
    s = SignalScore(
        jd_confidence=0.9, careers_score=0.9, github_score=0.9,
        status_score=0.9, eng_blog_score=0.9,
    )
    assert combine(s) > 0.70


def test_evidence_is_verbatim() -> None:
    jd = "Our Postgres needs vacuum tuning because writes are locking up."
    _, ev = score_jd(jd)
    assert ev and all(isinstance(e, Evidence) for e in ev)
    assert any("vacuum tuning" in e.excerpt for e in ev), "excerpt must be verbatim"
