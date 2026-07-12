"""Deterministic fallback prototype — guarantees an index.html always ships.

The LLM Engineer is authoritative when it works, but Nous occasionally
skips ``write_prototype_file`` or writes nonsense. For a live demo we
cannot serve an empty deploy, so this module renders a strong,
prospect-personalised prototype from a fixed template — same brand as the
Shroud site, same interactive redaction demo, same three fit bullets.

The template pulls (a) the prospect's company + contact + evidence from the
brief, and (b) a summary paragraph the LLM can compose fast (or a stubbed
value) — so we always have something high-quality to deploy even when the
Engineer LLM loop fails.
"""

from __future__ import annotations

import html
from typing import Any


def render_fallback_html(prospect: dict[str, Any], product_gist: str = "") -> str:
    """Render a personalised single-page HTML prototype. Never raises."""
    company = html.escape(prospect.get("company_name") or "your company")
    contact = prospect.get("contact") or {}
    person = html.escape((contact.get("name") or "").split()[0] if contact.get("name") else "team")
    industry = html.escape((prospect.get("industry") or "your industry"))
    excerpt = ""
    for ev in (prospect.get("pain_evidence") or [])[:1]:
        if ev.get("excerpt"):
            excerpt = html.escape(ev["excerpt"])[:280]
            break
    fit_line = html.escape(prospect.get("fit_rationale") or "")[:300]
    gist = html.escape(product_gist)[:600] if product_gist else \
        "A drop-in HTTP API that redacts and tokenizes 28 categories of PII, PHI, and PCI data — before it hits your logs, warehouses, or LLM prompts."

    return _TEMPLATE.format(
        company=company, person=person, industry=industry,
        excerpt=excerpt, fit_line=fit_line, gist=gist,
    )


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Shroud × {company}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {{ --bg:#05060a; --panel:rgba(148,163,184,0.05); --line:rgba(148,163,184,0.14);
             --ink:#e6ebf5; --muted:#8a94a8; --accent:#7ee0c6; --danger:#ff6b7a; }}
    body {{ margin:0; background: radial-gradient(1000px 700px at 80% -10%, rgba(126,224,198,0.08), transparent 60%), var(--bg);
           color:var(--ink); font-family:Inter,system-ui,sans-serif; line-height:1.55; -webkit-font-smoothing:antialiased; }}
    .container {{ max-width:1000px; margin:0 auto; padding:0 24px; }}
    header {{ padding:22px 0; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }}
    .logo {{ font-weight:800; letter-spacing:-0.02em; font-size:20px; }}
    .hero {{ padding:80px 0 40px; }}
    .eyebrow {{ font-family:"JetBrains Mono",monospace; font-size:11px; letter-spacing:0.28em; color:var(--accent); text-transform:uppercase; margin-bottom:22px; }}
    h1 {{ font-size:clamp(38px, 6vw, 60px); font-weight:800; letter-spacing:-0.03em; line-height:1.02; margin:0 0 24px; }}
    h1 .strike {{ text-decoration:line-through; color:var(--muted); }}
    .sub {{ font-size:clamp(17px, 1.6vw, 20px); color:var(--muted); max-width:640px; margin:0 0 40px; }}
    .demo {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:26px; margin:24px 0 60px;
            font-family:"JetBrains Mono",monospace; font-size:14px; }}
    .demo-lbl {{ color:var(--muted); font-size:11px; letter-spacing:0.18em; margin-bottom:10px; }}
    textarea {{ width:100%; height:130px; background:#0a0d15; border:1px solid var(--line); border-radius:8px; padding:14px;
                font-family:"JetBrains Mono",monospace; color:var(--ink); font-size:13px; resize:vertical; }}
    .btn {{ display:inline-block; padding:12px 22px; border-radius:10px; font-weight:600; font-size:15px; border:1px solid transparent;
            cursor:pointer; transition:transform .12s ease; background:var(--accent); color:#05130f; margin-top:14px; }}
    .btn:hover {{ transform: translateY(-1px); }}
    #out {{ background:#0a0d15; border:1px solid var(--line); border-radius:8px; padding:14px; margin-top:14px; min-height:130px;
            font-family:"JetBrains Mono",monospace; font-size:13px; white-space:pre-wrap; }}
    .redacted {{ background:rgba(126,224,198,.14); color:var(--accent); padding:1px 4px; border-radius:3px; }}
    section {{ padding:70px 0; border-top:1px solid var(--line); }}
    .grid-3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:22px; margin-top:34px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:24px; }}
    .card h3 {{ margin:0 0 10px; font-size:17px; }}
    .card p {{ margin:0; color:var(--muted); font-size:14px; }}
    .quote {{ border-left:2px solid var(--accent); padding:16px 20px; background:var(--panel); border-radius:8px; margin-top:18px;
              color:var(--ink); font-style:italic; }}
    footer {{ padding:36px 0 60px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; text-align:center; }}
    @media (max-width:720px) {{ .grid-3 {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <header class="container">
    <div class="logo">SHROUD × {company}</div>
    <span class="eyebrow" style="margin:0;">for {person}</span>
  </header>

  <section class="hero container">
    <div class="eyebrow">a working prototype for {company}</div>
    <h1>Log everything.<br /><span class="strike">Leak nothing.</span></h1>
    <p class="sub">{gist}</p>

    <div class="demo">
      <div class="demo-lbl">TRY IT ON A {industry} LOG LINE</div>
      <textarea id="in">Patient Priya Nair (DOB 1988-04-11, MRN 8471293) called from +91 98450 12233 about her prescription. Card ending 4111 charged $42.50. SSN 123-45-6789 on file.</textarea>
      <button class="btn" onclick="run()">Redact</button>
      <div class="demo-lbl" style="margin-top:22px;">CLEANED BEFORE IT HITS YOUR PIPELINE</div>
      <div id="out">Click Redact — sample output will appear here.</div>
    </div>
  </section>

  <section class="container">
    <div class="eyebrow">why {company}</div>
    <h2 style="font-size:32px; font-weight:800; letter-spacing:-.02em; margin:0 0 6px;">Three ways this pays off</h2>
    <div class="grid-3">
      <div class="card"><h3>Compliance without ceremony</h3><p>SOC 2, HIPAA, PCI — one API call handles what a consultant charges $50K to spec.</p></div>
      <div class="card"><h3>Ship LLM features safely</h3><p>Strip PII before prompts. Your users' data never trains someone else's model.</p></div>
      <div class="card"><h3>Cheap by design</h3><p>~15 ms p95. Priced per-character, not per-seat. Hobby tier is free forever.</p></div>
    </div>
    {evidence_block}
    {fit_block}
  </section>

  <footer class="container">
    Built autonomously for <b>{company}</b> · Nothing on this page is production data · <a href="https://shroud-site.pages.dev" style="color:var(--accent);">shroud-site.pages.dev</a>
  </footer>

  <script>
    const PATTERNS = [
      {{ rx: /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{{2,}}\b/g, ph: '[EMAIL]' }},
      {{ rx: /\b\d{{3}}-\d{{2}}-\d{{4}}\b/g, ph: '[SSN]' }},
      {{ rx: /\bMRN[:\s#-]*\d{{6,10}}\b/gi, ph: '[MRN]' }},
      {{ rx: /\+?\d{{1,3}}[\s.-]?\(?\d{{2,4}}\)?[\s.-]?\d{{3,4}}[\s.-]?\d{{3,4}}/g, ph: '[PHONE]' }},
      {{ rx: /(?:USD|\$|₹|€)\s?\d{{1,3}}(?:[,\s]\d{{3}})*(?:\.\d+)?/g, ph: '[AMOUNT]' }},
      {{ rx: /\b\d{{4}}-\d{{2}}-\d{{2}}\b/g, ph: '[DATE]' }},
      {{ rx: /\bcard\s+ending\s+\d{{4}}\b/gi, ph: 'card ending [CARD]' }},
      {{ rx: /\b(?!Patient|The|From|Dear)[A-Z][a-z]+\s+(?!Street|Ave|Road)[A-Z][a-z]+\b/g, ph: '[NAME]' }},
    ];
    function run() {{
      let s = document.getElementById('in').value;
      const found = new Set();
      for (const p of PATTERNS) {{
        s = s.replace(p.rx, m => {{ found.add(p.ph); return `<span class="redacted">${{p.ph}}</span>`; }});
      }}
      document.getElementById('out').innerHTML = s;
    }}
    // demo on load
    window.addEventListener('load', run);
  </script>
</body>
</html>
"""

# populated by render_fallback_html when the prospect has evidence/fit
def _evidence_block(excerpt: str) -> str:
    if not excerpt:
        return ""
    return (f'<div class="quote">“{excerpt}” — <span class="eyebrow">from their site</span></div>')

def _fit_block(fit: str) -> str:
    if not fit:
        return ""
    return f'<p style="color:var(--muted); margin-top:22px; max-width:640px;">{fit}</p>'


# rewrite render_fallback_html to plug the two dynamic sub-templates
def render_fallback_html(prospect: dict[str, Any], product_gist: str = "") -> str:
    company = html.escape(prospect.get("company_name") or "your company")
    contact = prospect.get("contact") or {}
    name = (contact.get("name") or "").split()[0] if contact.get("name") else "team"
    person = html.escape(name)
    industry = html.escape((prospect.get("industry") or "your").split(" ")[0])
    excerpt = ""
    for ev in (prospect.get("pain_evidence") or [])[:1]:
        if ev.get("excerpt"):
            excerpt = html.escape(ev["excerpt"])[:280]
            break
    fit_line = html.escape(prospect.get("fit_rationale") or "")[:300]
    gist = html.escape(product_gist)[:600] if product_gist else (
        "A drop-in HTTP API that redacts and tokenizes 28 categories of PII, "
        "PHI, and PCI data — before it hits your logs, warehouses, or LLM prompts.")
    return _TEMPLATE.format(
        company=company, person=person, industry=industry,
        gist=gist,
        evidence_block=_evidence_block(excerpt),
        fit_block=_fit_block(fit_line),
    )
