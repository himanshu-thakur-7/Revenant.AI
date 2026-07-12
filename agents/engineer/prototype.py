"""Per-run prototype workspace and deployment state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


PROTOTYPES_ROOT = Path.home() / "Revenant.AI" / "out" / "prototypes"


# Injected into every prototype's <head>. Prevents ANY element from pushing
# the page wider than the viewport — the #1 cause of "text overflowing
# everywhere" (an unwrapped <pre>/log line running off the right edge).
_HARDEN_CSS = """<style id="revenant-harden">
*,*::before,*::after{box-sizing:border-box;}
html,body{max-width:100%;overflow-x:hidden;}
img,video,canvas,svg,iframe{max-width:100%;height:auto;}
pre,code,kbd,samp{white-space:pre-wrap!important;overflow-wrap:break-word!important;
  word-break:break-word;max-width:100%;}
pre{overflow-x:auto;}
textarea,input,select{max-width:100%;}
table{display:block;max-width:100%;overflow-x:auto;}
*{min-width:0;}
</style>"""


def _harden_html(content: str) -> str:
    """Inject the overflow-prevention stylesheet just before </head> (or at the
    top of the doc if there's no head). Idempotent — re-writes won't stack it."""
    if 'id="revenant-harden"' in content:
        return content
    m = re.search(r"</head\s*>", content, re.I)
    if m:
        return content[:m.start()] + _HARDEN_CSS + "\n" + content[m.start():]
    # no </head> — put it right after <head ...> or <html ...>, else prepend
    m = re.search(r"<head[^>]*>", content, re.I) or re.search(r"<html[^>]*>", content, re.I)
    if m:
        return content[:m.end()] + "\n" + _HARDEN_CSS + content[m.end():]
    return _HARDEN_CSS + "\n" + content


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "prospect"


@dataclass
class PrototypeState:
    """Everything one Engineer run produces.

    The workspace lives on disk so the deployer can `wrangler pages deploy`
    (or an httpx upload) point at a real directory. The deployer stashes the
    resulting URL back into `deployment_url`.
    """

    prospect_slug: str
    workspace: Path
    files: dict[str, str] = field(default_factory=dict)
    deployment_url: str | None = None
    deployer: str | None = None       # "cloudflare-pages" | "file" | None
    finalized: bool = False

    @classmethod
    def for_prospect(cls, company_name: str) -> "PrototypeState":
        slug = _slug(company_name)
        ws = PROTOTYPES_ROOT / slug
        ws.mkdir(parents=True, exist_ok=True)
        return cls(prospect_slug=slug, workspace=ws)

    def write(self, filename: str, content: str) -> Path:
        # only allow simple relative names — no path traversal
        safe = filename.lstrip("/").replace("..", "_")
        target = (self.workspace / safe).resolve()
        if not str(target).startswith(str(self.workspace.resolve())):
            raise ValueError(f"filename outside workspace: {filename}")
        # Bulletproof the layout: inject an overflow-prevention stylesheet so
        # long log lines / code blocks / wide grids can NEVER push the page
        # sideways (the "text overflowing everywhere" bug). Deterministic —
        # works no matter what the LLM emitted.
        if safe.endswith((".html", ".htm")):
            content = _harden_html(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.files[safe] = content
        return target

    def paths(self) -> list[str]:
        return sorted(self.files.keys())

    def has(self, filename: str) -> bool:
        return filename in self.files
