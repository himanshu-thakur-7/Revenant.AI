"""Deterministic full-chain runner — Research → Engineer → Director → Sales.

The interactive `revenant chat` uses the LLM Orchestrator to *decide* what to
do. But automated front-ends (the Telegram bot, cron's 3 AM loop) know
exactly what they want — a full campaign — and need **structured artifacts
back**, not a prose brief. This module runs the four sub-agents in sequence
with no orchestration LLM in the loop, so it's fast, cheap, and can't stall
on a mis-formatted tool call.

Each stage reports progress through an optional ``on_stage(stage, detail)``
callback so a front-end can stream status. Failures degrade gracefully:
the runner returns whatever artifacts it managed to produce with a warning,
rather than losing the whole run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ghost.config import settings

from .base import EventSink
from .bridge import bridge
from .context import FounderContext


# ── walkthrough + interactive-agent embedding ─────────────────────
def _video_section_html(video_url: str, company: str) -> str:
    """A dark-themed section with the narrated walkthrough as an inline
    <video>. Placed near the top of the prototype so the prospect sees the
    Loom-style tour immediately."""
    import html as _h
    co = _h.escape(company or "your team")
    return (
        '<section style="max-width:1000px;margin:0 auto;padding:40px 24px;">'
        '<div style="font-family:\'JetBrains Mono\',monospace;font-size:11px;'
        'letter-spacing:.28em;color:#7ee0c6;text-transform:uppercase;'
        'margin-bottom:14px;">a 90-second walkthrough for ' + co + '</div>'
        '<div style="background:rgba(148,163,184,0.05);border:1px solid '
        'rgba(148,163,184,0.14);border-radius:16px;padding:14px;">'
        f'<video controls playsinline preload="metadata" '
        'style="width:100%;border-radius:10px;display:block;background:#000;">'
        f'<source src="{video_url}" type="video/mp4" />'
        'Your browser can\'t play this video.</video></div></section>'
    )


def _agent_embed_html() -> str:
    """The D-ID interactive agent (Sana) floating-avatar embed. Emitted only
    when the agent is configured. NOTE: D-ID enforces a domain allowlist set
    in the D-ID Studio 'Embed' tab — until the deploy domain (e.g.
    *.pages.dev) is whitelisted there, the widget stays silent (it never
    breaks the page). Once whitelisted, it lights up automatically."""
    aid = getattr(settings, "did_agent_id", None)
    ckey = getattr(settings, "did_agent_client_key", None)
    if not (aid and ckey):
        return ""
    return (
        '<script type="module" src="https://agent.d-id.com/v1/index.js" '
        'data-name="did-agent" data-mode="fabio" '
        f'data-client-key="{ckey}" data-agent-id="{aid}" '
        'data-monitor="true"></script>'
    )


def _embed_media_into_prototype(art: "CampaignArtifacts", stage_cb) -> None:
    """Inject the walkthrough video (+ Sana agent) into the prospect's
    already-built prototype and redeploy. Updates ``art.prototype_url`` to
    the new deployment. No-op if the walkthrough isn't publicly hosted."""
    if not art.walkthrough_url or art.walkthrough_url.startswith("file:"):
        return
    from pathlib import Path
    from .engineer.prototype import PrototypeState
    from .engineer.cf_pages import deploy_dir

    ws = PrototypeState.for_prospect(art.company).workspace
    idx = ws / "index.html"
    if not idx.exists():
        return
    doc = idx.read_text(encoding="utf-8")
    if "revenant-embedded-media" in doc:
        return  # already embedded (idempotent on re-runs)

    video_html = _video_section_html(art.walkthrough_url, art.company)
    agent_html = _agent_embed_html()
    marker = '<div id="revenant-embedded-media"></div>'

    # video goes right after <body …>; agent script right before </body>
    if re.search(r"<body[^>]*>", doc):
        doc = re.sub(r"(<body[^>]*>)", r"\1\n" + marker + video_html, doc, count=1)
    else:
        doc = marker + video_html + doc
    if agent_html:
        doc = (doc.replace("</body>", agent_html + "\n</body>", 1)
               if "</body>" in doc else doc + agent_html)

    idx.write_text(doc, encoding="utf-8")
    stage_cb("embed_media",
             "Embedding the walkthrough + AI rep into the prototype…")
    redeploy = deploy_dir(ws)
    url = redeploy.get("url", "")
    if url and not url.startswith("file:"):
        art.prototype_url = url


