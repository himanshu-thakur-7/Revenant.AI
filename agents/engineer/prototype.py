"""Per-run prototype workspace and deployment state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


PROTOTYPES_ROOT = Path.home() / "Revenant.AI" / "out" / "prototypes"


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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.files[safe] = content
        return target

    def paths(self) -> list[str]:
        return sorted(self.files.keys())

    def has(self, filename: str) -> bool:
        return filename in self.files
