"""Hermes Agent integration — reads the local Hermes state at boot.

Revenant's fleet is a Hermes Agent skill. The Hermes framework installed at
``~/.hermes`` invokes the fleet either directly (headless: ``hermes chat``
picks up the ``revenant-outbound`` skill) or indirectly through the Telegram
gateway (this Python UI wraps the same underlying pipeline).

This module reads Hermes's local state — version, configured model, registered
skills — from disk so we can surface it in the bot boot sequence without a
90-second ``hermes -z`` subprocess round-trip.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_INIT = HERMES_AGENT / "hermes_cli" / "__init__.py"
HERMES_CONFIG = HERMES_HOME / "config.yaml"
HERMES_SKILLS = HERMES_HOME / "skills"

REVENANT_SKILL = "revenant-outbound"


@dataclass
class HermesStatus:
    installed: bool = False
    version: str = ""
    model: str = ""
    provider: str = ""
    skill_registered: bool = False
    skill_count: int = 0

    @property
    def summary(self) -> str:
        if not self.installed:
            return "not installed (skill-only mode)"
        parts = [f"v{self.version}" if self.version else "installed"]
        if self.model:
            parts.append(self.model)
        if self.skill_registered:
            parts.append(f"skill ✓")
        return " · ".join(parts)


_VERSION_RX = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]")
_MODEL_RX = re.compile(r"^\s*default\s*:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.M)
_PROVIDER_RX = re.compile(r"^\s*provider\s*:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.M)


def detect() -> HermesStatus:
    """Read Hermes state from disk. Never raises — returns
    ``HermesStatus(installed=False)`` if anything is missing."""
    st = HermesStatus()
    if not HERMES_HOME.exists():
        return st
    st.installed = True

    # version
    if HERMES_INIT.exists():
        try:
            m = _VERSION_RX.search(HERMES_INIT.read_text())
            if m:
                st.version = m.group(1)
        except OSError:
            pass

    # model + provider from config.yaml (strip yaml comments)
    if HERMES_CONFIG.exists():
        try:
            raw = HERMES_CONFIG.read_text()
            # kill in-line comments so the regex doesn't grab '# defaults'
            no_comments = re.sub(r"#.*$", "", raw, flags=re.M)
            mm = _MODEL_RX.search(no_comments)
            if mm:
                st.model = mm.group(1).strip()
            pm = _PROVIDER_RX.search(no_comments)
            if pm:
                st.provider = pm.group(1).strip()
        except OSError:
            pass

    # skills — count enabled hub skills + check our own
    if HERMES_SKILLS.exists():
        try:
            names = [p.name for p in HERMES_SKILLS.iterdir() if p.is_dir()]
            st.skill_count = len(names)
            st.skill_registered = REVENANT_SKILL in names
        except OSError:
            pass
    # local repo copy — also count as registered if the hub copy is missing
    repo_skill = Path.home() / "Revenant.AI" / "skills" / REVENANT_SKILL / "SKILL.md"
    if not st.skill_registered and repo_skill.exists():
        st.skill_registered = True

    return st