# ── deterministic Apollo-first prospect hunt ──────────────────────
# The LLM Research agent is powerful but flaky on the first try; for the
# demo path we want a 100 %-reliable "we found something" moment. Apollo's
# org search is free, deterministic, and returns real US companies filtered
# by industry + size — perfect for a warm-start prospect. The LLM Research
# only kicks in as a fallback when Apollo's isn't configured or comes back
# empty (e.g. an unusual vertical Apollo doesn't index well).

# Vertical → Apollo keyword tags. Add more as you demo new verticals; the
# runner also asks the LLM to synthesize tags for unknown verticals.
_VERTICAL_TAGS: dict[str, list[str]] = {
    "healthtech":     ["digital health", "healthtech", "telehealth", "health tech"],
    "fintech":        ["fintech", "financial technology", "digital banking"],
    "insurtech":      ["insurtech", "insurance technology"],
    "legaltech":      ["legaltech", "legal technology", "legal software"],
    "edtech":         ["edtech", "education technology", "online learning"],
    "cybersecurity":  ["cybersecurity", "information security", "cyber security"],
    "ai":             ["artificial intelligence", "machine learning", "ai infrastructure"],
    "saas":           ["saas", "b2b software"],
    "developer tools": ["developer tools", "devops", "api"],
}


def _classify_vertical(brief: str) -> tuple[str, list[str]]:
    """Extract a canonical vertical and Apollo keyword tags from the founder's
    brief. Rule-based first (fast, free); LLM fallback for anything odd."""
    b = brief.lower()
    for name, tags in _VERTICAL_TAGS.items():
        if name in b or any(t in b for t in tags):
            return name, tags

    # LLM fallback — extract vertical + suggest Apollo tags
    from ghost.llm import complete_json
    result = complete_json(
        "Extract a target vertical + 3-4 Apollo.io industry/keyword tags from "
        f"this founder brief:\n\n{brief}\n\n"
        'Respond: {"vertical": "<name>", "tags": ["tag1", "tag2", ...]}',
        agent="runner.vertical",
        offline={"vertical": "b2b saas", "tags": ["saas", "b2b software"]},
    )
    return (str(result.get("vertical", "b2b saas")),
            [str(t) for t in (result.get("tags") or ["saas"])])


def _llm_name_startups(brief: str, vertical: str,
                       founder_gist: str = "") -> list[dict]:
    """Ask the STRONG model (gpt-4o) to name 10-12 real, notable startups in the
    vertical, each with a *specific* fit rationale grounded in the founder's
    product. The Apollo enrichment step verifies each candidate exists before
    we commit to them, and the rationale carries through to the shortlist so
    the founder can compare picks side by side."""
    from ghost.llm import complete_strong_json
    result = complete_strong_json(
        f"The founder briefed us: {brief!r}\n\n"
        f"Founder's product context (what they sell / core capability):\n"
        f"{founder_gist[:1500] or '(none supplied)'}\n\n"
        f"Name 12 real, well-known {vertical} startups (Seed to Series-B, "
        "US-based, founded 2018 or later — no huge public companies, no "
        "fabrications). For EACH candidate, write a **two-sentence fit "
        "rationale** that is SPECIFIC to this founder's product — name the "
        "pain angle, the trigger (e.g. recent hire, product launch, "
        "regulatory pressure), and the exact capability of the founder's "
        "product that lands. Do NOT be generic. Skip a candidate rather "
        "than write a vague rationale.\n\n"
        "Order candidates strongest-fit-first. Include the primary domain.\n\n"
        'Respond with JSON exactly of shape:\n'
        '{"startups": [{"name": "<CompanyName>", "domain": "<primary-domain>", '
        '"fit_rationale": "<two sentence specific fit rationale>"}]}',
        agent="runner.startups",
        offline={"startups": []},
    )
    picks = []
    for s in result.get("startups") or []:
        name = str(s.get("name", "")).strip()
        domain = str(s.get("domain", "")).strip().lower().rstrip("/")
        rationale = str(s.get("fit_rationale") or "").strip()
        for p in ("https://", "http://", "www."):
            if domain.startswith(p):
                domain = domain[len(p):]
        if name and domain and "." in domain:
            picks.append({"name": name, "domain": domain,
                          "fit_rationale": rationale})
    return picks


