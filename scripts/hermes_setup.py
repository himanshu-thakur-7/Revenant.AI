#!/usr/bin/env python3
"""Hermes skill entry point — onboard the founder's startup (the /setup flow).

The founder points Revenant at their startup ONCE (a GitHub repo or a local
folder), and every later "find a customer" run sells on its behalf. This
mirrors the seamless ``/setup github.com/you/startup`` UX of the old bot,
but routed through Hermes so it lives in the Hermes app.

It ingests the repo/docs, writes a small pointer file
(``~/.revenant/active_context.json``) that ``hermes_run.py`` reads, and prints
a short confirmation brief (what the startup does) for Hermes to relay.

Usage:
    python scripts/hermes_setup.py "github.com/himanshu-thakur-7/shroud"
    python scripts/hermes_setup.py "~/shroud"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

def _active_ctx_path(product_name: str = "") -> Path:
    """Per-tenant active_context.json. Onboarding is where a tenant is
    ESTABLISHED, so the ingested product name becomes the tenant key (or
    the active/default tenant when ingestion couldn't name the product) —
    same rule as agents/mcp_server.py::setup_startup, which is the MCP
    equivalent of this script."""
    from agents import tenancy
    tenant = tenancy.resolve(product_name)
    tenancy.set_active(tenant)
    return tenancy.state_path(tenant, "active_context.json")


def _looks_like_repo(src: str) -> bool:
    return src.startswith(("http://", "https://", "git@")) or (
        "/" in src and not src.startswith(("~", "/", ".")))


def main() -> None:
    os.environ.setdefault("REVENANT_MODE", "live")
    raw = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    # tolerate the founder pasting "set up my startup github.com/x/y" — grab
    # the last token that looks like a repo/url/path.
    if not raw:
        print("Point me at your startup: send a GitHub repo or a local path, "
              "e.g. `set up github.com/you/your-startup`.")
        sys.exit(0)
    source = raw.split()[-1].strip().rstrip("/")

    from ghost.config import get_settings
    get_settings.cache_clear()
    from agents.context import FounderContext

    print(f"  → ingesting {source} …", file=sys.stderr, flush=True)
    try:
        if _looks_like_repo(source):
            ctx = FounderContext.from_github(source)
        else:
            ctx = FounderContext.from_folder(os.path.expanduser(source))
        briefing = ctx.summary()
    except Exception as exc:
        print(f"I couldn't read `{source}` — {exc}. "
              "Double-check the repo is public (or the path exists) and try again.")
        sys.exit(0)

    # Persist the active context pointer for hermes_run.py.
    product_name = getattr(ctx, "product_name", "") or ""
    ctx_path = _active_ctx_path(product_name)
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(json.dumps({
        "source": source,
        "kind": "github" if _looks_like_repo(source) else "folder",
        "product_name": product_name,
        "n_files": len(ctx.files),
    }), encoding="utf-8")

    # One-line gist from the briefing for the confirmation.
    gist = ""
    for ln in briefing.splitlines():
        s = ln.strip("*-# ").strip()
        if s and not s.startswith("#"):
            gist = s
            break

    print(
        f"✅ **Locked onto your startup** — read {len(ctx.files)} files from "
        f"`{source}`.\n\n"
        + (f"_{gist[:280]}_\n\n" if gist else "")
        + "I'll sell on their behalf from here. When you're ready, just say "
          "**\"find a healthcare customer\"** (or any vertical) and I'll build "
          "the whole campaign — verified prospect, live prototype, AI "
          "walkthrough video, pitch deck, and a drafted email."
    )


if __name__ == "__main__":
    main()
