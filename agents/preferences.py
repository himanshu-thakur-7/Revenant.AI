"""Per-startup learned preferences — Phase 3 of the multi-tenant work.

Different startups want different things: tone, banned words, brand
facts, "we never claim X", feedback on a past prototype. This module
persists those per tenant and renders them into agent prompts, so the
Engineer/Sales/Director sub-agents behave the way THIS customer asked
rather than a generic default.

## The one hard rule: every preference must quote the founder

An LLM asked to "extract preferences from a conversation" will happily
produce confident, plausible, entirely invented ones — and unlike a bad
prototype (which T1 checks catch by fetching the real artifact), a
fabricated preference has nothing to check it against. It would then be
injected into every future prompt for that customer, permanently, as if
they had asked for it.

So extraction here borrows the exact mechanism evals/judge.py uses to
stop the judge fooling itself (its M3, `_verify_citations`): every
extracted preference must carry a `quote` that appears VERBATIM in the
actual conversation transcript. A quote that does not literally appear
means the preference is discarded — not down-weighted, discarded. That
turns "trust the extractor" into a structural guarantee: it can only
record something the founder actually said, because it has to show the
receipt.

Two further guards, both learned from real bugs earlier in this session:
  * OFFLINE MODE NEVER WRITES. In offline mode ghost/llm.py returns a
    canned stub; persisting that would poison a customer's preferences
    with placeholder junk, exactly like the offline-stub briefing and
    offline-mode judge bugs.
  * Preferences are CAPPED and deduped, so a long-running account can't
    accumulate an unbounded prompt prefix that slowly crowds out the
    actual task.

## Trust boundary

Same as agents/tenancy.py: this isolates preferences per tenant, it does
not authorize the caller. See that module's docstring.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from agents import tenancy

PREFS_FILE = "preferences.json"

# Kinds are a closed set on purpose — an open-ended `kind` invites the
# extractor to invent taxonomy instead of recording facts.
KINDS = ("tone", "avoid", "brand_fact", "feedback")

# Hard cap on stored preferences per tenant. Beyond this the oldest are
# dropped: an unbounded list would slowly turn every agent prompt into
# mostly-preamble, which degrades the actual task quality.
MAX_PREFERENCES = 40

# How much of a preference/quote we keep. Long enough to be meaningful,
# short enough that 40 of them stay a reasonable prompt prefix.
_MAX_TEXT = 240
_MAX_QUOTE = 300


def _normalize(s: str) -> str:
    """Same normalization as evals/judge.py::_normalize — whitespace
    collapsed, case folded — so quote verification is robust to
    reformatting without becoming loose enough to match paraphrase."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


@dataclass
class Preference:
    text: str                  # the durable instruction
    quote: str                 # VERBATIM founder words justifying it
    kind: str = "feedback"
    recorded_at: str = ""
    source: str = ""           # free label, e.g. "console conversation"

    def key(self) -> str:
        """Dedupe key — normalized text, so the same preference phrased
        with different capitalisation/whitespace is not stored twice."""
        return _normalize(self.text)


def _prefs_path(tenant: str):
    return tenancy.tenant_file(tenant, PREFS_FILE)


def load(tenant: str) -> list[Preference]:
    """Every stored preference for one tenant, oldest first. A missing or
    unreadable file yields an empty list — never raises, because a broken
    preferences file must not be able to block a build."""
    try:
        p = _prefs_path(tenant)
        if not p.exists():
            return []
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = []
        for row in raw.get("preferences", []):
            try:
                out.append(Preference(
                    text=str(row.get("text", ""))[:_MAX_TEXT],
                    quote=str(row.get("quote", ""))[:_MAX_QUOTE],
                    kind=row.get("kind") if row.get("kind") in KINDS else "feedback",
                    recorded_at=str(row.get("recorded_at", "")),
                    source=str(row.get("source", "")),
                ))
            except Exception:
                continue
        return [p for p in out if p.text]
    except Exception:
        return []


