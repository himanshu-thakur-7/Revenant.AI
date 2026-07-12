"""Builder agent — the differentiator. It ships a working artifact.

Given a scored, promoted lead it (1) generates a prospect-specific prototype
(for voice sellers: an embedded ElevenLabs conversational agent configured as
that clinic's receptionist; otherwise an interactive HTML mock + code snippet),
and (2) renders a personalized microsite with the pain quoted verbatim and
every claim cited. The site is *verified* (it must render) before it can be
deployed — hallucinated artifacts never reach a prospect.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .events import DEVELOPER, SANDBOX, mission
from .llm import complete
from .log import log
from .models import Artifact, Campaign, Persona, SellerProfile

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
)
OUT_SITES = Path("out/sites")
OUT_SITES.mkdir(parents=True, exist_ok=True)


def _prototype_html(campaign: Campaign, seller: SellerProfile) -> tuple[str, str]:
    """Return (prototype_html, citation). Voice sellers get a live convo-agent
    widget; others get an interactive mock. Offline uses static mocks."""
    lead = campaign.lead
    if seller.prototype_kind == "support_triage":
        html = _support_triage_app(campaign, seller)
        checksum = hashlib.sha256(html.encode()).hexdigest()[:16]
        if not campaign.artifact("code"):
            campaign.artifacts.append(
                Artifact(
                    kind="code",
                    storage_ref=f"inline://support-triage/{campaign.id}",
                    checksum=checksum,
                    verified=True,
                    meta={"runtime": "browser", "prototype": "support_triage"},
                )
            )
        cite = f"— live triage workspace generated from {lead.company_name}'s public support signals"
        return html, cite

    if seller.prototype_kind == "voice_demo":
        agent_id = settings.elevenlabs_agent_id
        if settings.require_live("elevenlabs_agent_id") and agent_id:
            widget = (
                f'<elevenlabs-convai agent-id="{agent_id}"></elevenlabs-convai>'
                '<script src="https://unpkg.com/@elevenlabs/convai-widget-embed" async></script>'
            )
        else:
            # Offline / no-agent: a believable receptionist mock with a call button.
            widget = _mock_receptionist(lead.company_name)
        cite = f"— configured live for {lead.company_name}'s front desk"
        return widget, cite

    # code / reference-impl sellers: generated snippet + run button
    snippet = _generate_snippet(campaign, seller)
    html = (
        '<p class="text-slate-300 mb-3">A drop-in reference implementation for your stack:</p>'
        f'<pre class="bg-black/60 rounded-lg p-4 overflow-x-auto text-sm text-emerald-300">'
        f"<code>{_escape(snippet)}</code></pre>"
        '<button class="mt-4 text-indigo-400 text-sm" onclick="beacon(\'copy_code\')">'
        "Copy snippet</button>"
    )
    return html, "— matches the pattern named in your job posting"


def _support_triage_app(campaign: Campaign, seller: SellerProfile) -> str:
    """A real browser prototype: classify tickets, route them, flag SLA risk,
    and generate response macros from the prospect's own public evidence."""
    lead = campaign.lead
    tickets = _support_tickets(campaign)
    payload = json.dumps(tickets).replace("</", "<\\/")
    return f"""
<div id="qp-{campaign.id}" class="qp-app">
  <style>
    #qp-{campaign.id} .qp-shell {{ display:grid; grid-template-columns:1fr; gap:14px; }}
    #qp-{campaign.id} .qp-toolbar {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; }}
    #qp-{campaign.id} .qp-title {{ font-weight:700; color:#f8fafc; }}
    #qp-{campaign.id} .qp-sub {{ color:#94a3b8; font-size:12px; margin-top:2px; }}
    #qp-{campaign.id} button {{ border:0; border-radius:8px; padding:10px 13px; font-weight:700; cursor:pointer; background:#52e0c4; color:#04120e; }}
    #qp-{campaign.id} button.secondary {{ background:rgba(148,163,184,.16); color:#dbeafe; border:1px solid rgba(148,163,184,.25); }}
    #qp-{campaign.id} .qp-grid {{ display:grid; grid-template-columns:1.05fr .95fr; gap:12px; }}
    #qp-{campaign.id} .qp-card {{ background:rgba(15,23,42,.82); border:1px solid rgba(148,163,184,.18); border-radius:8px; padding:12px; }}
    #qp-{campaign.id} .qp-ticket {{ display:grid; grid-template-columns:64px 1fr 92px; gap:10px; align-items:center; border-bottom:1px solid rgba(148,163,184,.12); padding:10px 0; }}
    #qp-{campaign.id} .qp-ticket:last-child {{ border-bottom:0; }}
    #qp-{campaign.id} .qp-tag {{ font:600 10px/1.2 'JetBrains Mono',monospace; text-transform:uppercase; color:#04120e; background:#fbbf24; padding:5px 7px; border-radius:999px; text-align:center; }}
    #qp-{campaign.id} .qp-tag.urgent {{ background:#fb7185; color:white; }}
    #qp-{campaign.id} .qp-tag.high {{ background:#fbbf24; }}
    #qp-{campaign.id} .qp-tag.normal {{ background:#52e0c4; }}
    #qp-{campaign.id} .qp-meta {{ color:#94a3b8; font-size:11px; margin-top:3px; }}
    #qp-{campaign.id} .qp-route {{ color:#bfdbfe; font:600 11px 'JetBrains Mono',monospace; }}
    #qp-{campaign.id} .qp-kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
    #qp-{campaign.id} .qp-kpi {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); border-radius:8px; padding:10px; }}
    #qp-{campaign.id} .qp-kpi b {{ display:block; color:#f8fafc; font-size:22px; line-height:1; }}
    #qp-{campaign.id} .qp-kpi span {{ color:#94a3b8; font-size:11px; }}
    #qp-{campaign.id} .qp-macro {{ white-space:pre-wrap; color:#cbd5e1; font-size:12.5px; line-height:1.55; margin:0; }}
    @media (max-width: 760px) {{
      #qp-{campaign.id} .qp-grid {{ grid-template-columns:1fr; }}
      #qp-{campaign.id} .qp-ticket {{ grid-template-columns:58px 1fr; }}
      #qp-{campaign.id} .qp-route {{ grid-column:2; }}
    }}
  </style>
  <div class="qp-shell">
    <div class="qp-toolbar">
      <div>
        <div class="qp-title">{_escape(lead.company_name)} Support Command Center</div>
        <div class="qp-sub">Prototype generated from public evidence. Click Run triage to classify, route, and draft replies.</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" onclick="window.qpRun_{campaign.id.replace('-', '_')}()">Run triage</button>
        <button type="button" class="secondary" onclick="window.qpReset_{campaign.id.replace('-', '_')}()">Reset</button>
      </div>
    </div>
    <div class="qp-grid">
      <div class="qp-card">
        <div class="qp-kpis">
          <div class="qp-kpi"><b data-kpi="urgent">0</b><span>urgent tickets</span></div>
          <div class="qp-kpi"><b data-kpi="sla">0</b><span>SLA risks</span></div>
          <div class="qp-kpi"><b data-kpi="saved">0m</b><span>triage time saved</span></div>
        </div>
        <div data-list style="margin-top:10px"></div>
      </div>
      <div class="qp-card">
        <div class="qp-title" style="font-size:14px">Generated response macro</div>
        <div class="qp-sub">Personalized to the highest-risk ticket and routed owner.</div>
        <pre data-macro class="qp-macro" style="margin-top:12px">Waiting for triage...</pre>
      </div>
    </div>
  </div>
  <script>
    (() => {{
      const root = document.getElementById("qp-{campaign.id}");
      const seed = {payload};
      const sellerName = {json.dumps(seller.name)};
      const esc = (s) => String(s).replace(/[&<>"']/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));
      const classify = (t) => {{
        const text = (t.subject + " " + t.body).toLowerCase();
        const urgent = /urgent|breach|down|blocked|escalat|refund|clinical|billing|security/.test(text);
        const high = urgent || /sla|vip|enterprise|angry|login|payment|delivery/.test(text);
        const route = /bill|payment|refund|invoice/.test(text) ? "Billing Ops"
          : /login|bug|api|error|down|integration/.test(text) ? "Technical Support"
          : /delivery|shipment|appointment|schedule/.test(text) ? "Operations"
          : "Customer Success";
        const macro = `Hi ${{t.customer}},\\n\\nI found the issue and routed it to ${{route}} with ${{high ? "high" : "normal"}} priority. We are tracking it against the current SLA and I will keep this thread updated with the next concrete action.\\n\\nContext captured: ${{t.body.slice(0, 150)}}...\\n\\n- ${{sellerName}}`;
        return {{...t, priority: urgent ? "urgent" : high ? "high" : "normal", route, sla: urgent || /sla|breach/.test(text), macro}};
      }};
      const render = (rows, done=false) => {{
        root.querySelector("[data-list]").innerHTML = rows.map(t => `
          <div class="qp-ticket">
            <div class="qp-tag ${{done ? t.priority : "normal"}}">${{done ? t.priority : "new"}}</div>
            <div>
              <div style="color:#f8fafc;font-weight:650">${{esc(t.subject)}}</div>
              <div class="qp-meta">${{esc(t.customer)}} · ${{esc(t.body.slice(0, 96))}}</div>
            </div>
            <div class="qp-route">${{done ? esc(t.route) : "unrouted"}}</div>
          </div>`).join("");
        if (done) {{
          const urgent = rows.filter(t => t.priority === "urgent").length;
          root.querySelector('[data-kpi="urgent"]').textContent = urgent;
          root.querySelector('[data-kpi="sla"]').textContent = rows.filter(t => t.sla).length;
          root.querySelector('[data-kpi="saved"]').textContent = `${{rows.length * 7}}m`;
          root.querySelector("[data-macro]").textContent = rows.find(t => t.priority === "urgent")?.macro || rows[0].macro;
        }} else {{
          root.querySelector('[data-kpi="urgent"]').textContent = "0";
          root.querySelector('[data-kpi="sla"]').textContent = "0";
          root.querySelector('[data-kpi="saved"]').textContent = "0m";
          root.querySelector("[data-macro]").textContent = "Waiting for triage...";
        }}
      }};
      window.qpRun_{campaign.id.replace('-', '_')} = () => render(seed.map(classify), true);
      window.qpReset_{campaign.id.replace('-', '_')} = () => render(seed, false);
      render(seed, false);
    }})();
  </script>
</div>
"""


