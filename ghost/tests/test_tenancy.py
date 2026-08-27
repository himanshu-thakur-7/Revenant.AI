"""T0 coverage for agents/tenancy.py — per-startup state isolation.
Pure path/file logic against tmp_path, no network.

Phase 1 of the multi-tenant work. The containment tests here matter more
than usual: tenant ids arrive from MCP tool arguments, so a tenant id
that escapes STARTUPS_DIR would let one caller read or clobber another
tenant's state. See the trust-boundary note in tenancy.py's docstring
for what this does and does not guarantee.
"""

from __future__ import annotations

import json

import pytest

import agents.tenancy as tenancy


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point every tenancy path at tmp_path so tests never touch the real
    ~/.revenant. All four module-level paths must be patched together —
    patching only REVENANT_HOME would leave STARTUPS_DIR pointing at the
    developer's actual home directory."""
    monkeypatch.setattr(tenancy, "REVENANT_HOME", tmp_path)
    monkeypatch.setattr(tenancy, "STARTUPS_DIR", tmp_path / "startups")
    monkeypatch.setattr(tenancy, "LAST_ACTIVE_PATH", tmp_path / "last_active_tenant")
    return tmp_path


def test_slug_matches_the_other_two_slug_implementations():
    # tenancy.slug is duplicated from evals/bundle.py and
    # agents/engineer/prototype.py to keep this module's import graph
    # light; this test is what actually keeps the three in sync.
    from agents.engineer.prototype import _slug as engineer_slug
    from evals.bundle import slug as bundle_slug

    for name in ["Razorpay", "Bombay Shaving Company", "1mg", "  weird--Name!! "]:
        assert tenancy.slug(name) == engineer_slug(name) == bundle_slug(name)


def test_slug_empty_falls_back_to_default():
    # Note this differs from evals/bundle.py::slug, which falls back to
    # "prospect" — different domain, different sensible default, so the
    # sync test above deliberately uses only non-empty names.
    assert tenancy.slug("") == "default"
    assert tenancy.slug("   ") == "default"


# ── containment: a tenant id must never escape STARTUPS_DIR ────────────

@pytest.mark.parametrize("hostile", [
    "../escape",
    "../../etc",
    "a/../../b",
    "/absolute/path",
    "..",
    "./.",
    "foo/bar",
])
def test_tenant_home_never_escapes_startups_dir(home, hostile):
    resolved = tenancy.tenant_home(hostile).resolve()
    startups = (home / "startups").resolve()
    assert startups in resolved.parents or resolved == startups, (
        f"tenant id {hostile!r} escaped to {resolved}"
    )


def test_two_tenants_get_different_directories(home):
    a = tenancy.tenant_home("Razorpay")
    b = tenancy.tenant_home("Stripe")
    assert a != b


def test_tenant_home_is_stable_across_name_spellings(home):
    assert tenancy.tenant_home("Razorpay") == tenancy.tenant_home("  razorpay  ")


def test_state_path_rejects_unknown_filenames(home):
    with pytest.raises(ValueError):
        tenancy.state_path("acme", "../../../secrets.json")
    with pytest.raises(ValueError):
        tenancy.state_path("acme", "arbitrary.json")


def test_state_path_accepts_the_three_real_files(home):
    for name in ("active_context.json", "last_shortlist.json", "last_campaign.json"):
        p = tenancy.state_path("acme", name)
        assert p.name == name
        assert p.parent == tenancy.tenant_home("acme")


# ── resolve(): explicit startup wins, else last-active, else default ───

def test_resolve_prefers_explicit_startup(home):
    tenancy.set_active("Stripe")
    assert tenancy.resolve("Razorpay") == "razorpay"


def test_resolve_falls_back_to_last_active(home):
    tenancy.set_active("Stripe")
    assert tenancy.resolve("") == "stripe"


def test_resolve_with_nothing_set_is_default(home):
    assert tenancy.resolve("") == tenancy.DEFAULT_TENANT


def test_resolve_ignores_whitespace_only_startup(home):
    tenancy.set_active("Stripe")
    assert tenancy.resolve("   ") == "stripe"


def test_get_active_survives_a_corrupt_pointer_file(home):
    (home / "last_active_tenant").write_text("\x00\x01 not a slug \x02")
    # Must not raise; whatever comes back must still be a safe slug.
    got = tenancy.get_active()
    assert got == tenancy.slug(got)


# ── migration of pre-multi-tenant global state ────────────────────────

def _legacy(home, name, payload):
    (home / name).write_text(json.dumps(payload), encoding="utf-8")


def test_migration_moves_legacy_files_into_the_tenant(home):
    _legacy(home, "active_context.json", {"source": "github.com/acme/api"})
    _legacy(home, "last_campaign.json", {"company": "PhonePe"})

    moved = tenancy.migrate_legacy_state()
    assert set(moved) == {"active_context.json", "last_campaign.json"}

    dest = tenancy.tenant_home(tenancy.DEFAULT_TENANT)
    assert json.loads((dest / "active_context.json").read_text())["source"] == "github.com/acme/api"
    assert json.loads((dest / "last_campaign.json").read_text())["company"] == "PhonePe"


def test_migration_is_a_move_not_a_copy(home):
    # Deliberate: a leftover legacy file would let any missed call site
    # keep reading stale global state forever, silently. See the
    # docstring on migrate_legacy_state().
    _legacy(home, "active_context.json", {"source": "x"})
    tenancy.migrate_legacy_state()
    assert not (home / "active_context.json").exists()


def test_migration_writes_a_breadcrumb(home):
    _legacy(home, "active_context.json", {"source": "x"})
    tenancy.migrate_legacy_state()
    marker = tenancy.tenant_home(tenancy.DEFAULT_TENANT) / "MIGRATED.json"
    assert marker.exists()
    assert "active_context.json" in json.loads(marker.read_text())["files"]


def test_migration_with_nothing_to_migrate_is_a_noop(home):
    assert tenancy.migrate_legacy_state() == []
    assert not (tenancy.tenant_home(tenancy.DEFAULT_TENANT) / "MIGRATED.json").exists()


def test_migration_never_clobbers_newer_tenant_state(home):
    # An older global file must not overwrite newer per-tenant state.
    dest = tenancy.tenant_home(tenancy.DEFAULT_TENANT)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "last_campaign.json").write_text(json.dumps({"company": "NEWER"}))
    _legacy(home, "last_campaign.json", {"company": "OLDER"})

    moved = tenancy.migrate_legacy_state()
    assert "last_campaign.json" not in moved
    assert json.loads((dest / "last_campaign.json").read_text())["company"] == "NEWER"


def test_migration_is_idempotent(home):
    _legacy(home, "active_context.json", {"source": "x"})
    first = tenancy.migrate_legacy_state()
    second = tenancy.migrate_legacy_state()
    assert first == ["active_context.json"]
    assert second == []


# ── enumeration ───────────────────────────────────────────────────────

def test_list_tenants_empty_when_nothing_exists(home):
    assert tenancy.list_tenants() == []


def test_list_tenants_reports_each_tenant_once(home):
    for name in ("Razorpay", "Stripe", "Acme"):
        tenancy.tenant_home(name).mkdir(parents=True, exist_ok=True)
    assert tenancy.list_tenants() == ["acme", "razorpay", "stripe"]