def save(tenant: str, prefs: list[Preference]) -> None:
    """Persist, newest-last, capped at MAX_PREFERENCES (oldest dropped).
    Never raises — preference storage is an enhancement, not a
    precondition for any build."""
    try:
        trimmed = prefs[-MAX_PREFERENCES:]
        tenancy.tenant_home(tenant).mkdir(parents=True, exist_ok=True)
        _prefs_path(tenant).write_text(
            json.dumps({
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "preferences": [asdict(p) for p in trimmed],
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def add(tenant: str, new: list[Preference]) -> list[Preference]:
    """Merge new preferences into a tenant's stored set, skipping ones
    already present (by normalized text). Returns the ones actually
    added, so a caller can report 'remembered 2 new things' honestly
    rather than claiming credit for duplicates."""
    existing = load(tenant)
    seen = {p.key() for p in existing}
    added = []
    for p in new:
        if not p.text or p.key() in seen:
            continue
        seen.add(p.key())
        added.append(p)
    if added:
        save(tenant, existing + added)
    return added


def verify_against(pref: Preference, transcript: str) -> bool:
    """The structural guarantee: the preference's quote must literally
    appear in the transcript (normalized). Mirrors evals/judge.py's M3 —
    an extractor that cannot show the receipt does not get to record the
    preference."""
    if not pref.quote or not pref.text:
        return False
    return _normalize(pref.quote) in _normalize(transcript)


_EXTRACT_PROMPT = """\
You are extracting DURABLE PREFERENCES a founder has stated about how \
their outbound campaigns should be built. These will be injected into \
every future prompt for this customer, so a wrong one does lasting damage.

RULES — these are absolute:
- Extract ONLY what the founder EXPLICITLY stated. Never infer, never \
generalise, never "read between the lines".
- Every preference MUST include `quote`: an EXACT substring copied \
verbatim from the conversation below. Copy-paste it; do not paraphrase, \
reword, fix typos, or merge two sentences.
- If the founder stated nothing durable, return an empty list. That is a \
correct and common answer. Do not invent something to be useful.
- Skip anything one-off or task-specific ("build for Meesho next"). Only \
things that should hold for FUTURE campaigns.

`kind` must be one of:
- "tone"       — how copy should sound
- "avoid"      — words, claims, or approaches to never use
- "brand_fact" — a durable fact about their product/company
- "feedback"   — a correction about how artifacts get built

CONVERSATION:
---
{transcript}
---

Respond with a JSON object: {{"preferences": [{{"text": ..., "quote": ..., "kind": ...}}]}}
"""


def extract(transcript: str, *, source: str = "conversation") -> tuple[list[Preference], list[str]]:
    """Extract preferences from a conversation transcript.

    Returns (verified_preferences, rejection_reasons). Every returned
    preference has been checked to quote the transcript verbatim; the
    rejections are returned rather than swallowed so a caller can surface
    "the extractor claimed 3 things but 1 could not be verified" instead
    of silently dropping it.

    Returns nothing in offline mode: ghost/llm.py's offline stub would
    otherwise be parsed as if it were a real extraction, and persisting
    that would poison the customer's preferences permanently.
    """
    from ghost.config import settings

    if settings.offline:
        return [], ["offline mode — preference extraction skipped (would record a stub)"]
    if not (transcript or "").strip():
        return [], []

    from ghost.llm import complete_json

    raw: dict[str, Any] = complete_json(
        _EXTRACT_PROMPT.format(transcript=transcript[:20_000]),
        agent="preferences.extract",
        offline={"preferences": []},
    )

    verified: list[Preference] = []
    rejected: list[str] = []
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    for row in (raw.get("preferences") or []):
        try:
            pref = Preference(
                text=str(row.get("text", "")).strip()[:_MAX_TEXT],
                quote=str(row.get("quote", "")).strip()[:_MAX_QUOTE],
                kind=row.get("kind") if row.get("kind") in KINDS else "feedback",
                recorded_at=now,
                source=source,
            )
        except Exception:
            continue
        if not pref.text:
            continue
        if verify_against(pref, transcript):
            verified.append(pref)
        else:
            rejected.append(
                f"unverified quote, discarded: {pref.text[:80]!r} "
                f"(claimed quote {pref.quote[:60]!r} does not appear in the conversation)"
            )
    return verified, rejected


_KIND_LABEL = {
    "tone": "Tone",
    "avoid": "Never do this",
    "brand_fact": "Fact about their product",
    "feedback": "Past feedback",
}


def render_for_prompt(prefs: list[Preference], *, max_chars: int = 2000) -> str:
    """The block injected into an agent's prompt. Empty string when there
    is nothing stored, so a caller can concatenate unconditionally without
    producing a dangling 'PREFERENCES:' header with nothing under it."""
    if not prefs:
        return ""
    lines = ["The founder has stated these standing preferences. Follow them:"]
    for p in prefs:
        label = _KIND_LABEL.get(p.kind, "Note")
        lines.append(f"- [{label}] {p.text}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit("\n", 1)[0]
    return out


def for_startup(startup: str, *, max_chars: int = 2000) -> str:
    """Convenience: resolve the tenant from a startup name and render its
    preference block in one call — what agent code actually wants."""
    try:
        return render_for_prompt(load(tenancy.resolve(startup)), max_chars=max_chars)
    except Exception:
        return ""
