from __future__ import annotations

from pathlib import Path

from ghost.agents import AgentRole, build_orchestrator_state, save_state, scan_knowledge


def test_orchestrator_builds_delegation_plan(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text(
        "QueuePilot AI routes customer support tickets, detects SLA breach risk, "
        "and drafts response macros for ecommerce support teams."
    )
    (docs / "app.py").write_text("def route_ticket(ticket): return 'billing'")

    state = build_orchestrator_state(
        "We sell QueuePilot AI for support teams drowning in ticket backlog.",
        scan_roots=[docs],
        slug="queuepilot-test",
    )

    assert state.brief.company_name
    assert state.seller.slug == "queuepilot-test"
    assert len(state.knowledge) >= 3  # founder blurb + docs/code
    assert {task.role for task in state.tasks} == {
        AgentRole.RESEARCH,
        AgentRole.ENGINEER,
        AgentRole.DIRECTOR,
        AgentRole.SALES,
    }
    assert state.task_for(AgentRole.ENGINEER)


def test_scan_knowledge_skips_generated_dirs(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "noise.ts").write_text("ignore me")
    (tmp_path / "product.md").write_text("Support operations product docs")

    items = scan_knowledge(tmp_path)

    assert [i.source_path for i in items] == ["product.md"]


def test_save_state_writes_latest(tmp_path: Path) -> None:
    state = build_orchestrator_state("We sell LedgerLoop for fintech event delivery.")

    out = save_state(state, tmp_path)

    assert out.exists()
    assert (tmp_path / "latest.json").exists()
