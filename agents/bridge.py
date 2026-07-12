"""Convex live bridge — mirrors agent activity into the deployed console.

While the founder watches the terminal séance, anyone with the Mission
Control URL (judges, the audience screen) watches the same run fill the
five-act console in real time. The bridge:

* subscribes as a **global sink** on the agent event bus (`agents.base`),
* translates low-level tool events into narrative mission-log rows in the
  shape `console/src/App.tsx` already renders,
* batches writes to Convex on a background worker thread so a slow network
  never blocks an agent's tool loop,
* tracks one campaign row per prospect company and upserts it as the chain
  advances (scouting → building → deployed → filming → awaiting_review).

Enabled automatically when ``CONVEX_URL`` is set; disable with
``CONVEX_LIVE=0``. A new Research delegation starts a fresh run and (by
default) resets the console so the audience sees only the live story —
disable with ``CONVEX_RESET_ON_RUN=0``.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from typing import Any

import httpx

from ghost.config import settings


# ── agent/tool → console narrative mapping ─────────────────────
# Console act numbering: II recon · III build · IV film · V close.
_AGENT_ACT = {"research": 2, "engineer": 3, "director": 4, "sales": 5,
              "orchestrator": 1}

# Console display names (must match AGENT_META in console/src/App.tsx).
_AGENT_DISPLAY = {
    "orchestrator": "The Brain",
    "research": "Detective",
    "engineer": "Developer",
    "director": "Director",
    "sales": "Outreach",
}

# Narrative templates per tool. `{a}` interpolates a salient argument.
_TOOL_STORY: dict[str, tuple[str, str]] = {
    # research                                  (kind, template)
    "web_search":           ("query",    "Hunting the wire: “{a}”"),
    "fetch_page":           ("info",     "Reading the prospect's own words at {a}"),
    "extract_pain_signals": ("evidence", "Distilling pain signals from the page…"),
    "guess_emails":         ("info",     "Deriving inbox candidates for {a}"),
    "find_contact":         ("query",    "Asking Apollo for the decision-maker at {a}"),
    "add_prospect":         ("verdict",  "Target acquired: {a}"),
    "finalize_shortlist":   ("state",    "Shortlist sealed."),
    # engineer
    "read_prospect_brief":  ("info",     "Studying who we're building for…"),
    "list_founder_files":   ("info",     "Reading the founder's own repo…"),
    "read_founder_file":    ("code",     "Reading {a}"),
    "search_founder_context": ("code",   "Grepping the founder's codebase for “{a}”"),
    "write_prototype_file": ("code",     "Writing the personalized prototype ({a})"),
    "deploy_prototype":     ("artifact", "Pushing the prototype to Cloudflare's edge…"),
    "finalize_prototype":   ("state",    "Prototype is LIVE."),
    # director
    "read_prototype_url":   ("info",     "Aiming the camera…"),
    "read_prospect_context": ("info",    "Reading the tone we need to match…"),
    "render_walkthrough":   ("film",     "Narrating, filming, and muxing the walkthrough…"),
    "finalize_walkthrough": ("state",    "Walkthrough uploaded."),
    # sales
    "read_founder_pitch":   ("info",     "Recalling the product's pitch…"),
    "write_pitch_deck":     ("artifact", "Assembling the pitch deck: “{a}”"),
    "deploy_deck":          ("artifact", "Publishing the deck to Cloudflare…"),
    "save_draft":           ("mail",     "Email drafted — queued for the founder's review."),
    "finalize_sales":       ("state",    "The missive awaits a human click."),
    # orchestrator
    "spawn_research_agent": ("info",     "The Brain wakes the Detective."),
    "spawn_engineer_agent": ("info",     "The Brain wakes the Engineer."),
    "spawn_director_agent": ("info",     "The Brain rolls film."),
    "spawn_sales_agent":    ("info",     "The Brain calls for the missive."),
    "send_approved_email":  ("mail",     "Approved — dispatching the missive."),
}

# Which argument to surface per tool (first match wins).
_ARG_PICK = ("query", "url", "path", "pattern", "filename", "company_name",
             "company_domain", "title", "first_name")

_SLUG_RX = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RX.sub("-", (text or "").lower()).strip("-") or "prospect"


class ConvexBridge:
    """Fire-and-forget mirror of agent events into Convex."""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._t0 = time.monotonic()
        self._run_id = f"run_live_{int(time.time())}"
        self._seq = 0
        self._campaigns: dict[str, str] = {}   # company → campaign_id
        self._company_ctx: str = ""            # last company seen this run
        self._lock = threading.Lock()

    # ── lifecycle ─────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        if os.getenv("CONVEX_LIVE", "1").strip().lower() in {"0", "false", "off"}:
            return False
        return bool(settings.convex_url)

    def new_run(self, *, reset: bool | None = None) -> None:
        """Start a fresh run (called when a new Research chain begins)."""
        if not self.enabled:
            return
        with self._lock:
            self._t0 = time.monotonic()
            self._run_id = f"run_live_{int(time.time())}"
            self._seq = 0
            self._campaigns.clear()
            self._company_ctx = ""
        if reset is None:
            reset = os.getenv("CONVEX_RESET_ON_RUN", "1").strip().lower() not in {
                "0", "false", "off"}
        if reset:
            self._post("ledger:reset", {})

    # ── event ingestion (called from the agent event bus) ─────
    def on_agent_event(self, ev: Any) -> None:
        """Translate an AgentEvent into a mission-log row. Never raises."""
        try:
            self._translate(ev)
        except Exception:
            pass  # the console is a mirror, never a failure source

    def _translate(self, ev: Any) -> None:
        if not self.enabled or ev.kind not in ("tool_call", "tool_result"):
            return
        tool = ev.tool or ""

        if ev.kind == "tool_call":
            story = _TOOL_STORY.get(tool)
            if story is None:
                return
            kind, template = story
            arg = self._pick_arg(ev.args or {})
            message = template.format(a=arg) if "{a}" in template else template
            self._note_company(ev.args or {})
            self._emit(ev.agent, kind, message)
            return

        # tool_result: surface the juicy artifacts (URLs) + stage campaigns.
        if tool in ("add_prospect", "deploy_prototype", "finalize_prototype",
                    "finalize_walkthrough", "save_draft", "finalize_sales"):
            self._on_result(ev.agent, tool, ev.result or "")

    # ── result handlers: campaign lifecycle ───────────────────
    def _on_result(self, agent: str, tool: str, raw: str) -> None:
        data = _safe_json(raw)

        if tool == "add_prospect" and isinstance(raw, str) and raw.startswith("added:"):
            company = raw.split("added:", 1)[1].split("(")[0].strip()
            self._company_ctx = company
            cid = self._campaign_id(company)
            self._emit(agent, "verdict", f"{company} enters the circle.",
                       campaign_id=cid, company=company,
                       payload={"state": "scored"})
            self.upsert_campaign({"id": cid, "state": "scored",
                                  "lead": {"company_name": company}})
            return

        if not isinstance(data, dict):
            return

        company = self._company_ctx
        cid = self._campaign_id(company) if company else ""

        if tool in ("deploy_prototype", "finalize_prototype"):
            url = data.get("url", "")
            if url and not url.startswith("file:"):
                self._emit(agent, "artifact",
                           f"Prototype LIVE → {url}",
                           campaign_id=cid, company=company,
                           payload={"url": url, "state": "deployed"})
                self.upsert_campaign({"id": cid, "state": "deployed",
                                      "microsite_url": url,
                                      "lead": {"company_name": company}})

        elif tool == "finalize_walkthrough":
            url = data.get("video_url", "")
            if url:
                self._emit(agent, "film",
                           f"Walkthrough LIVE → {url}",
                           campaign_id=cid, company=company,
                           payload={"url": url, "state": "filming"})
                self.upsert_campaign({"id": cid, "walkthrough_url": url,
                                      "lead": {"company_name": company}})

        elif tool in ("save_draft", "finalize_sales"):
            deck = data.get("deck_url", "")
            subject = data.get("email_subject", "")
            msg = "Draft sealed"
            if subject:
                msg += f' — “{subject}”'
            self._emit(agent, "mail", msg,
                       campaign_id=cid, company=company,
                       payload={"deck_url": deck, "state": "awaiting_review"})

    # ── campaign helpers ──────────────────────────────────────
    def _campaign_id(self, company: str) -> str:
        if not company:
            return ""
        with self._lock:
            if company not in self._campaigns:
                self._campaigns[company] = f"camp_live_{_slug(company)}"
            return self._campaigns[company]

    def campaign_id_for(self, company: str) -> str | None:
        """Public: lets Sales reuse the run's campaign id in its Convex row."""
        with self._lock:
            return self._campaigns.get(company)

    def upsert_campaign(self, partial: dict[str, Any]) -> None:
        """Merge-style upsert — Convex `upsertCampaign` replaces the doc, so
        we keep a local merged copy per company and send the whole thing."""
        if not self.enabled or not partial.get("id"):
            return
        with self._lock:
            cache = getattr(self, "_camp_cache", {})
            row = cache.get(partial["id"], {
                "seller_id": "live", "state": "scouting",
                "microsite_url": "", "microsite_html": "",
                "walkthrough_url": "", "voice_memo_ref": "",
                "email_subject": "", "email_body": "", "deck_url": "",
                "payment_link": "", "cost_usd": 0.0,
                "lead": {"company_name": "", "company_domain": "",
                         "person_name": "", "person_title": "",
                         "job_description": ""},
            })
            lead = {**row.get("lead", {}), **(partial.pop("lead", {}) or {})}
            row.update(partial)
            row["lead"] = lead
            cache[row["id"]] = row
            self._camp_cache = cache
        self._post("ledger:upsertCampaign", {"doc": row})

    # ── low-level emit / transport ────────────────────────────
    def _note_company(self, args: dict[str, Any]) -> None:
        for key in ("company_name", "prospect_company"):
            if args.get(key):
                self._company_ctx = str(args[key])
                return
        pj = args.get("prospect_json")
        if isinstance(pj, str):
            data = _safe_json(pj)
            if isinstance(data, dict) and data.get("company_name"):
                self._company_ctx = str(data["company_name"])

    def _pick_arg(self, args: dict[str, Any]) -> str:
        for key in _ARG_PICK:
            v = args.get(key)
            if v:
                s = str(v)
                return s if len(s) <= 80 else s[:79] + "…"
        return ""

    def _emit(self, agent: str, kind: str, message: str, *,
              campaign_id: str = "", company: str = "",
              payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._seq += 1
            row = {
                "id": f"ev_{self._run_id}_{self._seq:04d}",
                "at": round(time.monotonic() - self._t0, 1),
                "act": _AGENT_ACT.get(agent, 1),
                "agent": _AGENT_DISPLAY.get(agent, agent.title()),
                "kind": kind,
                "message": message,
                "campaign_id": campaign_id,
                "company": company or self._company_ctx,
                "payload": payload or {},
            }
            run_id = self._run_id
        self._post("ledger:addEvents", {"runId": run_id, "docs": [row]})

    def _post(self, fn: str, args: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self._ensure_worker()
        self._q.put((fn, args))

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._drain, daemon=True,
                                            name="convex-bridge")
            self._worker.start()

    def _drain(self) -> None:
        while True:
            fn, args = self._q.get()
            try:
                httpx.post(
                    f"{settings.convex_url}/api/mutation",
                    json={"path": fn, "args": args, "format": "json"},
                    timeout=8,
                )
            except Exception:
                pass  # mirror, not a dependency
            finally:
                self._q.task_done()

    def flush(self, timeout: float = 5.0) -> None:
        """Best-effort wait for queued writes (call before process exit)."""
        deadline = time.monotonic() + timeout
        while not self._q.empty() and time.monotonic() < deadline:
            time.sleep(0.1)


def _safe_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


# module singleton — self-registers on the global agent event bus
bridge = ConvexBridge()

from .base import register_global_sink  # noqa: E402  (deliberate tail import)

register_global_sink(bridge.on_agent_event)
