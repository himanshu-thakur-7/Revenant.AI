"""Deterministic fallback prototype — used ONLY when the Engineer LLM fails to
write index.html. Product-AGNOSTIC: it pitches whatever startup was set up
(product name + gist from the ingested repo), never a specific domain. Honest
placeholder demo — no fake domain-specific logic. Correct element-id contract
(#demo/#demoInput/#demoRun/#demoOutput/#code/#cta) so the Director can film it.
"""

from __future__ import annotations

import html
from typing import Any


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{product} × {company}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {{ --accent:#5b8cff; --bg:#0a0c12; --card:rgba(255,255,255,.03);
      --line:rgba(255,255,255,.09); --muted:#9aa4b2; }}
    body {{ background:radial-gradient(1200px 600px at 70% -10%, rgba(91,140,255,.14), transparent), var(--bg);
      color:#e8edf5; font-family:Inter,system-ui,sans-serif; margin:0; }}
    .container {{ max-width:960px; margin:0 auto; padding:0 22px; }}
    nav {{ display:flex; justify-content:space-between; align-items:center; padding:18px 0;
      border-bottom:1px solid var(--line); }}
    .logo {{ font-weight:800; letter-spacing:-.02em; }}
    .eyebrow {{ font-family:ui-monospace,monospace; font-size:12px; letter-spacing:.18em;
      text-transform:uppercase; color:var(--accent); }}
    h1 {{ font-size:44px; line-height:1.05; font-weight:800; letter-spacing:-.03em; margin:14px 0; }}
    .sub {{ color:var(--muted); font-size:18px; max-width:640px; }}
    section {{ padding:56px 0; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px; }}
    .btn {{ background:var(--accent); color:#04101f; font-weight:700; border:none;
      border-radius:10px; padding:12px 20px; cursor:pointer; }}
    textarea {{ width:100%; background:#0e121b; color:#e8edf5; border:1px solid var(--line);
      border-radius:10px; padding:14px; font-family:ui-monospace,monospace; font-size:13px; min-height:120px; }}
    #demoOutput {{ background:#0e121b; border:1px solid var(--line); border-radius:10px;
      padding:14px; margin-top:14px; font-family:ui-monospace,monospace; font-size:13px; color:var(--muted); }}
    .grid-3 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin-top:20px; }}
    .quote {{ border-left:3px solid var(--accent); padding-left:14px; color:#cdd6e4; margin-top:20px; }}
    footer {{ color:var(--muted); font-size:13px; padding:34px 0; border-top:1px solid var(--line); }}
  </style>
</head>
<body>
  <div class="container">
    <nav><div class="logo">{product} × {company}</div>
      <span class="eyebrow">built for {company}</span></nav>

    <section>
      <div class="eyebrow">a working prototype for {company}</div>
      <h1>{product}, wired for {company}.</h1>
      <p class="sub">{gist}</p>
      {fit_block}
    </section>

    <section id="demo">
      <div class="eyebrow">live preview</div>
      <h2 style="font-size:28px;font-weight:800;margin:8px 0 14px;">See it in action</h2>
      <textarea id="demoInput">Paste a real example from {company}'s world here — then hit Run.</textarea>
      <button id="demoRun" class="btn" style="margin-top:12px;" onclick="run()">Run it</button>
      <div id="demoOutput">Hit “Run it” and {product} processes your input here.</div>
    </section>

    <section>
      <div class="eyebrow">why {company}</div>
      <h2 style="font-size:28px;font-weight:800;margin:8px 0 6px;">Where it fits</h2>
      <div class="grid-3">
        <div class="card"><h3 style="font-weight:700;">Drop-in</h3><p style="color:var(--muted);">Slots into your existing stack with minimal setup.</p></div>
        <div class="card"><h3 style="font-weight:700;">Built for your use case</h3><p style="color:var(--muted);">Tuned to what {company} actually does day to day.</p></div>
        <div class="card"><h3 style="font-weight:700;">Fast to pilot</h3><p style="color:var(--muted);">A scoped pilot you can run this week, no long project.</p></div>
      </div>
      {evidence_block}
    </section>

    <section id="code">
      <div class="eyebrow">integration</div>
      <h2 style="font-size:28px;font-weight:800;margin:8px 0 12px;">Wire it in</h2>
      <pre class="card" style="overflow:auto;"><code># point {company}'s workflow at {product}
result = {product_slug}.run(your_input)  # drops in where you need it</code></pre>
    </section>

    <section id="cta" style="text-align:center;">
      <h2 style="font-size:26px;font-weight:800;">Run {product} on {company}'s real workflow</h2>
      <a class="btn" href="#" style="display:inline-block;margin-top:16px;">Book a 30-min pilot</a>
    </section>

    <footer>Built autonomously for <b>{company}</b> · Preview only, not production data.</footer>
  </div>
  <script>
    function run() {{
      var v = document.getElementById('demoInput').value.trim();
      var out = document.getElementById('demoOutput');
      out.style.color = '#e8edf5';
      out.textContent = '⏳ Running…';
      setTimeout(function() {{
        out.innerHTML = '✓ <b>Processed by {product}.</b>\\n\\n' +
          (v ? ('Input received (' + v.length + ' chars). ') : '') +
          'This lightweight preview shows the shape of the flow — the full ' +
          '{product} engine runs server-side against your real data in a pilot.';
      }}, 700);
    }}
  </script>
</body>
</html>
"""


def _evidence_block(excerpt: str) -> str:
    if not excerpt:
        return ""
    return (f'<div class="quote">“{excerpt}” — '
            f'<span class="eyebrow">from their site</span></div>')


def _fit_block(fit: str) -> str:
    if not fit:
        return ""
    return (f'<p style="color:var(--muted);margin-top:18px;max-width:640px;">{fit}</p>')


def render_fallback_html(prospect: dict[str, Any], product_gist: str = "",
                         product_name: str = "") -> str:
    company = html.escape(prospect.get("company_name") or "your company")
    product = html.escape(product_name or "our product")
    product_slug = html.escape((product_name or "product").lower().split()[0])
    excerpt = ""
    for ev in (prospect.get("pain_evidence") or [])[:1]:
        if ev.get("excerpt"):
            excerpt = html.escape(ev["excerpt"])[:280]
            break
    fit_line = html.escape(prospect.get("fit_rationale") or "")[:300]
    gist = html.escape(product_gist)[:600] if product_gist else (
        f"{product} — set up for {company}. A working preview of how it slots "
        "into your stack and what it unlocks.")
    return _TEMPLATE.format(
        product=product, product_slug=product_slug, company=company, gist=gist,
        evidence_block=_evidence_block(excerpt),
        fit_block=_fit_block(fit_line),
    )
