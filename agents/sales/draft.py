"""Per-run Sales workspace + draft state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


DRAFTS_ROOT = Path.home() / "Revenant.AI" / "out" / "drafts"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "prospect"


@dataclass
class DraftState:
    prospect_slug: str
    workspace: Path
    slides: list[dict] = field(default_factory=list)
    deck_title: str = ""
    deck_pptx_path: str | None = None
    deck_url: str | None = None
    email_subject: str = ""
    email_body: str = ""
    convex_id: str | None = None
    email_md_path: str | None = None
    finalized: bool = False

    @classmethod
    def for_prospect(cls, company_name: str) -> "DraftState":
        slug = _slug(company_name)
        ws = DRAFTS_ROOT / slug
        ws.mkdir(parents=True, exist_ok=True)
        return cls(prospect_slug=slug, workspace=ws)
