"""Profiler agent — build a persona for the target exec.

Produces tone-axis scores and real callback references that tune the copy and
the ElevenLabs voice. Everything it uses is public. Offline mode returns a
sensible neutral-professional persona so downstream stages always have input.
"""

from __future__ import annotations

from .config import settings
from .llm import complete_json
from .log import log
from .models import Lead, Persona, SellerProfile


def profile(lead: Lead, seller: SellerProfile) -> Persona:
    log.stage(f"Profiler: reading {lead.person_name or 'the decision-maker'}…")

    offline = {
        "casual_formal": 0.65,          # execs skew a touch formal
        "technical_strategic": 0.6,
        "warm_blunt": 0.45,
        "references": [
            f"their public push to fix {seller.pain_keywords[0]}"
            if seller.pain_keywords else "their scaling challenges",
        ],
        "vocabulary": seller.pain_keywords[:5],
    }

    if settings.require_live("llm_api_key"):
        out = complete_json(
            f"Profile this executive for a personalized outbound touch. Score tone "
            f"axes in [0,1] and list 2-3 concrete public things to reference.\n\n"
            f"Name: {lead.person_name}\nTitle: {lead.person_title}\n"
            f"Company: {lead.company_name}\nContext: {lead.job_description[:400]}",
            agent="profiler",
            system=(
                "Return JSON {casual_formal, technical_strategic, warm_blunt "
                "(all 0..1), references (list of strings), vocabulary (list)}. "
                "Only reference things a person has actually published."
            ),
            offline=offline,
        )
    else:
        out = offline

    return Persona(
        name=lead.person_name,
        title=lead.person_title,
        casual_formal=float(out.get("casual_formal", 0.6)),
        technical_strategic=float(out.get("technical_strategic", 0.6)),
        warm_blunt=float(out.get("warm_blunt", 0.45)),
        references=list(out.get("references", []))[:3],
        vocabulary=list(out.get("vocabulary", []))[:8],
    )
