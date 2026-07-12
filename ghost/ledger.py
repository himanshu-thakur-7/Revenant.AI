"""The truth ledger — all campaign state reads/writes go through here.

In ``live`` mode this talks to Convex over its HTTP API. In ``offline`` mode
it keeps an in-memory store and mirrors every write to ``out/ledger.json`` so
the console can render a run without a Convex deployment. The interface is the
same either way, so no downstream code knows which backend it hit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .log import log
from .models import Campaign, SellerProfile

OUT = Path("out")
OUT.mkdir(exist_ok=True)
_LEDGER_FILE = OUT / "ledger.json"


class Ledger:
    def __init__(self) -> None:
        self._sellers: dict[str, SellerProfile] = {}
        self._campaigns: dict[str, Campaign] = {}
        self._live = settings.require_live("convex_url")
        if self._live:
            log.dim("[ledger] live → Convex")
        else:
            log.dim("[ledger] offline → out/ledger.json")

    # ── writes ────────────────────────────────────────────────
    def upsert_seller(self, seller: SellerProfile) -> None:
        self._sellers[seller.id] = seller
        self._convex("sellers:upsert", seller.model_dump())
        self._flush()

    def upsert_campaign(self, camp: Campaign) -> None:
        self._campaigns[camp.id] = camp
        self._convex("campaigns:upsert", _campaign_row(camp))
        self._flush()

    def set_state(self, camp: Campaign, state: Any) -> None:
        camp.state = state
        log.dim(f"[ledger] {camp.id} → {state.value if hasattr(state,'value') else state}")
        self.upsert_campaign(camp)

    # ── reads ─────────────────────────────────────────────────
    def campaigns(self) -> list[Campaign]:
        return list(self._campaigns.values())

    def sellers(self) -> list[SellerProfile]:
        return list(self._sellers.values())

    # ── backends ──────────────────────────────────────────────
    def _convex(self, fn: str, args: dict[str, Any]) -> None:
        if not self._live:
            return
        try:  # pragma: no cover - network path
            httpx.post(
                f"{settings.convex_url}/api/mutation",
                json={"path": fn, "args": args},
                headers={"Authorization": f"Bearer {settings.convex_deploy_key}"},
                timeout=10,
            )
        except Exception as exc:  # pragma: no cover
            log.warn(f"[ledger] convex {fn} failed: {exc!r}")

    def _flush(self) -> None:
        snapshot = {
            "sellers": [s.model_dump() for s in self._sellers.values()],
            "campaigns": [_campaign_row(c) for c in self._campaigns.values()],
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
    return d


# module-level singleton — one ledger per process
ledger = Ledger()
