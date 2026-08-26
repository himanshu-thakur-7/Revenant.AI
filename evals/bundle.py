"""The Bundle — the addressable unit of evaluation.

A Bundle resolves everything one campaign produced to real paths/URLs on
disk. Nothing here trusts a tool's self-reported success text — this
session found nine real bugs (three of them "the tool said it worked and
it didn't") by refusing to do that, and the eval framework inherits the
same discipline structurally: checks (evals/checks/*) always re-fetch or
re-open the actual artifact, never read `Bundle.prototype_url` and assume
it resolves.

Layout on disk, verified against what agents/mcp_server.py + agents/
engineer/director/sales actually write (not assumed from the docstrings):
  out/prototypes/<slug>/index.html
  out/walkthroughs/<slug>/walkthrough.mp4
  out/drafts/<slug>/<slug>-pitch.pptx
  out/drafts/<slug>/<slug>-email.md
  ~/.revenant/last_campaign.json   — the live URLs for the MOST RECENT build only
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "out"
BUNDLES_DIR = OUT / "evals" / "bundles"
REVENANT_HOME = Path.home() / ".revenant"


def slug(text: str) -> str:
    """Same slugify as agents/engineer/prototype.py::_slug — duplicated
    rather than imported so evals/ has its own independent import graph;
    ghost/tests/test_evals_offline.py::test_slug_matches_engineer_prototype_slug
    is what actually keeps the two in sync."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "prospect"


def git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


_FIELD_NAMES = None  # populated after Bundle is defined


@dataclass
class Bundle:
    bundle_id: str
    created_at: str
    git_sha: str
    startup: str = ""
    startup_summary: str = ""
    merchant: str = ""
    merchant_domain: str = ""
    pain: str = ""

    prototype_url: str = ""
    prototype_html_path: str = ""

    walkthrough_url: str = ""
    walkthrough_mp4_path: str = ""

    deck_url: str = ""
    deck_pptx_path: str = ""

    email_md_path: str = ""
    email_subject: str = ""

    recipient_email: str = ""
    contact_name: str = ""
    contact_title: str = ""

    durations_s: dict[str, float] = field(default_factory=dict)
    prompt_versions: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)

    def save(self) -> Path:
        BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
        p = BUNDLES_DIR / f"{self.bundle_id}.json"
        p.write_text(json.dumps(asdict(self), indent=2))
        return p

    @classmethod
    def load(cls, bundle_id: str) -> "Bundle":
        p = BUNDLES_DIR / f"{bundle_id}.json"
        return cls(**_filter_known(json.loads(p.read_text())))

    @classmethod
    def new_id(cls, merchant: str) -> str:
        return f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(merchant)}"

    def artifacts(self) -> dict[str, bool]:
        """Which artifact KINDS this bundle claims to have (path or URL
        set) — NOT whether they're actually valid; that's evals/checks'
        job. Used by the runner to know what to check."""
        return {
            "prototype": bool(self.prototype_url or self.prototype_html_path),
            "walkthrough": bool(self.walkthrough_url or self.walkthrough_mp4_path),
            "deck": bool(self.deck_url or self.deck_pptx_path),
            "email": bool(self.email_md_path or self.email_subject),
        }


_FIELD_NAMES = {f.name for f in fields(Bundle)}


def _filter_known(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k in _FIELD_NAMES}


def merge_into(bundle_id: str, *, startup: str = "", merchant: str = "", **patch: Any) -> Bundle:
    """Create-or-update a bundle by id. build_prototype / film_walkthrough /
    draft_outreach each fill their own slice; this merges (dict fields
    update, scalar fields overwrite-if-truthy) rather than clobbers, so
    calling the tools independently — not just via build_full_outreach —
    still accumulates into one addressable bundle."""
    p = BUNDLES_DIR / f"{bundle_id}.json"
    if p.exists():
        data = _filter_known(json.loads(p.read_text()))
    else:
        data = {
            "bundle_id": bundle_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "git_sha": git_sha(),
            "durations_s": {}, "prompt_versions": {}, "models": {},
        }
    if startup:
        data["startup"] = startup
    if merchant:
        data["merchant"] = merchant
    for k, v in patch.items():
        if k not in _FIELD_NAMES:
            continue
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        elif v not in (None, ""):
            data[k] = v
    b = Bundle(**data)
    b.save()
    return b


def from_disk(merchant: str, *, startup: str = "Razorpay") -> Bundle:
    """Reconstruct a bundle purely from what's on disk — no dependency on
    a prior live tool call having written bundle metadata. Used for the
    artifacts already produced earlier this session, and as a recovery
    path if a bundle write is ever lost."""
    s = slug(merchant)
    proto_html = OUT / "prototypes" / s / "index.html"
    wt_mp4 = OUT / "walkthroughs" / s / "walkthrough.mp4"
    deck = OUT / "drafts" / s / f"{s}-pitch.pptx"
    email = OUT / "drafts" / s / f"{s}-email.md"

    urls: dict[str, Any] = {}
    camp_path = REVENANT_HOME / "last_campaign.json"
    if camp_path.exists():
        try:
            camp = json.loads(camp_path.read_text())
            if slug(camp.get("company", "")) == s:
                urls = {
                    "prototype_url": camp.get("prototype_url", ""),
                    "walkthrough_url": camp.get("walkthrough_url", ""),
                    "deck_url": camp.get("deck_url", ""),
                    "email_subject": camp.get("email_subject", ""),
                    "recipient_email": camp.get("recipient_email", ""),
                    "contact_name": camp.get("contact_name", ""),
                }
        except Exception:
            pass

    return Bundle(
        bundle_id=f"fromdisk-{s}",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        git_sha=git_sha(),
        startup=startup,
        merchant=merchant,
        prototype_html_path=str(proto_html) if proto_html.exists() else "",
        walkthrough_mp4_path=str(wt_mp4) if wt_mp4.exists() else "",
        deck_pptx_path=str(deck) if deck.exists() else "",
        email_md_path=str(email) if email.exists() else "",
        **urls,
    )