def _verify_and_enrich(cand: dict, vertical: str) -> dict[str, Any] | None:
    """Verify one LLM-named candidate against Apollo + fetch a real contact
    with an email. Returns a Research-shaped prospect dict, or None if
    Apollo can't resolve the org OR no contact-with-email exists."""
    from .research import apollo, email_guess, web as _web

    try:
        enrich = apollo.enrich_organization(cand["domain"])
    except apollo.ApolloError:
        return None
    if not enrich:
        return None

    best = {
        "name": enrich.get("name") or cand["name"],
        "domain": (enrich.get("primary_domain") or cand["domain"]).lower(),
        "employees": enrich.get("estimated_num_employees") or 0,
        "industry": enrich.get("industry") or vertical,
        "founded_year": enrich.get("founded_year"),
        "short_description": (enrich.get("short_description") or "")[:400],
        "linkedin_url": enrich.get("linkedin_url", ""),
    }

    # Contact discovery — filter aggressively: we ONLY accept a candidate
    # if we can produce a real, addressable email. A "beautiful shortlist
    # of unreachable companies" is worthless for outbound.
    contact_name = contact_title = ""
    email_candidates: list[str] = []
    try:
        contact = apollo.find_best_contact(best["domain"])
        if isinstance(contact, dict) and not contact.get("error"):
            contact_name = contact.get("name", "") or ""
            contact_title = contact.get("title", "") or ""
            if contact.get("email"):
                email_candidates = [contact["email"]]
    except apollo.ApolloError:
        pass

    # If Apollo gave us a name but no email, guess the top pattern — first-
    # party patterns hit ~70 % on well-known startups. Better than dropping
    # a real decision-maker on a technicality.
    if not email_candidates and contact_name:
        parts = contact_name.split()
        if len(parts) >= 2:
            email_candidates = [g["email"] for g in email_guess.guess(
                parts[0], parts[-1], best["domain"])[:3]]

    # HARD FILTER: no contact + no email → drop the candidate. The user's
    # feedback was explicit — a target the founder can't email is worse
    # than no target at all.
    if not contact_name or not email_candidates:
        return None

    # Homepage excerpt (pain-adjacent paragraph) — a nicer evidence
    # source than the Apollo short description alone.
    excerpt = ""
    try:
        page = _web.fetch(f"https://{best['domain']}")
        if page.get("text"):
            for para in page["text"].split("\n"):
                if 40 < len(para) < 300 and any(w in para.lower() for w in
                        ("we ", "our ", "platform", "healthcare", "patients",
                         "data", "compliance", "software")):
                    excerpt = para.strip()
                    break
    except Exception:
        pass

    return {
        "company_name": best["name"],
        "company_domain": best["domain"],
        "industry": best.get("industry", vertical),
        "employees": best["employees"],
        "founded_year": best.get("founded_year"),
        "short_description": best.get("short_description", ""),
        "contact": {
            "name": contact_name,
            "title": contact_title,
            "email_candidates": email_candidates,
            "linkedin_url": "",
        },
        "pain_evidence": [{
            "source_url": f"https://{best['domain']}",
            "excerpt": excerpt or best.get("short_description", ""),
        }] if (excerpt or best.get("short_description")) else [],
        "fit_score": 0.82,
        # LLM rationale takes precedence — it was written against the
        # founder's actual product briefing, not a generic vertical.
        "fit_rationale": cand.get("fit_rationale") or (
            f"Apollo-sourced {vertical} company, {best['employees']} employees "
            f"(startup-sized). Founded {best.get('founded_year','?')}."
        ),
    }


