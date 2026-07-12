"""Agent contracts for the real Revenant pipeline.

This module is intentionally boring: typed roles, inputs, outputs, and task
contracts. The product can only stop feeling fabricated when every agent has a
clear job, grounded inputs, and a handoff artifact the next agent can inspect.
"""

from __future__ import annotations

import json
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .llm import complete_json
from .models import SellerProfile, new_id


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    RESEARCH = "research"
    ENGINEER = "engineer"
    DIRECTOR = "director"
    SALES = "sales"


class AgentTask(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task_"))
    role: AgentRole
    objective: str
    inputs: list[str] = Field(default_factory=list)
    expected_output: str
    success_criteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["planned", "running", "blocked", "done"] = "planned"


class KnowledgeItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("kn_"))
    source_path: str
    kind: Literal["founder_blurb", "doc", "code", "config", "unknown"] = "unknown"
    title: str = ""
    excerpt: str
    signals: list[str] = Field(default_factory=list)


class FounderBrief(BaseModel):
    company_name: str
    one_liner: str
    product: str
    ideal_customer_profile: str
    strongest_use_cases: list[str] = Field(default_factory=list)
    proof_assets: list[str] = Field(default_factory=list)
    pain_keywords: list[str] = Field(default_factory=list)
    disqualifiers: list[str] = Field(default_factory=list)


class OrchestratorState(BaseModel):
    id: str = Field(default_factory=lambda: new_id("brain_"))
    created_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    founder_blurb: str
    seller: SellerProfile
    brief: FounderBrief
    knowledge: list[KnowledgeItem] = Field(default_factory=list)
    tasks: list[AgentTask] = Field(default_factory=list)

    def task_for(self, role: AgentRole) -> AgentTask | None:
        return next((t for t in self.tasks if t.role == role), None)


SCAN_EXTENSIONS = {
    ".md": "doc",
    ".mdx": "doc",
    ".txt": "doc",
    ".rst": "doc",
    ".py": "code",
    ".ts": "code",
    ".tsx": "code",
    ".js": "code",
    ".jsx": "code",
    ".json": "config",
    ".toml": "config",
    ".yaml": "config",
    ".yml": "config",
}

SKIP_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".uv-cache",
    "__pycache__",
    "node_modules",
    "dist",
    "out",
    "console/public",
}


def build_orchestrator_state(
    founder_blurb: str,
    *,
    scan_roots: list[Path] | None = None,
    slug: str = "founder",
    max_files: int = 40,
) -> OrchestratorState:
    """Create the master brain artifact from founder chat + local knowledge."""
    roots = scan_roots or [Path.cwd()]
    knowledge = [KnowledgeItem(source_path="founder_chat", kind="founder_blurb",
                               title="Founder intake", excerpt=founder_blurb)]
    for root in roots:
        knowledge.extend(scan_knowledge(root, max_files=max_files - len(knowledge)))
        if len(knowledge) >= max_files:
            break

    brief = synthesize_founder_brief(founder_blurb, knowledge)
    seller = SellerProfile(
        slug=slug,
        name=brief.company_name,
        one_liner=brief.one_liner,
        product=brief.product,
        icp=brief.ideal_customer_profile,
        pain_keywords=brief.pain_keywords[:6] or _keywords(founder_blurb),
        prototype_kind="reference_impl",
        value_prop=brief.one_liner,
    )
    state = OrchestratorState(
        founder_blurb=founder_blurb,
        seller=seller,
        brief=brief,
        knowledge=knowledge,
    )
    state.tasks = plan_agent_tasks(state)
    return state


def scan_knowledge(root: Path, *, max_files: int = 40) -> list[KnowledgeItem]:
    """Scan local founder docs/code without reading heavyweight generated dirs."""
    root = root.resolve()
    if root.is_file():
        return [_knowledge_from_file(root, root.parent)] if _allowed(root) else []

    items: list[KnowledgeItem] = []
    for path in sorted(root.rglob("*")):
        if len(items) >= max_files:
            break
        if not path.is_file() or not _allowed(path):
            continue
        if _skipped(path, root):
            continue
        item = _knowledge_from_file(path, root)
        if item:
            items.append(item)
    return items


def synthesize_founder_brief(founder_blurb: str, knowledge: list[KnowledgeItem]) -> FounderBrief:
    offline = _offline_brief(founder_blurb, knowledge)
    payload = {
        "founder_blurb": founder_blurb,
        "knowledge": [k.model_dump() for k in knowledge[:20]],
    }
    out = complete_json(
        "You are the Accumulator/Orchestrator agent for an autonomous sales "
        "engineering system. Build a factual company brief from founder input "
        "and local docs/code. Do not invent customers or integrations.\n\n"
        f"INPUT:\n{json.dumps(payload)[:12000]}",
        agent="orchestrator",
        system=(
            "Return JSON {company_name, one_liner, product, ideal_customer_profile, "
            "strongest_use_cases, proof_assets, pain_keywords, disqualifiers}. "
            "Use only provided evidence."
        ),
        offline=offline.model_dump(),
    )
    return FounderBrief(
        company_name=str(out.get("company_name") or offline.company_name),
        one_liner=str(out.get("one_liner") or offline.one_liner),
        product=str(out.get("product") or offline.product),
        ideal_customer_profile=str(
            out.get("ideal_customer_profile") or offline.ideal_customer_profile
        ),
        strongest_use_cases=list(out.get("strongest_use_cases") or offline.strongest_use_cases)[:8],
        proof_assets=list(out.get("proof_assets") or offline.proof_assets)[:8],
        pain_keywords=list(out.get("pain_keywords") or offline.pain_keywords)[:8],
        disqualifiers=list(out.get("disqualifiers") or offline.disqualifiers)[:8],
    )


