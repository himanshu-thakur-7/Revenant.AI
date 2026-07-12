"""Per-run walkthrough workspace state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


WALKTHROUGHS_ROOT = Path.home() / "Revenant.AI" / "out" / "walkthroughs"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "prospect"


@dataclass
class WalkthroughState:
    prospect_slug: str
    workspace: Path
    beats: list[dict] = field(default_factory=list)
    mp3_paths: list[str] = field(default_factory=list)
    mp3_durations: list[float] = field(default_factory=list)
    webm_path: str | None = None
    mp4_path: str | None = None
    stream_uid: str | None = None
    stream_iframe_url: str | None = None
    finalized: bool = False

    @classmethod
    def for_prospect(cls, company_name: str) -> "WalkthroughState":
        slug = _slug(company_name)
        ws = WALKTHROUGHS_ROOT / slug
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "audio").mkdir(exist_ok=True)
        (ws / "video").mkdir(exist_ok=True)
        return cls(prospect_slug=slug, workspace=ws)

    @property
    def audio_dir(self) -> Path:
        return self.workspace / "audio"

    @property
    def video_dir(self) -> Path:
        return self.workspace / "video"
