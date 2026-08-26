"""T0 guard: every prompt in evals.prompt_versions.PROMPT_MODULES must
declare PROMPT_VERSION, and if its text changed since the checked-in
baseline (evals/baselines/prompt_shas.json), PROMPT_VERSION must have
changed too. See docs/evals-observability-design.md §2.4.

When this legitimately fails (you changed a prompt AND correctly bumped
its PROMPT_VERSION): regenerate the baseline —

    python -c "
    import json
    from evals.prompt_versions import current_shas, declared_versions
    shas, versions = current_shas(), declared_versions()
    data = {m: {'sha': shas[m], 'version': versions[m]} for m in shas}
    open('evals/baselines/prompt_shas.json', 'w').write(json.dumps(data, indent=2, sort_keys=True) + '\n')
    "

then commit the updated baseline alongside the prompt change.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.prompt_versions import PROMPT_MODULES, current_shas, declared_versions

_BASELINE_PATH = Path(__file__).resolve().parent.parent.parent / "evals" / "baselines" / "prompt_shas.json"


def test_every_prompt_module_declares_a_version():
    versions = declared_versions()
    for mod_path, _const_name in PROMPT_MODULES:
        assert mod_path in versions
        v = versions[mod_path]
        assert isinstance(v, str) and "@" in v, (
            f"{mod_path}.PROMPT_VERSION={v!r} — expected a 'name@N' shaped version string"
        )


def test_prompt_edits_bump_their_version():
    baseline = json.loads(_BASELINE_PATH.read_text())
    shas = current_shas()
    versions = declared_versions()

    stale = []
    for mod_path, _const_name in PROMPT_MODULES:
        base = baseline.get(mod_path)
        if base is None:
            continue  # a brand-new module — nothing to compare against yet
        if shas[mod_path] != base["sha"] and versions[mod_path] == base["version"]:
            stale.append(mod_path)

    assert not stale, (
        f"prompt text changed but PROMPT_VERSION wasn't bumped for: {stale}. "
        f"Bump the PROMPT_VERSION constant in each of those files, then "
        f"regenerate evals/baselines/prompt_shas.json (see this test file's "
        f"module docstring for the exact command) and commit both together."
    )
