"""Revenant — the autonomous outbound engineer.

The ``ghost`` package is the agent-independent pipeline. Every stage
(recon → gate → profiler → builder → deploy → director → voice → outreach)
is a plain module that can be run and tested without Hermes in the loop.
Hermes skills in ``skills/`` are thin wrappers over these functions.
"""

__version__ = "0.1.0"
