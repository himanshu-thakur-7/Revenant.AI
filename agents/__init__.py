"""Revenant agent fleet.

Five agents, each a proper LLM-driven loop with real tools:

* :mod:`agents.orchestrator` — Agent 0. The founder chats with it. It holds the
  startup context (docs + code), reasons about who to sell to, and delegates.
* Agents 1–4 (Research / Engineer / Director / Sales) land as siblings under
  this package once Agent 0 is perfect.

All shared machinery lives in :mod:`agents.base` (the tool loop),
:mod:`agents.tools` (tool decorator + registry), :mod:`agents.context` (founder
knowledge ingestion), and :mod:`agents.memory` (conversation history).
"""

from __future__ import annotations

__version__ = "0.1.0"
