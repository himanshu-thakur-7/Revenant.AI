"""The truth ledger — all campaign state reads/writes go through here.

In ``live`` mode this talks to the deployed Convex backend over its HTTP API
(mutations under ``convex/ledger.ts``). In ``offline`` mode it keeps an
in-memory store. Either way every write is mirrored to ``out/ledger.json`` so
the console can always render a run. The interface is identical, so no
downstream code knows which backend it hit.

The ledger also carries the *mission log* — the event stream that lets the
console replay the run, agent by agent (see :mod:`ghost.events`).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .events import mission
from .log import log
from .models import Campaign, SellerProfile, new_id

OUT = Path("out")
OUT.mkdir(exist_ok=True)
_LEDGER_FILE = OUT / "ledger.json"


class Ledger:
    def __init__(self) -> None:
        self._sellers: dict[str, SellerProfile] = {}
        self._campaigns: dict[str, Campaign] = {}
        self._memories: list[dict[str, Any]] = []
        self._run_id = new_id("run_")
        self._live = settings.require_live("convex_url")
        if self._live:
            log.dim(f"[ledger] live → Convex ({settings.convex_url})")
        else:
            log.dim("[ledger] offline → out/ledger.json")

    # ── writes ────────────────────────────────────────────────
    def upsert_seller(self, seller: SellerProfile) -> None:
        self._sellers[seller.id] = seller
        self._convex("ledger:upsertSeller", {"doc": seller.model_dump()})
        self._flush()

    def upsert_campaign(self, camp: Campaign) -> None:
        self._campaigns[camp.id] = camp
        self._convex("ledger:upsertCampaign", {"doc": _campaign_row(camp)})
        self._flush()

    def set_state(self, camp: Campaign, state: Any) -> None:
        camp.state = state
        log.dim(f"[ledger] {camp.id} → {state.value if hasattr(state,'value') else state}")
        self.upsert_campaign(camp)

    def remember(
        self, campaign: Campaign, kind: str, body: str, re_ping_at: float | None = None
    ) -> None:
        """Write a long-term memory row — the persistence engine's fuel."""
        row = {
            "campaign_id": campaign.id,
            "person_name": campaign.lead.person_name,
            "kind": kind,
            "body": body,
            "re_ping_at": re_ping_at,
        }
        self._memories.append(row)
        self._convex("ledger:addMemory", {"doc": row})
        self._flush()

    def publish_events(self) -> None:
        """Batch-push the mission log to Convex (one call, not N)."""
        events = mission.all()
        if events:
            self._convex("ledger:addEvents", {"runId": self._run_id, "docs": events})
        self._flush()

    def due_memories(self, now: float) -> list[dict[str, Any]]:
        """Memories whose deferral window has closed."""
        return [
            m for m in self._memories
            if m.get("re_ping_at") is not None and m["re_ping_at"] <= now
        ]

    # ── reads ─────────────────────────────────────────────────
    def campaigns(self) -> list[Campaign]:
        return list(self._campaigns.values())

    def sellers(self) -> list[SellerProfile]:
        return list(self._sellers.values())

    def memories(self) -> list[dict[str, Any]]:
        return list(self._memories)

    # ── backends ──────────────────────────────────────────────
    def _convex(self, fn: str, args: dict[str, Any]) -> None:
        if not self._live:
            return
        try:  # pragma: no cover - network path
            resp = httpx.post(
                f"{settings.convex_url}/api/mutation",
                json={"path": fn, "args": args, "format": "json"},
                timeout=15,
            )
            body = resp.json()
            if body.get("status") != "success":
                log.warn(f"[ledger] convex {fn}: {str(body)[:160]}")
        except Exception as exc:  # pragma: no cover
            log.warn(f"[ledger] convex {fn} failed: {exc!r}")

    def _flush(self) -> None:
        snapshot = {
            "run_id": self._run_id,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sellers": [s.model_dump() for s in self._sellers.values()],
            "campaigns": [_campaign_row(c) for c in self._campaigns.values()],
            "memories": self._memories,
            "events": mission.all(),
        }
        _LEDGER_FILE.write_text(json.dumps(snapshot, indent=2, default=str))


def _campaign_row(camp: Campaign) -> dict[str, Any]:
    """Flatten a campaign into the row shape the console expects."""
    d = camp.model_dump()
    d["state"] = camp.state.value if hasattr(camp.state, "value") else camp.state
    d["cost_usd"] = round(camp.cost_cents / 100, 2)
    if camp.lead.score:
        d["tier"] = camp.lead.score.tier.value
        d["combined_score"] = round(camp.lead.score.combined, 3)
    # inline the rendered microsite so remote consoles can iframe-srcdoc it
    site = camp.artifact("site")
    if site and site.verified:
        try:
            d["microsite_html"] = Path(site.storage_ref).read_text()
        except OSError:
            d["microsite_html"] = ""
    return d


# module-level singleton — one ledger per process
ledger = Ledger()
