"""Live Deal Room — a prospect dossier streamed WHILE the Engineer builds.

The gpt-5-mini Engineer takes ~2 min to build + deploy a prototype. Rather than
show the audience a dead "building…" spinner, we run this in parallel: a fast
gpt-4o-mini pass turns the prospect into a genuinely useful pre-call brief
(why-now trigger, decision-maker read, risk/ROI, talking points) and streams it
as bite-sized messages. It's real value for the founder AND showmanship for a
live demo — then a cheeky diversion to pass the last stretch.
"""

from __future__ import annotations

from typing import Any, Callable


# A serious-sounding "resource" that is, of course, a rick roll. Pure fun to
# cover the final seconds before the artifacts drop. Override for a real clip.
_DIVERSION_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def build_dossier_cards(prospect: dict[str, Any], founder_summary: str = "") -> list[str]:
    """Return a list of ready-to-send (HTML) message strings: a why-now
    trigger, a decision-maker read, a risk/ROI line, and 3 talking points.
    Falls back to a grounded template if the LLM is unavailable."""
    company = prospect.get("company_name", "the prospect")
    industry = prospect.get("industry", "their industry")
    contact = prospect.get("contact") or {}
    person = contact.get("name") or "the decision-maker"
    title = contact.get("title") or ""
    fit = (prospect.get("fit_rationale") or "").strip()

    from ghost.llm import complete_strong_json
    data = complete_strong_json(
        "You are a sharp SDR prepping a founder for a cold outreach. Using what "
        "you know about the company + the notes below, write a crisp pre-call "
        "brief. Be specific and realistic; if unsure, stay plausible and "
        "high-level (no fabricated numbers presented as fact).\n\n"
        f"Company: {company}\nIndustry: {industry}\n"
        f"Decision-maker: {person}{(' — ' + title) if title else ''}\n"
        f"Why they fit our product: {fit}\n"
        f"Our product: {founder_summary[:600]}\n\n"
        "Respond JSON: {"
        '"trigger": "1 sentence — the timely why-now (funding, launch, hire, '
        'regulation, scale) that makes outreach land", '
        '"decision_maker": "1 sentence — what this person likely cares about + '
        'how to frame it for them", '
        '"risk_roi": "1 sentence — the cost of the status quo / ROI angle, '
        'concrete but honestly hedged", '
        '"talking_points": ["3 short call talking points"]}',
        agent="dossier",
        offline={"trigger": "", "decision_maker": "", "risk_roi": "",
                 "talking_points": []},
    )

    cards: list[str] = []
    if data.get("trigger"):
        cards.append(f"⚡ <b>Why now:</b> {_esc(data['trigger'])}")
    if data.get("decision_maker"):
        cards.append(f"🎯 <b>{_esc(person)}:</b> {_esc(data['decision_maker'])}")
    if data.get("risk_roi"):
        cards.append(f"💰 <b>The stakes:</b> {_esc(data['risk_roi'])}")
    tps = [t for t in (data.get("talking_points") or []) if str(t).strip()][:3]
    if tps:
        body = "\n".join(f"  {i}. {_esc(str(t))}" for i, t in enumerate(tps, 1))
        cards.append(f"🗣 <b>Talking points:</b>\n{body}")

    # If the LLM was offline/empty, ground a minimal card so we never show
    # nothing.
    if not cards:
        cards.append(f"⚡ <b>Why {_esc(company)} fits:</b> "
                     f"{_esc(fit or f'{industry} company handling sensitive data')}.")
    return cards


def diversion_card() -> str:
    """The tongue-in-cheek time-pass diversion (serious framing, rick-roll link)."""
    return (
        "📎 <b>Almost there.</b> While the prototype finishes compiling, here's "
        "our 90-second explainer on why in-line PII redaction beats after-the-fact "
        f"scrubbing — worth a watch before your call:\n{_DIVERSION_URL}\n"
        "<i>(…you've been warned. 😉 Back to your campaign in a sec.)</i>"
    )


def _esc(s: str) -> str:
    import html as _html
    return _html.escape(str(s or ""))
