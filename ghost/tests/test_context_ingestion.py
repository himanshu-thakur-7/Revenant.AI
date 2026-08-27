"""agents/context.py — repo/folder ingestion.

This decides what the LLM is ever allowed to know about the founder's
product. Everything downstream (the briefing, the prototype spec, the
pitch) is built on whatever survives this filter, so a bug here is
invisible at the point of failure and shows up much later as "the
prototype doesn't understand our product".

Offline and hermetic: builds real directory trees under tmp_path.
"""

from __future__ import annotations

import agents.context as ctx_mod
from agents.context import FounderContext


def _tree(root, files: dict[str, str]):
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


# ── what gets picked up ───────────────────────────────────────────────

def test_source_and_docs_are_ingested(tmp_path):
    _tree(tmp_path, {"README.md": "# Acme", "src/main.py": "print(1)"})
    c = FounderContext.from_folder(tmp_path)
    assert "README.md" in c.files
    assert "src/main.py" in c.files


def test_readme_sorts_before_deep_source(tmp_path):
    # summary() feeds the model the highest-signal files first within a
    # budget, so ordering decides what survives truncation.
    _tree(tmp_path, {"a/b/c/deep.py": "x", "README.md": "# Acme"})
    c = FounderContext.from_folder(tmp_path)
    assert list(c.files)[0] == "README.md"


def test_shallow_files_sort_before_deeper_ones(tmp_path):
    _tree(tmp_path, {"deep/nested/x.py": "x", "top.py": "y"})
    c = FounderContext.from_folder(tmp_path)
    assert list(c.files).index("top.py") < list(c.files).index("deep/nested/x.py")


def test_extensionless_priority_files_are_included(tmp_path):
    _tree(tmp_path, {"Dockerfile": "FROM python", "Makefile": "all:"})
    c = FounderContext.from_folder(tmp_path)
    assert "Dockerfile" in c.files and "Makefile" in c.files


def test_env_example_is_included_but_real_env_is_not(tmp_path):
    # .env.example documents configuration and is safe; a real .env is
    # secrets and must never reach a model.
    _tree(tmp_path, {".env.example": "KEY=", ".env": "KEY=sk-real-secret"})
    c = FounderContext.from_folder(tmp_path)
    assert ".env.example" in c.files
    assert ".env" not in c.files
    assert "sk-real-secret" not in "".join(c.files.values())


# ── what gets excluded ────────────────────────────────────────────────

def test_dependency_and_build_dirs_are_skipped(tmp_path):
    _tree(tmp_path, {
        "node_modules/pkg/index.js": "junk",
        ".venv/lib/thing.py": "junk",
        "dist/bundle.js": "junk",
        "__pycache__/x.py": "junk",
        "src/real.py": "real",
    })
    c = FounderContext.from_folder(tmp_path)
    assert "src/real.py" in c.files
    assert not any(d in k for k in c.files
                   for d in ("node_modules", ".venv", "dist", "__pycache__"))


def test_hidden_directories_are_skipped(tmp_path):
    _tree(tmp_path, {".github/workflows/ci.yml": "on: push", "src/x.py": "x"})
    c = FounderContext.from_folder(tmp_path)
    assert not any(k.startswith(".github") for k in c.files)


def test_binary_extensions_are_not_ingested(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    _tree(tmp_path, {"README.md": "# Acme"})
    c = FounderContext.from_folder(tmp_path)
    assert "logo.png" not in c.files


def test_a_binary_file_wearing_a_text_extension_is_dropped(tmp_path):
    # Invalid UTF-8 in a .md — would otherwise inject mojibake into the
    # prompt and waste budget.
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe\x00\x01binary")
    _tree(tmp_path, {"good.md": "# fine"})
    c = FounderContext.from_folder(tmp_path)
    assert "bad.md" not in c.files
    assert "good.md" in c.files


def test_unknown_extensions_are_ignored(tmp_path):
    _tree(tmp_path, {"data.parquet": "x", "notes.md": "y"})
    c = FounderContext.from_folder(tmp_path)
    assert "data.parquet" not in c.files


# ── budgets ───────────────────────────────────────────────────────────

def test_a_single_huge_file_is_truncated_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx_mod, "_MAX_FILE_BYTES", 500)
    _tree(tmp_path, {"big.md": "x" * 5000})
    c = FounderContext.from_folder(tmp_path)
    assert "big.md" in c.files
    assert len(c.files["big.md"]) <= 500