def plan_agent_tasks(state: OrchestratorState) -> list[AgentTask]:
    brief = state.brief
    research = AgentTask(
        role=AgentRole.RESEARCH,
        objective=(
            f"Find companies and decision-makers matching {brief.company_name}'s ICP: "
            f"{brief.ideal_customer_profile}."
        ),
        inputs=["founder_brief", "knowledge_items", "pain_keywords"],
        expected_output="Ranked prospects with public evidence, contact hypotheses, and compliance notes.",
        success_criteria=[
            "Each prospect has at least two sourced pain signals or one very strong source.",
            "Contacts include role/person rationale; emails must come from approved enrichment providers.",
            "No private, scraped, or unsourced claims enter the ledger.",
        ],
    )
    engineer = AgentTask(
        role=AgentRole.ENGINEER,
        objective="Build a prospect-specific prototype proving the product fit.",
        inputs=["selected_prospect", "founder_brief", "relevant_docs_or_code"],
        expected_output="Deployed prototype URL plus build log, tests, and explanation of fit.",
        success_criteria=[
            "Prototype is deployed to a public URL.",
            "Prototype demonstrates a real workflow, not static marketing copy.",
            "Every personalization claim traces to prospect evidence or founder docs.",
        ],
        depends_on=[research.id],
    )
    director = AgentTask(
        role=AgentRole.DIRECTOR,
        objective="Record a Loom-style walkthrough of the deployed prototype.",
        inputs=["prototype_url", "engineer_rationale", "prospect_context"],
        expected_output="Hosted walkthrough video URL with script, captions, and recording metadata.",
        success_criteria=[
            "Playwright opens the deployed prototype and records actual interaction.",
            "Narration explains what was built and why it fits the prospect.",
            "Voice/avatar assets use owned or consented voices only.",
        ],
        depends_on=[engineer.id],
    )
    sales = AgentTask(
        role=AgentRole.SALES,
        objective="Draft a human-reviewable outbound email for the decision-maker.",
        inputs=["prospect_contact", "prototype_url", "walkthrough_url", "proof_ledger"],
        expected_output="Email draft queued in dashboard with send/suppress controls.",
        success_criteria=[
            "Email includes prototype and walkthrough links.",
            "Email cites one concrete reason for outreach.",
            "Nothing sends until a human approves it.",
        ],
        depends_on=[research.id, engineer.id, director.id],
    )
    return [research, engineer, director, sales]


def save_state(state: OrchestratorState, out_dir: Path = Path("out/orchestrator")) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{state.id}.json"
    path.write_text(json.dumps(state.model_dump(), indent=2, default=str))
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(state.model_dump(), indent=2, default=str))
    return path


def _offline_brief(founder_blurb: str, knowledge: list[KnowledgeItem]) -> FounderBrief:
    name = _company_name(founder_blurb)
    keywords = _keywords(founder_blurb)
    proof = [k.source_path for k in knowledge if k.kind in {"doc", "code", "config"}][:6]
    return FounderBrief(
        company_name=name,
        one_liner=_first_sentence(founder_blurb)[:180] or f"{name} helps teams solve painful workflows.",
        product=founder_blurb[:600],
        ideal_customer_profile="Teams with urgent operational pain that the product can prove in a prototype.",
        strongest_use_cases=[
            "Find public evidence of urgent workflow pain.",
            "Generate a small deployed artifact that proves product fit.",
            "Package the artifact into reviewable outbound.",
        ],
        proof_assets=proof,
        pain_keywords=keywords,
        disqualifiers=["No public evidence", "No clear buyer", "Cannot build a useful prototype"],
    )


def _knowledge_from_file(path: Path, root: Path) -> KnowledgeItem | None:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    kind = SCAN_EXTENSIONS.get(path.suffix.lower(), "unknown")
    return KnowledgeItem(
        source_path=rel,
        kind=kind,  # type: ignore[arg-type]
        title=path.stem.replace("-", " ").replace("_", " ").title(),
        excerpt=text[:1200],
        signals=_keywords(text)[:8],
    )


def _allowed(path: Path) -> bool:
    return path.suffix.lower() in SCAN_EXTENSIONS and path.stat().st_size <= 250_000


def _skipped(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    parts = set(rel.parts)
    return any(skip in parts or str(rel).startswith(skip + "/") for skip in SKIP_DIRS)


def _company_name(text: str) -> str:
    m = re.search(r"(?i)\b(?:we are|we sell|company is|called|startup is)\s+([A-Z][A-Za-z0-9 ._-]{1,50})", text)
    if m:
        return m.group(1).strip(" .")
    words = re.findall(r"[A-Z][A-Za-z0-9]+", text)
    return " ".join(words[:2]) if words else "Founder Startup"


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0] if text.strip() else ""


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", text.lower())
    stop = {
        "that", "this", "with", "from", "have", "will", "your", "their", "about",
        "into", "build", "built", "software", "startup", "company", "product",
        "agent", "agents", "using", "would", "should", "where", "there",
    }
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
