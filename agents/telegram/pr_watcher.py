"""GitHub PR-merge watcher — the trigger for the Razorpay demo flow.

Instead of the founder typing "find merchants", we watch the founder's Razorpay
repo for a **merged PR**. The instant one lands, we fire a callback that runs
the shortlist + build for the feature that just shipped.

Design:
- One daemon thread per chat, cancellable via ``stop()``.
- Polls ``GET /repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc``
  every ``poll_seconds`` (default 15s; 5000 req/hr with a PAT is comfortable).
- ``since_ts`` is set to "now" at start, so only PRs merged AFTER the watcher
  begins are surfaced — historical merges are ignored. A merged PR fires the
  callback ONCE (deduped by PR number).
- Failures are silent (network hiccups, rate limits) — the loop just retries
  next tick. It never crashes the bot.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

_UA = "revenant-ai/pr-watcher"


@dataclass
class MergedPR:
    number: int
    title: str
    body: str
    merged_at: str
    author: str
    html_url: str


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class PRWatcher:
    """Watch one repo for merged PRs and fire a callback per unique merge.

    Usage:
        w = PRWatcher("razorpayInc/Razorpay", on_merge=lambda pr: ...)
        w.start()   # non-blocking, daemon thread
        w.stop()    # cancel

    Only PRs merged AFTER start() are surfaced (``since_ts`` = now).
    """

    def __init__(self, repo: str,
                 on_merge: Callable[[MergedPR], None],
                 *,
                 token: str | None = None,
                 poll_seconds: float = 15.0,
                 on_error: Callable[[str], None] | None = None) -> None:
        self.repo = repo.strip("/")
        self.on_merge = on_merge
        self.on_error = on_error or (lambda _msg: None)
        self.poll_seconds = poll_seconds
        self._token = token or os.getenv("GITHUB_TOKEN", "")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._since_ts: datetime = datetime.now(timezone.utc)
        self._seen: set[int] = set()

    # ── lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._since_ts = datetime.now(timezone.utc)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"pr-watch-{self.repo.replace('/', '-')}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── the poll loop ────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "User-Agent": _UA}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _fetch_once(self) -> list[MergedPR]:
        url = f"https://api.github.com/repos/{self.repo}/pulls"
        params = {"state": "closed", "sort": "updated",
                  "direction": "desc", "per_page": 10}
        try:
            r = httpx.get(url, params=params, headers=self._headers(), timeout=10)
        except Exception as exc:  # noqa: BLE001
            self.on_error(f"github fetch failed: {exc}")
            return []
        if r.status_code == 404:
            self.on_error(f"repo not found: {self.repo}")
            return []
        if r.status_code == 401:
            self.on_error("github: 401 unauthorized (check GITHUB_TOKEN)")
            return []
        if r.status_code == 403 and "rate limit" in r.text.lower():
            self.on_error("github: rate limit — will retry")
            return []
        if r.status_code != 200:
            self.on_error(f"github: HTTP {r.status_code}")
            return []
        out: list[MergedPR] = []
        for p in r.json() or []:
            n = p.get("number")
            merged = p.get("merged_at")
            if not merged or n is None:
                continue
            try:
                merged_dt = _parse_iso(merged)
            except Exception:
                continue
            # only merges AFTER we started watching, deduped
            if merged_dt <= self._since_ts:
                continue
            if n in self._seen:
                continue
            out.append(MergedPR(
                number=int(n),
                title=str(p.get("title") or ""),
                body=str(p.get("body") or "")[:2000],
                merged_at=str(merged),
                author=str((p.get("user") or {}).get("login") or ""),
                html_url=str(p.get("html_url") or ""),
            ))
        # oldest merge first so the callback fires in chronological order
        out.sort(key=lambda pr: pr.merged_at)
        return out

    def _loop(self) -> None:
        # give GH a beat before the very first poll
        self._stop.wait(1.5)
        while not self._stop.is_set():
            for pr in self._fetch_once():
                if self._stop.is_set():
                    return
                self._seen.add(pr.number)
                try:
                    self.on_merge(pr)
                except Exception as exc:  # noqa: BLE001
                    self.on_error(f"on_merge callback: {exc}")
            self._stop.wait(self.poll_seconds)