def test_total_ingest_respects_the_global_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx_mod, "_MAX_TOTAL_BYTES", 1000)
    _tree(tmp_path, {f"f{i}.md": "y" * 400 for i in range(10)})
    c = FounderContext.from_folder(tmp_path)
    assert sum(len(v) for v in c.files.values()) <= 1000


def test_the_budget_stops_ingestion_rather_than_looping(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx_mod, "_MAX_TOTAL_BYTES", 10)
    _tree(tmp_path, {f"f{i}.md": "z" * 100 for i in range(5)})
    c = FounderContext.from_folder(tmp_path)
    assert sum(len(v) for v in c.files.values()) <= 10


# ── product_name derivation ───────────────────────────────────────────

def test_product_name_from_the_readme_h1(tmp_path):
    _tree(tmp_path, {"README.md": "# Shroud\n\nPII redaction."})
    assert FounderContext.from_folder(tmp_path).product_name == "Shroud"


def test_product_name_strips_a_tagline_after_a_dash(tmp_path):
    _tree(tmp_path, {"README.md": "# Shroud — redact PII fast\n"})
    assert FounderContext.from_folder(tmp_path).product_name == "Shroud"


def test_a_generic_readme_heading_is_not_used_as_the_name(tmp_path):
    # "Welcome to ..." is a template heading, not a product name.
    _tree(tmp_path, {"README.md": "# Welcome to the project\n"})
    assert FounderContext.from_folder(tmp_path).product_name != "Welcome to the project"


def test_product_name_falls_back_to_the_folder_name(tmp_path):
    d = tmp_path / "acme-api"
    d.mkdir()
    _tree(d, {"src/x.py": "x"})
    assert "Acme" in FounderContext.from_folder(d).product_name


def test_product_name_never_returns_empty(tmp_path):
    _tree(tmp_path, {"x.py": "pass"})
    assert FounderContext.from_folder(tmp_path).product_name.strip()


# ── multi-source merging ──────────────────────────────────────────────

def test_two_sources_merge_without_losing_files(tmp_path):
    a = _tree(tmp_path / "a", {"README.md": "# A", "a.py": "1"})
    b = _tree(tmp_path / "b", {"b.py": "2"})
    c = FounderContext.from_folder(a)
    c._ingest(b)
    assert "a.py" in c.files and "b.py" in c.files


def test_a_colliding_path_from_a_second_source_is_namespaced(tmp_path):
    # Both sources have README.md; the second must not silently overwrite
    # the first — that would lose one company's description entirely.
    a = _tree(tmp_path / "a", {"README.md": "# FROM A"})
    b = _tree(tmp_path / "b", {"README.md": "# FROM B"})
    c = FounderContext.from_folder(a)
    c._ingest(b)
    joined = "".join(c.files.values())
    assert "FROM A" in joined and "FROM B" in joined


# ── robustness ────────────────────────────────────────────────────────

def test_an_empty_folder_yields_no_files_rather_than_raising(tmp_path):
    c = FounderContext.from_folder(tmp_path)
    assert c.files == {}


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path):
    _tree(tmp_path, {"ok.md": "# fine", "locked.md": "secret"})
    (tmp_path / "locked.md").chmod(0o000)
    try:
        c = FounderContext.from_folder(tmp_path)
        assert "ok.md" in c.files          # the readable file still lands
    finally:
        (tmp_path / "locked.md").chmod(0o644)


def test_a_nonexistent_folder_raises_clearly(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        FounderContext.from_folder(tmp_path / "nope")


def test_a_file_path_instead_of_a_folder_raises(tmp_path):
    import pytest
    f = tmp_path / "x.md"
    f.write_text("x")
    with pytest.raises(ValueError):
        FounderContext.from_folder(f)


def test_a_symlink_loop_does_not_hang(tmp_path):
    # os.walk does not follow symlinks by default; asserting it so a future
    # switch to followlinks=True cannot silently introduce an infinite walk.
    _tree(tmp_path, {"README.md": "# Acme"})
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    c = FounderContext.from_folder(tmp_path)
    assert "README.md" in c.files
