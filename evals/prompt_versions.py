"""Prompt version registry — the (module, constant, PROMPT_VERSION) triples
this session's whole tracing effort correlates back to. See
docs/evals-observability-design.md §2.4.

Deliberately a plain data list, not a dynamic scan of the codebase: an
un-scanned new prompt silently missing from here is exactly the kind of
gap this file exists to prevent, so adding a prompt here is a conscious
step, not something a grep can do for you.
"""

from __future__ import annotations

import importlib

# (module path, prompt constant name)
PROMPT_MODULES: list[tuple[str, str]] = [
    ("agents.engineer.prompt", "ENGINEER_SYSTEM"),
    ("agents.engineer.planner", "_PLANNER_SYSTEM"),
    ("agents.engineer.polish", "_PROMPT"),
    ("agents.sales.prompt", "SALES_SYSTEM"),
    ("agents.director.prompt", "DIRECTOR_SYSTEM"),
    ("agents.research.prompt", "RESEARCH_SYSTEM"),
    ("agents.orchestrator.prompt", "BASE_SYSTEM"),
]


def current_shas() -> dict[str, str]:
    """{module_path: sha12} for every prompt in PROMPT_MODULES, using the
    same fingerprint the trace shim stamps onto live spans (ghost.trace.
    prompt_fingerprint) — so this file's baseline and a real trace's
    prompt_sha attribute are directly comparable."""
    from ghost.trace import prompt_fingerprint

    out = {}
    for mod_path, const_name in PROMPT_MODULES:
        mod = importlib.import_module(mod_path)
        text = getattr(mod, const_name)
        out[mod_path] = prompt_fingerprint(text)
    return out


def declared_versions() -> dict[str, str]:
    """{module_path: PROMPT_VERSION} — fails loudly (KeyError/AttributeError)
    if any module in PROMPT_MODULES is missing the constant; that's the
    point, see ghost/tests/test_evals_offline.py::test_prompt_versions_declared."""
    out = {}
    for mod_path, _const_name in PROMPT_MODULES:
        mod = importlib.import_module(mod_path)
        out[mod_path] = mod.PROMPT_VERSION
    return out
