"""T0 coverage for the per-tenant persistent briefing cache
(agents/context.py::summary and its helpers). Phase 2 of the
multi-tenant work.

No network: every test builds a FounderContext by hand and stubs the LLM
call, so this exercises the cache logic itself rather than ingestion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agents.context as context_mod
import agents.tenancy as tenancy
from agents.context import FounderContext


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "REVENANT_HOME", tmp_path)
    monkeypatch.setattr(tenancy, "STARTUPS_DIR", tmp_path / "startups")
    monkeypatch.setattr(tenancy, "LAST_ACTIVE_PATH", tmp_path / "last_active_tenant")
    return tmp_path


def _ctx(files=None, source="github.com/acme/api"):
    return FounderContext(
        source=source,
        root=Path("/nonexistent"),
        files=files if files is not None else {"README.md": "# Acme\nWe do things."},
    )


def _stub_llm(monkeypatch, returns):
    """Replace the strong-model call; records how many times it ran."""
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        return returns

    monkeypatch.setattr(context_mod, "complete_strong", fake)
    return calls


# ── tenant derivation ─────────────────────────────────────────────────

def test_tenant_derives_from_ingested_product_name(home):
    ctx = _ctx({"README.md": "# Acme\nbody"})
    assert ctx.product_name == "Acme"
    assert ctx._tenant() == "acme"


def test_two_startups_get_separate_briefing_files(home, monkeypatch):
    _stub_llm(monkeypatch, "BRIEFING TEXT")
    a = _ctx({"README.md": "# Acme\nbody"})
    b = _ctx({"README.md": "# Globex\nbody"})
    a.summary()
    b.summary()

    a_file = tenancy.tenant_file("acme", "briefing.md")
    b_file = tenancy.tenant_file("globex", "briefing.md")
    assert a_file.exists() and b_file.exists()
    assert a_file != b_file


# ── caching behaviour ─────────────────────────────────────────────────

def test_summary_persists_and_is_reused_by_a_fresh_object(home, monkeypatch):
    calls = _stub_llm(monkeypatch, "REAL BRIEFING")
    first = _ctx()
    assert first.summary() == "REAL BRIEFING"
    assert calls["n"] == 1

    # A brand-new object (simulating a separate process) with identical
    # ingested content must hit the DISK cache, not the LLM.
    second = _ctx()
    assert second.summary() == "REAL BRIEFING"
    assert calls["n"] == 1, "second context re-ran the LLM instead of using the disk cache"


def test_changed_content_invalidates_the_cache(home, monkeypatch):
    calls = _stub_llm(monkeypatch, "V1")
    _ctx({"README.md": "# Acme\noriginal"}).summary()
    assert calls["n"] == 1

    monkeypatch.setattr(context_mod, "complete_strong",
                        lambda prompt, **kw: "V2")
    changed = _ctx({"README.md": "# Acme\nEDITED"})
    assert changed.summary() == "V2", "stale briefing served after the sources changed"


def test_same_length_edit_still_invalidates(home, monkeypatch):
    # Guards the fingerprint being content-based rather than size-based:
    # a same-length edit is exactly what a weaker fingerprint would miss.
    _stub_llm(monkeypatch, "V1")
    _ctx({"README.md": "# Acme\nAAAA"}).summary()

    monkeypatch.setattr(context_mod, "complete_strong", lambda prompt, **kw: "V2")
    assert _ctx({"README.md": "# Acme\nBBBB"}).summary() == "V2"


def test_in_memory_cache_short_circuits_before_disk(home, monkeypatch):
    calls = _stub_llm(monkeypatch, "ONCE")
    ctx = _ctx()
    ctx.summary()
    ctx.summary()
    ctx.summary()
    assert calls["n"] == 1


# ── the offline-stub poisoning guard ──────────────────────────────────

def test_offline_stub_is_never_written_to_the_cache(home, monkeypatch):
    """In offline mode complete_strong() returns the offline stub verbatim.
    Persisting that would serve a placeholder to every later LIVE process
    as if it were a real product briefing."""
    ctx = _ctx()
    # Reproduce the exact stub summary() builds internally.
    stub = (
        f"### Startup briefing (offline stub for {ctx.source})\n"
        f"- {len(ctx.files)} files ingested; live LLM disabled.\n"
        f"- Run with `REVENANT_MODE=live` and an `LLM_API_KEY` for a real summary."
    )
    monkeypatch.setattr(context_mod, "complete_strong", lambda prompt, **kw: stub)

    assert ctx.summary().strip() == stub.strip()
    assert not tenancy.tenant_file("acme", "briefing.md").exists(), (
        "offline stub was cached to disk and will poison later live runs"
    )


def test_empty_llm_response_is_not_cached(home, monkeypatch):
    _stub_llm(monkeypatch, "   ")
    _ctx().summary()
    assert not tenancy.tenant_file("acme", "briefing.md").exists()


# ── resilience: a broken cache must never block a build ───────────────

def test_corrupt_meta_falls_back_to_recomputing(home, monkeypatch):
    calls = _stub_llm(monkeypatch, "GOOD")
    _ctx().summary()
    assert calls["n"] == 1

    tenancy.tenant_file("acme", "briefing_meta.json").write_text("{ not json")
    fresh = _ctx()
    assert fresh.summary() == "GOOD"
    assert calls["n"] == 2, "corrupt metadata should force a recompute, not raise"


def test_missing_body_with_present_meta_recomputes(home, monkeypatch):
    calls = _stub_llm(monkeypatch, "GOOD")
    _ctx().summary()
    tenancy.tenant_file("acme", "briefing.md").unlink()

    assert _ctx().summary() == "GOOD"
    assert calls["n"] == 2


def test_meta_records_useful_provenance(home, monkeypatch):
    _stub_llm(monkeypatch, "BRIEF")
    _ctx().summary()
    meta = json.loads(tenancy.tenant_file("acme", "briefing_meta.json").read_text())
    assert meta["product_name"] == "Acme"
    assert meta["n_files"] == 1
    assert meta["source"] == "github.com/acme/api"
    assert meta["fingerprint"]
    assert meta["cached_at"]


# ── tenant_file containment ───────────────────────────────────────────

@pytest.mark.parametrize("hostile", ["../escape.md", "a/b.md", "/abs.md", "..", "x/../../y.md"])
def test_tenant_file_never_escapes_the_tenant_dir(home, hostile):
    resolved = tenancy.tenant_file("acme", hostile).resolve()
    assert tenancy.tenant_home("acme").resolve() == resolved.parent


def test_tenant_file_preserves_a_normal_extension(home):
    assert tenancy.tenant_file("acme", "briefing.md").name == "briefing.md"
    assert tenancy.tenant_file("acme", "briefing_meta.json").name == "briefing-meta.json"