def _apollo_shortlist(brief: str, stage_cb,
                      founder_gist: str = "",
                      want: int = 3) -> list[dict[str, Any]]:
    """Deterministic prospect discovery — return up to ``want`` candidates
    each verified in Apollo AND with a real decision-maker + email. The
    founder picks between them; downstream agents only ever build for the
    chosen one."""
    if not settings.apollo_api_key:
        return []

    try:
        vertical, _tags = _classify_vertical(brief)
    except Exception:
        vertical = "healthtech"

    stage_cb("brainstorm",
             f"Brainstorming candidate {vertical} startups (gpt-4o) with "
             "fit rationales…")
    candidates = _llm_name_startups(brief, vertical, founder_gist=founder_gist)
    if not candidates:
        return []

    shortlist: list[dict[str, Any]] = []
    for cand in candidates:
        prospect = _verify_and_enrich(cand, vertical)
        if prospect is None:
            continue
        shortlist.append(prospect)
        stage_cb("apollo_pick",
                 f"Verified {len(shortlist)}/{want}: {prospect['company_name']} "
                 f"· {prospect['contact']['name']} ({prospect['contact']['title']})")
        if len(shortlist) >= want:
            break
    return shortlist


StageCb = Callable[[str, str], None] | None


@dataclass
class CampaignArtifacts:
    """Everything a front-end needs to present + act on one campaign."""

    ok: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    # prospect
    company: str = ""
    domain: str = ""
    contact_name: str = ""
    contact_title: str = ""
    recipient_email: str = ""
    fit_rationale: str = ""
    prospect: dict[str, Any] = field(default_factory=dict)

    # artifacts
    prototype_url: str = ""
    walkthrough_url: str = ""
    walkthrough_mp4: str = ""
    deck_url: str = ""
    deck_pptx: str = ""

    # outreach
    campaign_id: str = ""
    email_subject: str = ""
    email_body: str = ""

    cost_usd: float = 0.0


def find_shortlist(
    brief: str,
    founder_context: FounderContext,
    *,
    on_stage: StageCb = None,
    on_event: EventSink = None,
    want: int = 3,
) -> list[dict[str, Any]]:
    """Return a shortlist of up to ``want`` prospect dicts. Each candidate is
    Apollo-verified AND has a real decision-maker with an addressable email
    AND carries a specific two-sentence fit rationale grounded in the
    founder's product. The Telegram bot / CLI presents the founder with
    these picks so they can choose which one to build for — no downstream
    agent runs until the founder commits.
    """
    def stage(name: str, detail: str = "") -> None:
        if on_stage:
            try:
                on_stage(name, detail)
            except Exception:
                pass

    stage("research", "Hunting for fit prospects…")
    # Seed the LLM with what the founder actually sells — the fit rationale
    # is only useful if it names capabilities the product actually has.
    founder_gist = ""
    try:
        founder_gist = founder_context.summary()[:1200]
    except Exception:
        pass

    shortlist = _apollo_shortlist(brief, stage,
                                  founder_gist=founder_gist, want=want)
    if shortlist:
        return shortlist

    # Fallback for unusual verticals Apollo doesn't index well
    stage("research_llm",
          "Apollo didn't have strong matches — switching to open web recon.")
    from .research import Research
    res = Research().run_brief(
        f"{brief.strip()}\n\nTarget shortlist size: {want}.",
        on_event=on_event)
    return (res.get("prospects") or [])[:want]