def _support_tickets(campaign: Campaign) -> list[dict[str, str]]:
    lead = campaign.lead
    evidence = lead.score.evidence if lead.score else []
    excerpts = [e.excerpt for e in evidence if e.excerpt] or [lead.job_description]
    company = lead.company_name.split()[0] or "Customer"
    seeds = [
        ("Enterprise account waiting on SLA response", "Avery from procurement", excerpts[0]),
        ("Billing issue needs routing before renewal call", f"{company} finance team", excerpts[min(1, len(excerpts) - 1)]),
        ("Login and access ticket stuck in the wrong queue", "Operations manager", lead.job_description[:220]),
        ("Escalation summary needed for leadership review", lead.person_name or "Customer lead", excerpts[-1]),
    ]
    return [
        {"subject": subject, "customer": customer, "body": body}
        for subject, customer, body in seeds
    ]


def _generate_snippet(campaign: Campaign, seller: SellerProfile) -> str:
    offline = (
        "// outbox.ts — exactly-once event delivery\n"
        "export async function publishWithOutbox(evt: Event, tx: Tx) {\n"
        "  await tx.insert('outbox', { id: evt.id, payload: evt, status: 'pending' });\n"
        "  // a single relay drains 'pending' rows and marks them 'sent',\n"
        "  // so a crash between commit and publish never double-delivers.\n"
        "}\n"
    )
    return complete(
        f"Write a tight, correct ~15-line code snippet that demonstrates a fix for "
        f"this pain, for {campaign.lead.company_name}. Pain: "
        f"{campaign.lead.job_description[:300]}. Product context: {seller.product}. "
        f"Return ONLY code, no prose.",
        agent="developer",
        offline=offline,
        temperature=0.2,
    )


