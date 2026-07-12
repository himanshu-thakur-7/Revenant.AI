"""Domain models — the typed spine of the pipeline.

These mirror the master-plan data model (§7) and Addendum 001 §7, trimmed to
what the buildathon needs. Every stage consumes and returns these; the Convex
ledger persists them. IDs are ULID-like time-sortable strings.
"""

from __future__ import annotations

import os
import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str = "") -> str:
    """Monotonic-ish, sortable id. Not a real ULID but good enough and
    dependency-free. ``os.urandom`` gives the entropy tail."""
    ms = int(time.time() * 1000)
    time_part = ""
    for _ in range(10):
        time_part = _ULID_ALPHABET[ms % 32] + time_part
        ms //= 32
    rand = os.urandom(5)
    rand_part = "".join(_ULID_ALPHABET[b % 32] for b in rand)
    ulid = time_part + rand_part
    return f"{prefix}{ulid}" if prefix else ulid


class Tier(str, Enum):
    """Signal-quality gate verdict (Addendum 001 §3)."""

    KILL = "kill"
    WARM_ONLY = "warm_only"
    CORROBORATE = "corroborate"
    PROMOTE = "promote"


class CampaignState(str, Enum):
    """Trimmed campaign state machine (master plan §7)."""

    SCOUTING = "scouting"
    SCORED = "scored"
    BUILDING = "building"
    DEPLOYED = "deployed"
    FILMING = "filming"
    AWAITING_REVIEW = "awaiting_review"
    SENT = "sent"
    REPLIED = "replied"
    WON = "won"
    SUPPRESSED = "suppressed"
    WARM_ONLY = "warm_only"
    KILLED = "killed"


ArtifactKind = Literal["benchmark", "diagnostic", "reference_impl", "config_diff", "voice_demo"]


class SellerProfile(BaseModel):
    """Who is doing the selling. Produced by ghost-onboard from a dictated
    company blurb. Drives every downstream query and prompt, so one config
    swap re-points the whole pipeline at a new vertical."""

    id: str = Field(default_factory=lambda: new_id("sel_"))
    slug: str
    name: str
    one_liner: str
    product: str
    icp: str                      # who they sell to
    pain_keywords: list[str]      # what to hunt for in the wild
    prototype_kind: ArtifactKind = "voice_demo"
    value_prop: str = ""
    pilot_price_inr: int = 4999   # Razorpay "book a paid pilot" amount


class Evidence(BaseModel):
    """A verbatim citation supporting a confidence score. The most defensible
    part of the system: every claim on the microsite traces to one of these.
    We store the excerpt verbatim — never a summary (Addendum 001 §7)."""

    id: str = Field(default_factory=lambda: new_id("ev_"))
    source: str                   # jd | careers | github | status | eng_blog | news
    url: str = ""
    excerpt: str                  # verbatim quote, no summarization
    weight: float = 0.0


class SignalScore(BaseModel):
    jd_confidence: float = 0.0
    careers_score: float = 0.0
    github_score: float = 0.0
    status_score: float = 0.0
    eng_blog_score: float = 0.0
    combined: float = 0.0
    tier: Tier = Tier.KILL
    evidence: list[Evidence] = Field(default_factory=list)


class Lead(BaseModel):
    """A candidate prospect surfaced by recon, before or after scoring."""

    id: str = Field(default_factory=lambda: new_id("lead_"))
    seller_id: str
    company_name: str
    company_domain: str
    person_name: str = ""
    person_title: str = ""
    job_description: str = ""      # the raw pain text recon found
    score: SignalScore | None = None


class Persona(BaseModel):
    """Exec persona from the Profiler agent — drives copy and voice tuning."""

    name: str = ""
    title: str = ""
    # tone axes in [0,1]; 0 = left label, 1 = right label
    casual_formal: float = 0.5
    technical_strategic: float = 0.5
    warm_blunt: float = 0.5
    references: list[str] = Field(default_factory=list)   # real callbacks
    vocabulary: list[str] = Field(default_factory=list)


class Artifact(BaseModel):
    """A generated asset attached to a campaign."""

    id: str = Field(default_factory=lambda: new_id("art_"))
    kind: Literal["site", "code", "memo", "walkthrough", "copy"]
    storage_ref: str = ""         # URL or path
    checksum: str = ""
    verified: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class Campaign(BaseModel):
    """One outbound attempt at one person. The unit the state machine tracks."""

    id: str = Field(default_factory=lambda: new_id("camp_"))
    seller_id: str
    lead: Lead
    persona: Persona | None = None
    state: CampaignState = CampaignState.SCOUTING
    artifacts: list[Artifact] = Field(default_factory=list)
    microsite_url: str = ""
    walkthrough_url: str = ""
    voice_memo_ref: str = ""
    email_subject: str = ""
    email_body: str = ""
    payment_link: str = ""
    cost_cents: int = 0           # running LLM+API spend for unit economics
    notes: list[str] = Field(default_factory=list)

    def artifact(self, kind: str) -> Artifact | None:
        for a in self.artifacts:
            if a.kind == kind:
                return a
        return None

    def add_cost(self, cents: float) -> None:
        self.cost_cents += int(round(cents))
