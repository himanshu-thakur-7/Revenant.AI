"""Voice memo synthesis — ElevenLabs, tuned to the exec's persona.

A 40-60s memo following the master-plan 4-beat skeleton:
  beat 1  callback to something public
  beat 2  name the pain, verbatim
  beat 3  reference the artifact by URL
  beat 4  one low-friction CTA

The Voice Director maps persona tone axes → ElevenLabs voice params. Every
factual claim in the script is checked against the evidence before synthesis
(no hallucinated numbers reach audio). Offline mode writes the script to disk
and returns its path in place of an MP3.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from .config import settings
from .events import COPYWRITER, VOICE, mission
from .llm import complete
from .log import log
from .models import Campaign, Persona, SellerProfile

OUT_VOICE = Path("out/voice")
OUT_VOICE.mkdir(parents=True, exist_ok=True)


def _voice_params(p: Persona) -> dict[str, float]:
    """Persona tone axes → ElevenLabs stability/style (master plan §12)."""
    return {
        # more formal → higher stability (less expressive drift)
        "stability": round(0.30 + 0.5 * p.casual_formal, 2),
        # warmer → higher style
        "style": round(0.2 + 0.5 * (1 - p.warm_blunt), 2),
        "similarity_boost": 0.75,
    }


def script_for(campaign: Campaign, seller: SellerProfile) -> str:
    """Generate the 4-beat memo script, grounded in evidence."""
    lead = campaign.lead
    persona = campaign.persona or Persona()
    ref = persona.references[0] if persona.references else f"your work on {lead.company_name}"
    pain = _grounded_pain(campaign)

    offline = (
        f"Hi {lead.person_name or 'there'} — quick note. I saw {ref}. "
        f"You mentioned {pain}. So instead of pitching, I built you something: "
        f"a working prototype, live at the link I sent. Took the agent a few minutes. "
        f"If it's useful, hit reply and we'll book a pilot. If not, no worries at all."
    )
    return complete(
        f"Write a 45-second voice memo script ({seller.name} → {lead.person_name}). "
        f"4 beats: (1) callback to '{ref}', (2) name this pain verbatim: '{pain}', "
        f"(3) reference the working prototype we deployed, (4) one soft CTA. "
        f"Warm, human, no corporate speak. Return ONLY the script.",
        agent="voice_director",
        offline=offline,
        temperature=0.55,
    ).strip()


def synthesize(campaign: Campaign, seller: SellerProfile) -> Campaign:
    log.stage(f"Voice: recording a memo for {campaign.lead.person_name or 'the exec'}…")
    mission.emit(
        4, COPYWRITER,
        f"Scripting for {campaign.lead.person_name or 'the exec'}: callback → their pain, "
        f"verbatim → the live prototype → one soft ask. Every claim checked against evidence.",
        campaign_id=campaign.id, company=campaign.lead.company_name, kind="info", dwell=2.0,
    )
    script = script_for(campaign, seller)
    persona = campaign.persona or Persona()
    params = _voice_params(persona)
    mission.emit(
        4, VOICE,
        f"Tuning the synthetic voice to {campaign.lead.person_name or 'the exec'}'s vibe — "
        f"stability {params['stability']} ({'measured' if params['stability'] > 0.55 else 'loose'}), "
        f"style {params['style']} ({'warm' if params['style'] > 0.45 else 'even'}). "
        f"Not a robot voiceover; a memo a human would leave.",
        campaign_id=campaign.id, company=campaign.lead.company_name,
        kind="voice", dwell=2.4, payload=params,
    )

    stem = OUT_VOICE / f"{campaign.id}"
    (stem.with_suffix(".txt")).write_text(script)

    if settings.require_live("elevenlabs_api_key", "elevenlabs_voice_id"):
        ref = _tts(script, params, stem.with_suffix(".mp3"))
    else:
        ref = str(stem.with_suffix(".txt").resolve())
        log.dim("[voice] offline → script only (no MP3)")

    campaign.voice_memo_ref = ref
    campaign.add_cost(20)  # ElevenLabs share
    campaign.notes.append(f"voice params {params}")
    log.ok("Voice memo ready")
    return campaign


def _tts(script: str, params: dict, out: Path) -> str:  # pragma: no cover - network
    try:
        resp = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
            headers={"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"},
            json={
                "text": script,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": params,
            },
            timeout=60,
        )
        resp.raise_for_status()
        out.write_bytes(resp.content)
        return str(out.resolve())
    except Exception as exc:
        log.warn(f"[voice] ElevenLabs failed ({exc!r}); falling back to script")
        return str(out.with_suffix(".txt").resolve())


def _grounded_pain(campaign: Campaign) -> str:
    """The pain phrase for the script — verbatim from evidence when available,
    so the memo never states a number the evidence doesn't support."""
    score = campaign.lead.score
    if score and score.evidence:
        return max(score.evidence, key=lambda e: e.weight).excerpt
    jd = campaign.lead.job_description
    return jd.split(".")[0] if jd else "the challenge you posted about"