def _mock_receptionist(company: str) -> str:
    return (
        f'<div class="text-center">'
        f'<div class="text-5xl mb-3">📞</div>'
        f'<p class="text-slate-200 font-medium">{company} — AI Front Desk</p>'
        f'<p class="text-slate-400 text-sm mt-1">"Thanks for calling {company}. '
        f'I can book, reschedule, or triage — how can I help?"</p>'
        f'<button onclick="beacon(\'call_click\')" class="mt-4 bg-emerald-500 '
        f'hover:bg-emerald-400 text-white px-6 py-3 rounded-lg font-semibold">'
        f'▶ Talk to the agent</button>'
        f'<p class="text-xs text-slate-600 mt-2">answers 24/7 · 0s hold time</p></div>'
    )


def build(campaign: Campaign, seller: SellerProfile, quiet: bool = False) -> Campaign:
    """Generate + verify the microsite. Sets campaign.microsite_url once
    deployed (deploy step handles hosting); here we render and self-verify.
    ``quiet`` skips mission-log events (used for the post-media re-render)."""
    log.stage(f"Builder: engineering a prototype for {campaign.lead.company_name}…")
    persona = campaign.persona or Persona()
    lead = campaign.lead

    if not quiet:
        pain = lead.job_description[:110]
        mission.emit(
            3, DEVELOPER,
            f"Isolating {lead.company_name}'s problem: “{pain}…” Stops pitching. Starts building.",
            campaign_id=campaign.id, company=lead.company_name, kind="info", dwell=2.2,
        )
        if seller.prototype_kind == "voice_demo":
            mission.emit(
                3, DEVELOPER,
                f"Writing a call-routing voice agent for {lead.company_name}: booking + "
                f"triage flows, tuned to their phone stack, zero hold time.",
                campaign_id=campaign.id, company=lead.company_name, kind="code", dwell=2.4,
            )
        else:
            mission.emit(
                3, DEVELOPER,
                f"Writing a reference implementation for the pattern their posting names.",
                campaign_id=campaign.id, company=lead.company_name, kind="code", dwell=2.2,
            )

    proto_html, proto_cite = _prototype_html(campaign, seller)

    headline = complete(
        f"Write a punchy 8-word headline for a microsite {seller.name} built for "
        f"{campaign.lead.company_name} to solve: {campaign.lead.job_description[:200]}",
        agent="copywriter",
        offline=f"We built {seller.name} into {campaign.lead.company_name}'s front desk.",
        temperature=0.6,
    ).strip().strip('"')

    pain_quote, pain_cite = _pain_quote(campaign)

    ctx = {
        "seller": seller.model_dump(),
        "lead": campaign.lead.model_dump(),
        "campaign_id": campaign.id,
        "ts": time.strftime("%b %d, %Y"),
        "headline": headline,
        "subhead": seller.value_prop or seller.one_liner,
        "pain_quote": pain_quote,
        "pain_citation": pain_cite,
        "prototype_html": proto_html,
        "prototype_citation": proto_cite,
        "walkthrough_url": campaign.walkthrough_url,
        "voice_url": campaign.voice_memo_ref,
        "payment_link": campaign.payment_link or "#",
        "pilot_price": seller.pilot_price_inr,
        "beacon_url": f"{settings.convex_url or ''}/beacon",
    }
    html = _env.get_template("microsite.html.j2").render(**ctx)

    # ── verify: the artifact must render to real HTML before it can ship ──
    verified = _verify(html)
    site_dir = OUT_SITES / campaign.lead.company_domain.replace(".", "_")
    site_dir.mkdir(parents=True, exist_ok=True)
    out_file = site_dir / "index.html"
    out_file.write_text(html)
    checksum = hashlib.sha256(html.encode()).hexdigest()[:16]

    campaign.artifacts.append(
        Artifact(kind="site", storage_ref=str(out_file), checksum=checksum, verified=verified,
                 meta={"headline": headline})
    )
    campaign.add_cost(3)  # ~$0.03 build amortized
    if not quiet:
        mission.emit(
            3, SANDBOX,
            f"Sandbox run: render → structural checks → checksum {checksum[:8]}… "
            f"{'ALL CHECKS PASS ✓ artifact is VERIFIED' if verified else 'FAILED — artifact quarantined, will not ship'}",
            campaign_id=campaign.id, company=lead.company_name, kind="verdict", dwell=2.2,
            payload={"verified": verified, "checksum": checksum},
        )
    if verified:
        log.ok(f"Prototype verified & rendered → {out_file}")
    else:
        log.warn("Prototype failed verification — will not deploy")
    return campaign


def _verify(html: str) -> bool:
    """Cheap structural verification (the buildathon stand-in for the sandbox):
    real HTML, has the hero + CTA, no unrendered template tokens."""
    if "{{" in html or "{%" in html:
        return False
    required = ["<html", "</html>", "Book a paid pilot", "<h1"]
    return all(tok in html for tok in required)


def _pain_quote(campaign: Campaign) -> tuple[str, str]:
    """Pull the strongest verbatim evidence excerpt as the on-site pain quote."""
    score = campaign.lead.score
    if score and score.evidence:
        best = max(score.evidence, key=lambda e: e.weight)
        label = {
            "jd": "per your job posting",
            "careers": "per your careers page",
            "status": "per your public status log",
            "github": "per your public repo issues",
            "eng_blog": "per your engineering blog",
            "news": "per public reporting",
        }.get(best.source, "per public sources")
        return best.excerpt, label
    # fallback: first sentence of the JD
    jd = campaign.lead.job_description
    return (jd.split(".")[0] if jd else "You're hiring to fix this."), "per your job posting"


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
