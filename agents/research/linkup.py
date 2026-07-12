"""Thin Linkup client — company/pain-signal web search.

Linkup is one of the buildathon sponsors; it wraps a search engine + LLM to
return sourced answers or raw results. We use ``searchResults`` mode so the
Research agent can pick which sources to actually read.

Gracefully falls back to an "unconfigured" error if ``LINKUP_API_KEY`` is
absent — the Research agent surfaces that back to the Orchestrator.
"""

from __future__ import annotations

from typing import Any

import httpx

from ghost.config import settings


_LINKUP_URL = "https://api.linkup.so/v1/search"


def search(
    query: str,
    *,
    depth: str = "standard",
    max_results: int = 8,
) -> list[dict[str, str]]:
    """Return a list of `{name, url, snippet}` dicts, or raise on error."""
    if not settings.linkup_api_key:
        raise RuntimeError(
            "LINKUP_API_KEY not configured — add it to .env to run live research."
        )

    payload: dict[str, Any] = {
        "q": query,
        "depth": depth,
        "outputType": "searchResults",
    }
    try:
        resp = httpx.post(
            _LINKUP_URL,
            headers={"Authorization": f"Bearer {settings.linkup_api_key}"},
            json=payload,
            timeout=25,
        )
    except httpx.HTTPError as exc:  # pragma: no cover - network
        raise RuntimeError(f"linkup HTTP error: {exc}") from exc

    if resp.status_code != 200:
        # Special-case the two errors we know how to explain.
        try:
            payload = resp.json()
            err = payload.get("error", {})
            code = err.get("code", "")
            msg = err.get("message", "")
        except ValueError:
            code = msg = ""
        if code == "INSUFFICIENT_FUNDS_CREDITS":
            raise RuntimeError(
                "Linkup account is out of credits. Top up at "
                "https://app.linkup.so/billing (a $5 top-up = ~1000 standard "
                "searches). The search you attempted needs $0.005 in credit."
            )
        if resp.status_code == 401:
            raise RuntimeError(
                "Linkup API key is invalid or revoked. Regenerate at "
                "https://app.linkup.so/api-keys and update LINKUP_API_KEY in .env."
            )
        raise RuntimeError(f"linkup {resp.status_code} {code}: {msg or resp.text[:200]}")

    data = resp.json()
    results = data.get("results", [])
    out: list[dict[str, str]] = []
    for r in results[:max_results]:
        out.append({
            "name": r.get("name", "") or r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("content") or r.get("snippet") or "")[:400],
        })
    return out
