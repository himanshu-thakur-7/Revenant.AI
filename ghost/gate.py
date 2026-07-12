"""Signal-quality gate — Addendum 001 §3-5 + Addendum 002 §2, ported to Python.

The gate runs *before* any expensive work. 60-80% of job descriptions are HR
boilerplate with zero technical signal; dispatching the full pipeline against
them wastes tokens and produces mediocre artifacts that violate the whole
"show, don't tell" principle. Cheap stages first:

  Stage 1  · regex anomaly rules  (deterministic, ~free)
  Stage 1b · LLM classifier        (only when regex is ambiguous)
  Stage 3  · weighted combiner + single-source override → tier

Tiers: kill · warm_only · corroborate · promote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .llm import complete_json
from .models import Evidence, SignalScore, Tier


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    weight: float


# Positive rules — anomalies a real engineer forced into HR boilerplate.
# Negative rules — boilerplate that pushes confidence down. (Addendum 001 §4)
JD_RULES: list[Rule] = [
    Rule("scale_metric", re.compile(r"(?i)\b(p95|p99|qps|rps|tps|throughput|latency)\b"), 0.15),
    Rule("migration", re.compile(r"(?i)\bmigrat(ing|ion|ed)?\s+(from|to)\s+[A-Za-z0-9]+"), 0.20),
    Rule(
        "tuning_verbs",
        re.compile(
            r"(?i)\b(vacuum tuning|memory fragmentation|deadlock|alert fatigue|"
            r"cascading failures|hot partition|cold start|call drop|wait time|"
            r"call volume|hold time|abandonment rate)\b"
        ),
        0.30,
    ),
    Rule(
        "named_tech",
        re.compile(
            r"(?i)\b(redis cluster|dragonfly|clickhouse|neo4j|cockroachdb|"
            r"dynamodb streams|kafka connect|pulsar|nats jetstream|otel collector|"
            r"twilio|epic|cerner|athenahealth|genesys)\b"
        ),
        0.20,
    ),
    Rule(
        "internal_system",
        re.compile(r"\b[A-Z][a-z]+(Service|Engine|Pipeline|Gateway|Ingest|Router|Desk)\b"),
        0.10,
    ),
    Rule(
        "boilerplate_soft",
        re.compile(
            r"(?i)\b(fast-paced|team player|passionate about|proactive|self-starter|"
            r"excellent communication|rockstar|ninja|wear many hats)\b"
        ),
        -0.15,
    ),
    Rule("soft_skills_only", re.compile(r"(?i)\b(stakeholder|cross-functional|synergy)\b"), -0.05),
]

# Combiner weights (Addendum 001 §3)
W_JD, W_CAREERS, W_GITHUB, W_STATUS, W_BLOG = 0.35, 0.25, 0.15, 0.15, 0.10

# Single-source override (Addendum 002 §2): one very-strong source guarantees
# at least the corroborate tier — never a free ride straight to promote.
SINGLE_SOURCE_THRESHOLD = 0.75
SINGLE_SOURCE_FLOOR = 0.55


def score_jd(jd: str) -> tuple[float, list[Evidence]]:
    """Stage 1 — deterministic regex pass. JDs are innocent (0.30 floor) until
    proven boilerplate."""
    base = 0.30
    ev: list[Evidence] = []
    for rule in JD_RULES:
        m = rule.pattern.search(jd)
        if m:
            base += rule.weight
            if rule.weight > 0:
                s, e = m.span()
                excerpt = jd[max(0, s - 30) : min(len(jd), e + 30)].strip()
                ev.append(Evidence(source="jd", excerpt=excerpt, weight=rule.weight))
    return max(0.0, min(1.0, base)), ev


def score_jd_semantic(jd: str) -> float:
    """Stage 1b — only called when regex is ambiguous. Cheap LLM classifier
    (Groq/OpenAI/Cloudflare per Addendum 002 §3). Offline → neutral 0.5."""
    out = complete_json(
        f"Classify this job description. Does it name a SPECIFIC technical pain "
        f"(a system, a metric, a migration, an incident), or is it generic HR "
        f"boilerplate?\n\nJD:\n{jd[:1500]}",
        agent="gate_stage1b",
        system="You are a precise classifier. Return {\"specific_pain\": <0..1>}.",
        offline={"specific_pain": 0.5},
    )
    try:
        return max(0.0, min(1.0, float(out.get("specific_pain", 0.5))))
    except (TypeError, ValueError):
        return 0.5


def combine(s: SignalScore) -> float:
    """Weighted blend with single-source override (Addendum 002 §2)."""
    linear = (
        W_JD * s.jd_confidence
        + W_CAREERS * s.careers_score
        + W_GITHUB * s.github_score
        + W_STATUS * s.status_score
        + W_BLOG * s.eng_blog_score
    )
    max_single = max(
        s.jd_confidence, s.careers_score, s.github_score, s.status_score, s.eng_blog_score
    )
    if max_single >= SINGLE_SOURCE_THRESHOLD:
        return max(linear, SINGLE_SOURCE_FLOOR)
    return linear


def classify(combined: float) -> Tier:
    if combined < 0.30:
        return Tier.KILL
    if combined < 0.50:
        return Tier.WARM_ONLY
    if combined < 0.70:
        return Tier.CORROBORATE
    return Tier.PROMOTE


def evaluate(jd: str, forensics: SignalScore | None = None) -> SignalScore:
    """Full gate for one signal. ``forensics`` carries the cheap external
    source scores (careers/github/status/blog) already gathered; None = JD only.
    """
    jd_conf, jd_ev = score_jd(jd)
    # Only spend the LLM when regex is genuinely on the fence.
    if 0.30 < jd_conf < 0.55:
        jd_conf = (jd_conf + score_jd_semantic(jd)) / 2

    f = forensics or SignalScore()
    score = SignalScore(
        jd_confidence=jd_conf,
        careers_score=f.careers_score,
        github_score=f.github_score,
        status_score=f.status_score,
        eng_blog_score=f.eng_blog_score,
        evidence=[*jd_ev, *f.evidence],
    )
    score.combined = combine(score)
    score.tier = classify(score.combined)
    return score