def build_campaign_for(
    prospect: dict[str, Any],
    founder_context: FounderContext,
    *,
    on_stage: StageCb = None,
    on_event: EventSink = None,
    skip_lipsync: bool | None = None,
) -> CampaignArtifacts:
    """Run Engineer → Director → Sales for a pre-picked prospect.

    Split out of ``run_campaign`` so the Telegram bot can (1) show a shortlist,
    (2) let the founder pick between them, and (3) only spend prototype /
    walkthrough / deck cycles on the chosen one.
    """
    from ghost.llm import COST

    art = CampaignArtifacts()

    def stage(name: str, detail: str = "") -> None:
        if on_stage:
            try:
                on_stage(name, detail)
            except Exception:
                pass

    # A fresh chain = a fresh live-console story.
    bridge.new_run()

    art.prospect = prospect
    art.company = prospect.get("company_name", "")
    art.domain = prospect.get("company_domain", "")
    contact = prospect.get("contact") or {}
    art.contact_name = contact.get("name", "")
    art.contact_title = contact.get("title", "")
    art.fit_rationale = prospect.get("fit_rationale", "")
    emails = contact.get("email_candidates") or []
    art.recipient_email = emails[0] if emails else ""
    stage("research_done", f"Target: {art.company}"
          + (f" · {art.contact_name}" if art.contact_name else ""))

    # ── 2. Engineer ───────────────────────────────────────────
    stage("engineer", f"Building a prototype for {art.company}…")
    from .engineer import Engineer
    from .engineer.fallback import render_fallback_html
    from .engineer.cf_pages import deploy_dir
    from .engineer.prototype import PrototypeState
    from pathlib import Path as _Path

    eng = Engineer(founder_context=founder_context, prospect=prospect)
    ebuild = eng.build(on_event=on_event)
    art.prototype_url = ebuild.get("url", "")
    files = ebuild.get("files") or []

    # Deterministic guarantee: if the LLM skipped write_prototype_file, or if
    # deploy fell back to file://, ship the template fallback + re-deploy.
    needs_fallback = (
        "index.html" not in files
        or not art.prototype_url
        or art.prototype_url.startswith("file:")
    )
    if needs_fallback:
        stage("engineer_fallback",
              "LLM prototype didn't ship — using the guaranteed template.")
        product_gist = ""
        try:
            product_gist = founder_context.summary()[:600]
        except Exception:
            pass
        html = render_fallback_html(prospect, product_gist=product_gist)
        pstate = PrototypeState.for_prospect(art.company)
        pstate.write("index.html", html)
        deploy = deploy_dir(pstate.workspace)
        art.prototype_url = deploy.get("url", "") or art.prototype_url
        if deploy.get("warning"):
            art.warnings.append(f"engineer: {deploy['warning']}")

    if not art.prototype_url or art.prototype_url.startswith("file:"):
        art.warnings.append("prototype deployed locally only (no public URL)")
    stage("engineer_done", art.prototype_url or "(local only)")

    # ── 3. Director ───────────────────────────────────────────
    stage("director", "Filming the walkthrough…")
    if skip_lipsync is not None:
        import os
        os.environ["DIRECTOR_SKIP_LIPSYNC"] = "1" if skip_lipsync else "0"
        from ghost.config import get_settings
        get_settings.cache_clear()
    from .director import Director

    d = Director(prototype_url=art.prototype_url or "https://example.com",
                 prospect=prospect)
    dfilm = d.film(on_event=on_event)
    art.walkthrough_url = dfilm.get("video_url", "")
    art.walkthrough_mp4 = dfilm.get("mp4_path", "")

    # Defense-in-depth: if the Director agent errored before calling
    # `render_walkthrough` (auth failure, LLM outage, tool-call quirk),
    # synthesize a deterministic 4-beat script grounded in the prospect
    # brief and film directly. Beats deliberately mirror the canonical
    # actionable sequence — scroll → type → click → scroll — so the
    # walkthrough is watchable even without a live LLM.
    if not art.walkthrough_mp4:
        stage("director_fallback",
              "Director agent didn't ship a film — using template beats.")
        try:
            from .director.tools import action_tools as _director_action_tools
            beats = _fallback_beats(prospect,
                                    prototype_url=art.prototype_url)
            tools = _director_action_tools(d.state,
                                           art.prototype_url or "https://example.com")
            render = next(t for t in tools if t.name == "render_walkthrough")
            result = render.fn(beats=beats, presenter_name="Revenant")
            if isinstance(result, dict) and result.get("mp4_path"):
                art.walkthrough_url = result.get("video_url", "")
                art.walkthrough_mp4 = result.get("mp4_path", "")
            else:
                art.warnings.append(
                    f"director fallback failed: {result.get('error') if isinstance(result, dict) else result}")
        except Exception as exc:
            art.warnings.append(f"director fallback exception: {exc}")

    if not art.walkthrough_url or art.walkthrough_url.startswith("file:"):
        art.warnings.append("walkthrough hosted locally only")
    stage("director_done", "walkthrough ready")

    # ── 3.5 Embed the walkthrough (+ interactive agent) INTO the
    #        prospect's prototype and redeploy, so the one URL we hand the
    #        prospect has the demo, the narrated video, and the AI rep. ──
    try:
        _embed_media_into_prototype(art, stage)
    except Exception as exc:
        art.warnings.append(f"media embed skipped: {exc}")

    # ── 4. Sales ──────────────────────────────────────────────
    stage("sales", "Drafting the deck + email…")
    from .sales import Sales

    s = Sales(founder_context=founder_context, prospect=prospect,
              prototype_url=art.prototype_url,
              walkthrough_url=art.walkthrough_url)
    sdraft = s.draft(on_event=on_event)
    art.campaign_id = sdraft.get("campaign_id", "")
    art.email_subject = s.state.email_subject
    art.email_body = s.state.email_body
    art.deck_url = sdraft.get("deck_url", "")
    art.deck_pptx = sdraft.get("deck_pptx_path", "")

    # Defense-in-depth: if the LLM never called `save_draft` (auth failure,
    # tool-call quirk, max-iters), synthesize a template email so the
    # founder always has SOMETHING to review — never an empty draft.
    if not art.email_body.strip():
        stage("sales_fallback",
              "Sales agent didn't ship a draft — using template.")
        art.email_subject, art.email_body = _fallback_email(
            prospect=prospect,
            founder_context=founder_context,
            prototype_url=art.prototype_url,
            walkthrough_url=art.walkthrough_url,
            deck_url=art.deck_url)
        art.warnings.append(
            "sales: LLM didn't produce a draft; template email generated. "
            "Check `sales_done` event for the error and rerun once the LLM is back.")

    stage("sales_done", "Draft ready for review.")

    art.cost_usd = round(COST.cents / 100, 4)
    art.ok = True
    stage("done", art.company)
    return art


