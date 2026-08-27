"""Per-startup (tenant) identity and state isolation.

Phase 1 of the multi-tenant work. Before this module, ALL of Revenant's
durable state was a single global set of files under ~/.revenant/:

    ~/.revenant/active_context.json   which startup we sell for
    ~/.revenant/last_shortlist.json   the last shortlist
    ~/.revenant/last_campaign.json    the last campaign

That is genuinely single-tenant: "which startup we sell for" was one
machine-wide setting. Two different customers using the same deployment
would silently overwrite each other's context, shortlist, and campaign —
not a subtle race, just last-writer-wins on a shared file. Everything
multi-tenant (per-startup preferences, per-startup learned memory) is
blocked on fixing that first, which is what this module does.

## How a tenant is identified

The tenant key is `slug(startup)` — the startup NAME already threaded
through most of the MCP tools (build_prototype, film_walkthrough,
draft_outreach, build_full_outreach all take `startup`). Deriving the
key from data already flowing through the system means no new parameter
on those tools and no console-side change for the core isolation to work.

Three tools (draft_email, status, critique_campaign) deliberately do NOT
take a startup — they act on "the last thing built". For those,
resolve() falls back to a `last_active` pointer file written whenever a
startup-bearing tool runs. That preserves the existing UX ("build, then
just say draft the email") while still resolving to a real tenant.

## Trust boundary — read this before relying on it for security

This module provides ISOLATION, not AUTHORIZATION. It guarantees that
startup A's state lives in a different directory from startup B's. It
does NOT verify that the caller is entitled to act as startup A — the
tenant key comes straight from a tool argument, so anything that can
call the MCP tools can name any tenant.

That is intentional for Phase 1 (the MCP server is currently reachable
only via the founder's own authenticated Hermes gateway), and it is
exactly what Phase 4 (auth hardening) has to close before this is a real
multi-customer product: the console's session cookie must become the
authoritative source of the tenant id, and the tool argument must be
checked against it rather than trusted. Until then, treat tenant
separation here as "prevents accidental cross-contamination", NOT as
"prevents a malicious caller reading another customer's data".
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

REVENANT_HOME = Path.home() / ".revenant"
STARTUPS_DIR = REVENANT_HOME / "startups"
LAST_ACTIVE_PATH = REVENANT_HOME / "last_active_tenant"

# The tenant used when nothing else resolves — also the migration target
# for pre-multi-tenant state, so an existing single-user install keeps
# working with its history intact rather than starting empty.
DEFAULT_TENANT = "default"

# The three legacy global files, and their per-tenant filenames (same
# names — only the directory changes, which keeps the on-disk layout
# familiar and makes the migration a plain move).
_STATE_FILES = ("active_context.json", "last_shortlist.json", "last_campaign.json")


def slug(text: str) -> str:
    """Same slugify as evals/bundle.py::slug and
    agents/engineer/prototype.py::_slug. Duplicated rather than imported
    to keep this module's import graph free of the heavy agent packages
    (it is imported by agents/mcp_server.py at startup) —
    ghost/tests/test_tenancy.py is what actually keeps them in sync."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "default"


def tenant_home(tenant: str) -> Path:
    """The isolated state directory for one tenant. Note this passes the
    id through slug() again: tenant_home() is called with values that
    came from a tool argument, and a tenant id containing '..' or '/'
    would otherwise escape STARTUPS_DIR entirely. slug() strips every
    character that could traverse, so this is the containment point."""
    return STARTUPS_DIR / slug(tenant)


def state_path(tenant: str, filename: str) -> Path:
    if filename not in _STATE_FILES:
        raise ValueError(f"unknown state file {filename!r}; expected one of {_STATE_FILES}")
    return tenant_home(tenant) / filename


def set_active(tenant: str) -> None:
    """Record the tenant a startup-bearing tool just acted for, so the
    tools that take no startup (draft_email/status/critique_campaign)
    can resolve to it. Never raises — a failure here degrades to
    DEFAULT_TENANT on the next resolve(), which is recoverable; letting
    it propagate would fail a build that had otherwise succeeded."""
    try:
        REVENANT_HOME.mkdir(parents=True, exist_ok=True)
        LAST_ACTIVE_PATH.write_text(slug(tenant), encoding="utf-8")
    except Exception:
        pass


def get_active() -> str:
    try:
        if LAST_ACTIVE_PATH.exists():
            return slug(LAST_ACTIVE_PATH.read_text().strip())
    except Exception:
        pass
    return DEFAULT_TENANT


def resolve(startup: str = "") -> str:
    """The one place tenant identity is decided.

    An explicit startup name always wins — that's the caller telling us
    exactly who this is for. Only when a tool carries no startup at all
    do we fall back to whoever was last active.
    """
    if startup and startup.strip():
        return slug(startup)
    return get_active()


def migrate_legacy_state(tenant: str = DEFAULT_TENANT) -> list[str]:
    """One-time move of the pre-multi-tenant global files into a tenant
    directory. Returns the filenames actually moved (empty list if
    there was nothing to migrate, which is the normal steady state).

    Deliberately a MOVE, not a copy: leaving the legacy files in place
    would mean any code path still reading the old global location
    silently sees stale state forever while the real state moves on —
    the exact "looks fine, is actually wrong" failure mode this codebase
    has been bitten by repeatedly. Moving makes a missed call site fail
    loudly (file not found) instead of quietly.

    Idempotent: once a tenant file exists, the legacy file is left alone
    rather than clobbering newer per-tenant state with older global state.
    """
    moved: list[str] = []
    try:
        home = tenant_home(tenant)
        home.mkdir(parents=True, exist_ok=True)
        for name in _STATE_FILES:
            legacy = REVENANT_HOME / name
            target = home / name
            if legacy.exists() and not target.exists():
                shutil.move(str(legacy), str(target))
                moved.append(name)
        if moved:
            _write_migration_marker(tenant, moved)
    except Exception:
        pass
    return moved


def _write_migration_marker(tenant: str, moved: list[str]) -> None:
    """Leave a breadcrumb so a human debugging 'where did my state go'
    can see exactly what moved and when, rather than finding an empty
    ~/.revenant and guessing."""
    try:
        (tenant_home(tenant) / "MIGRATED.json").write_text(
            json.dumps({
                "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "from": str(REVENANT_HOME),
                "files": moved,
                "note": "Pre-multi-tenant global state, moved into this tenant "
                        "directory by agents/tenancy.py::migrate_legacy_state().",
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def list_tenants() -> list[str]:
    """Every tenant with state on disk, oldest-known first by name.
    Used by status() and by anything that needs to enumerate customers."""
    try:
        if not STARTUPS_DIR.exists():
            return []
        return sorted(p.name for p in STARTUPS_DIR.iterdir() if p.is_dir())
    except Exception:
        return []
