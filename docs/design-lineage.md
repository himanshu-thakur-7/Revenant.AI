# Design lineage

Revenant is the buildathon incarnation of a larger portfolio design. The full
canonical design lives in three documents (the "Ghost SDR" papers):

- **Master Plan v1.0** — 34 pages. System architecture, the seven-agent swarm,
  the campaign state machine, cost model, compliance, 8-week build plan.
- **Addendum 001 · Signal Quality** — the signal-quality gate that runs *before*
  the swarm: honest source assessment, the three-stage filter, the forensic
  Architect prompt, and the golden-set test strategy.
- **Addendum 002 · Filter Corrections** — parallel forensic fetches, the
  combiner's single-source override, and the Stage-1b provider decision.

## What changed for the buildathon

The master plan targets a Go + gRPC + k3s + NATS + Qdrant + MinIO + Firecracker
stack over 8 weeks. That's right for the portfolio project and wrong for a
one-day sponsor buildathon. Revenant keeps the *design* and re-targets the
*implementation* onto the sponsor stack:

| Master plan | Revenant (buildathon) |
|---|---|
| Go orchestrator + Python cognition over gRPC | One readable Python pipeline (`ghost/`) |
| Postgres + NATS + Redis + Qdrant + MinIO | Convex (truth ledger + realtime + webhook) |
| Firecracker microVM sandbox | "Build must succeed" verification (site renders + 200) |
| LangGraph-style typed graph | Sequential, checkpointed `pipeline.py` |
| Voice memo (audio only) | Voice memo **+ AI-recorded Loom-style walkthrough** |
| Orchestrator daemon + cron | **Hermes** skills + Hermes cron |

## What was ported faithfully

- The **signal-quality gate** (`ghost/gate.py`): regex anomaly rules, LLM
  stage-1b, the weighted combiner, and the single-source override — with the
  exact golden-set cases from the addenda.
- The **evidence model**: verbatim excerpts, cited on the microsite.
- The **campaign state machine** and unit-economics cost logging.
- The **compliance posture** (`docs/compliance.md`).
- The **persistence engine** (long memory → timed re-engagement).