def _fallback_beats(prospect: dict[str, Any], *,
                    prototype_url: str) -> list[dict[str, Any]]:
    """Deterministic 4-beat walkthrough script. Used when the Director agent
    fails to compose beats (LLM auth failure, outage, etc.). Beats reference
    the canonical prototype element ids (#inputText, #redactBtn, #outputText,
    #code, #cta) that Engineer's prompts + fallback template both produce.
    """
    company = prospect.get("company_name") or "your team"
    industry = prospect.get("industry") or "your space"
    founder_company = settings.founder_company or "Shroud"
    return [
        {
            "narration": (f"Hi — this is a working prototype of {founder_company} "
                           f"built specifically for {company}. Under two minutes, "
                           "then you can poke at it yourself."),
            "action": {"type": "hold"},
            "hold_ms": 900,
        },
        {
            "narration": (f"Every team in {industry} handles data that shouldn't "
                           "leak. Here's what that looks like on a real record — "
                           "let me paste it in."),
            "action": {"type": "type", "selector": "#inputText",
                       "text": ("Patient MRN 4471029 · Card 4111 1111 1111 1234 · "
                                "Total USD $8,420.00 · john.doe@example.com · "
                                "SSN 123-45-6789")},
            "hold_ms": 900,
        },
        {
            "narration": ("One click — and every identifier is redacted in place. "
                           "Same schema, same fields, zero PII."),
            "action": {"type": "click",
                       "selector": "#redactBtn, button:has-text('Redact')"},
            "hold_ms": 900,
        },
        {
            "narration": ("Two lines of code to wire it into your stack — same "
                           "call whether it's a support handoff, an analytics "
                           "pipeline, or a model prompt."),
            "action": {"type": "scroll_to",
                       "selector": "#code, pre code, #outputText"},
            "hold_ms": 900,
        },
        {
            "narration": (f"That's it. Reply if you want a private staging URL "
                          f"you can hit from your API this week."),
            "action": {"type": "scroll_to",
                       "selector": "#cta, footer, a[href*='pilot']"},
            "hold_ms": 900,
        },
    ]


