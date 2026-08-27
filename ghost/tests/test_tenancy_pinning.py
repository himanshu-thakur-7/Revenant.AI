"""T0 coverage for Phase 4 tenant pinning — the control that turns
Phases 1-3's isolation into real authorization.

REVENANT_PINNED_TENANT is deliberately an ENVIRONMENT variable, not
anything derived from request data: the whole problem is that request
data (the `startup` tool argument) travels through an LLM we cannot
trust to faithfully preserve it. These tests lock in that a pinned
process cannot be talked into serving another tenant.
"""

from __future__ import annotations

import pytest

import agents.tenancy as tenancy


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "REVENANT_HOME", tmp_path)
    monkeypatch.setattr(tenancy, "STARTUPS_DIR", tmp_path / "startups")
    monkeypatch.setattr(tenancy, "LAST_ACTIVE_PATH", tmp_path / "last_active_tenant")
    monkeypatch.delenv("REVENANT_PINNED_TENANT", raising=False)
    return tmp_path


def _pin(monkeypatch, value):
    monkeypatch.setenv("REVENANT_PINNED_TENANT", value)


# ── unpinned: multi-tenant by design ──────────────────────────────────

def test_unpinned_has_no_pin(home):
    assert tenancy.pinned_tenant() == ""


def test_unpinned_resolves_from_the_startup_argument(home):
    assert tenancy.resolve("Acme") == "acme"


def test_unpinned_allows_any_startup(home):
    allowed, why = tenancy.assert_allowed("LiterallyAnyone")
    assert allowed and why == ""


# ── pinned: one customer, enforced ────────────────────────────────────

def test_pin_is_slugged(home, monkeypatch):
    _pin(monkeypatch, "  Acme Corp  ")
    assert tenancy.pinned_tenant() == "acme-corp"


def test_pin_overrides_an_explicit_startup_argument(home, monkeypatch):
    """The core property: a pinned process resolves to its own tenant no
    matter what the tool argument says."""
    _pin(monkeypatch, "acme")
    assert tenancy.resolve("Globex") == "acme"
    assert tenancy.resolve("") == "acme"


def test_pin_overrides_the_last_active_pointer(home, monkeypatch):
    tenancy.set_active("globex")
    _pin(monkeypatch, "acme")
    assert tenancy.resolve("") == "acme"


def test_pinned_refuses_a_different_tenant_rather_than_redirecting(home, monkeypatch):
    """Refusal, not silent redirect. A silent redirect would let a caller
    believe it acted on customer A while it actually acted on customer B —
    worse than an error."""
    _pin(monkeypatch, "acme")
    allowed, why = tenancy.assert_allowed("Globex")
    assert allowed is False
    assert "acme" in why and "globex" in why


def test_pinned_allows_its_own_tenant(home, monkeypatch):
    _pin(monkeypatch, "acme")
    allowed, why = tenancy.assert_allowed("Acme")
    assert allowed and why == ""


def test_pinned_allows_a_call_that_names_no_startup(home, monkeypatch):
    # No claim made — the pin simply applies.
    _pin(monkeypatch, "acme")
    allowed, _ = tenancy.assert_allowed("")
    assert allowed


def test_pinned_matches_case_and_spacing_variants(home, monkeypatch):
    _pin(monkeypatch, "acme-corp")
    assert tenancy.assert_allowed("Acme Corp")[0] is True
    assert tenancy.assert_allowed("  ACME   CORP ")[0] is True


def test_empty_pin_env_is_treated_as_unpinned(home, monkeypatch):
    _pin(monkeypatch, "   ")
    assert tenancy.pinned_tenant() == ""
    assert tenancy.assert_allowed("Anyone")[0] is True


def test_pin_is_read_fresh_not_cached_at_import(home, monkeypatch):
    assert tenancy.pinned_tenant() == ""
    _pin(monkeypatch, "acme")
    assert tenancy.pinned_tenant() == "acme"
    monkeypatch.delenv("REVENANT_PINNED_TENANT")
    assert tenancy.pinned_tenant() == ""


# ── pinned state paths follow the pin ─────────────────────────────────

def test_pinned_state_paths_ignore_the_startup_argument(home, monkeypatch):
    _pin(monkeypatch, "acme")
    a = tenancy.state_path(tenancy.resolve("Globex"), "last_campaign.json")
    b = tenancy.state_path(tenancy.resolve("Acme"), "last_campaign.json")
    assert a == b
    assert "acme" in str(a) and "globex" not in str(a)


def test_pinned_preferences_path_follows_the_pin(home, monkeypatch):
    _pin(monkeypatch, "acme")
    from agents import preferences

    preferences.save(tenancy.resolve("Globex"), [
        preferences.Preference(text="t", quote="q", kind="tone"),
    ])
    # Written under the PIN, not under globex.
    assert (tenancy.tenant_home("acme") / preferences.PREFS_FILE).exists()
    assert not (tenancy.tenant_home("globex") / preferences.PREFS_FILE).exists()