def _fallback_email(*, prospect: dict[str, Any],
                    founder_context: FounderContext,
                    prototype_url: str, walkthrough_url: str,
                    deck_url: str) -> tuple[str, str]:
    """Emergency template email when Sales's LLM loop didn't ship a draft.
    Not as good as an LLM-composed pitch — but it names the prospect, cites
    real evidence, links every artifact, and signs off with the founder's
    real name. Better than a blank draft."""
    company = prospect.get("company_name", "your team")
    contact = prospect.get("contact") or {}
    person = contact.get("name") or ""
    first_name = person.split()[0] if person else ""
    greeting = f"Hi {first_name}," if first_name else "Hi there,"

    excerpts = prospect.get("pain_evidence") or []
    excerpt = (excerpts[0].get("excerpt") if excerpts else "") or ""
    excerpt = excerpt.strip().rstrip(".")

    founder_first = settings.founder_name.split()[0] if settings.founder_name else "Himanshu"
    founder_company = settings.founder_company or "Shroud"

    fit = (prospect.get("fit_rationale") or "").strip().rstrip(".")

    links = []
    if walkthrough_url:
        links.append(f"Walkthrough (90s): {walkthrough_url}")
    if prototype_url:
        links.append(f"Live prototype: {prototype_url}")
    if deck_url:
        links.append(f"Deck: {deck_url}")
    link_block = "\n".join(links)

    subject = f"{company} — a working prototype for you, not a pitch"
    body_parts = [
        greeting, "",
        f"I've been watching {company} for a bit. {fit}." if fit
        else f"I spent this morning looking closely at {company}.",
    ]
    if excerpt:
        body_parts.append(
            f"You wrote — \"{excerpt[:180]}\" — that's exactly the shape of the "
            f"problem {founder_company} was built for.")
    body_parts.append(
        f"So instead of pitching, I built you a working prototype on your kind "
        f"of data. Everything below is live, tailored, and yours to poke at.")
    if link_block:
        body_parts.extend(["", link_block, ""])
    body_parts.append(
        f"15 minutes this week to walk you through it live on a sample of "
        f"your data? Reply \"yes\" and I'll send a slot.")
    body_parts.extend(["", founder_first])
    return subject, "\n".join(body_parts).strip()


def run_campaign(
    brief: str,
    founder_context: FounderContext,
    *,
    on_stage: StageCb = None,
    on_event: EventSink = None,
    skip_lipsync: bool | None = None,
) -> CampaignArtifacts:
    """Legacy one-shot: find a shortlist and auto-pick the top candidate.

    Preserved for the cron / autopilot loop where there's no human in the
    loop to pick between the shortlist. Interactive front-ends (Telegram)
    should call ``find_shortlist`` + ``build_campaign_for`` directly so the
    founder picks between the three options.
    """
    art = CampaignArtifacts()
    shortlist = find_shortlist(brief, founder_context,
                               on_stage=on_stage, on_event=on_event, want=3)
    if not shortlist:
        art.error = ("No fit prospect surfaced for that brief. Try a "
                     "different vertical or looser signal.")
        if on_stage:
            try:
                on_stage("failed", art.error)
            except Exception:
                pass
        return art
    return build_campaign_for(shortlist[0], founder_context,
                              on_stage=on_stage, on_event=on_event,
                              skip_lipsync=skip_lipsync)


def redraft_email(art: CampaignArtifacts, amendment: str,
                  founder_context: FounderContext,
                  *, on_event: EventSink = None) -> CampaignArtifacts:
    """Re-run Sales with a founder amendment, reusing the existing prototype,
    walkthrough, and deck. Returns the updated artifacts (same campaign id)."""
    from .sales import Sales

    s = Sales(founder_context=founder_context, prospect=art.prospect,
              prototype_url=art.prototype_url,
              walkthrough_url=art.walkthrough_url)
    s.draft(on_event=on_event, extra_instruction=(
        "The founder reviewed your previous draft and asked for this change:\n"
        f"“{amendment}”\n"
        "Rewrite the email accordingly. Keep the deck as-is unless the change "
        "clearly requires new slides."))
    art.email_subject = s.state.email_subject
    art.email_body = s.state.email_body
    if s.state.deck_url:
        art.deck_url = s.state.deck_url
    if s.state.convex_id:
        art.campaign_id = s.state.convex_id
    return art
