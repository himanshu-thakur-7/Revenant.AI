"""Tools available to the Engineer agent.

Three families:
* **Founder tools** — read the founder's own repo (docs, patterns, brand cues).
* **Prospect tool** — read the brief passed in from Research.
* **Workspace tools** — write / list prototype files, deploy, finalize.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..context import FounderContext
from ..tools import Tool, tool
from . import cf_pages
from .prototype import PrototypeState


def founder_tools(ctx: FounderContext) -> list[Tool]:
    """Read-only tools scoped to the founder's ingested context."""

    @tool("List files in the founder's repo (README, docs, source). "
          "Optional case-insensitive substring filter.")
    def list_founder_files(pattern: str = "") -> list[str]:
        paths = ctx.paths()
        if pattern:
            p = pattern.lower()
            paths = [x for x in paths if p in x.lower()]
        return paths[:200]

    @tool("Read one file from the founder's repo. Path is relative to the "
          "repo root. Returns the full text (capped at 20k chars).")
    def read_founder_file(path: str) -> str:
        body = ctx.read(path)
        if body is None:
            return f"[not-found] {path} is not in the founder's repo."
        return body[:20_000]

    @tool("Regex-search the founder's repo for lines matching a pattern. "
          "Returns file+line+text matches. Use to locate the exact function "
          "or regex you want to mirror in the prototype.")
    def search_founder_context(pattern: str) -> list[dict[str, str]]:
        return ctx.search(pattern)

    return [list_founder_files, read_founder_file, search_founder_context]


def prospect_tool(prospect: dict[str, Any]) -> list[Tool]:
    """Expose the prospect brief as a callable — the LLM should call it once
    at the start of its loop."""

    @tool("Return the prospect brief: company_name, company_domain, industry, "
          "contact (name/title/emails), pain_evidence excerpts, and the fit "
          "rationale. Call this at the start of every run.")
    def read_prospect_brief() -> dict[str, Any]:
        return prospect

    return [read_prospect_brief]


def _prospect_clues(prospect: dict[str, Any] | None) -> list[str]:
    if not prospect:
        return []
    chunks: list[str] = []
    for key in ("company_name", "company_domain", "industry", "fit_rationale"):
        if prospect.get(key):
            chunks.append(str(prospect[key]))
    contact = prospect.get("contact") or {}
    for key in ("title", "name"):
        if contact.get(key):
            chunks.append(str(contact[key]))
    for ev in prospect.get("pain_evidence") or []:
        if ev.get("excerpt"):
            chunks.append(str(ev["excerpt"]))

    clues: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9&+-]{3,}", chunk.lower()):
            if raw in {
                "with", "from", "this", "that", "their", "your", "they",
                "them", "company", "startup", "prospect", "merchant",
                "business", "solution", "platform", "customer", "customers",
            }:
                continue
            if raw not in seen:
                seen.add(raw)
                clues.append(raw)
            if len(clues) >= 28:
                return clues
    return clues


def _specificity_warning(content: str, prospect: dict[str, Any] | None) -> str:
    clues = _prospect_clues(prospect)
    if not clues:
        return ""
    lower = content.lower()
    hits = [c for c in clues if c in lower]
    company = str((prospect or {}).get("company_name") or "").strip()
    company_hits = lower.count(company.lower()) if company else 0
    has_demo = all(
        re.search(rf"id\s*=\s*['\"]{name}['\"]", lower)
        for name in ("demoinput", "demooutput")
    )
    if len(set(hits)) >= 8 and company_hits >= 4 and has_demo:
        return ""
    missing = [c for c in clues if c not in set(hits)][:8]
    return (
        " WARNING: this prototype may still be too generic. Before deploying, "
        "rewrite it to visibly include more prospect-specific clues from the "
        f"brief/homepage. Missing examples: {', '.join(missing) or 'merchant workflow data'}. "
        "Every major section, #demoInput, and #demoOutput should use this "
        "prospect's categories/workflows/KPIs, not generic copy."
    )


def workspace_tools(state: PrototypeState, prospect: dict[str, Any] | None = None) -> list[Tool]:
    """Write prototype files, list them, deploy, finalize. Scoped per-run."""

    @tool("Write one file into the prototype workspace. `filename` must be a "
          "simple relative name (e.g. 'index.html'). Returns the on-disk path "
          "the file was written to.")
    def write_prototype_file(filename: str, content: str) -> str:
        try:
            path = state.write(filename, content)
        except ValueError as exc:
            return f"[error] {exc}"
        warning = ""
        if filename.lower().endswith((".html", ".htm")):
            warning = _specificity_warning(content, prospect)
        return f"wrote {path} ({len(content)} chars){warning}"

    @tool("List files currently in the prototype workspace.")
    def list_prototype_files() -> list[str]:
        return state.paths()

    @tool("Deploy the workspace to Cloudflare Pages (falls back to a file:// URL "
          "if CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID aren't configured). "
          "Returns {url, deployer, warning?}. Call ONCE, after index.html is "
          "written.")
    def deploy_prototype() -> dict[str, str]:
        result = cf_pages.deploy_dir(state.workspace)
        state.deployment_url = result.get("url") or None
        state.deployer = result.get("deployer") or None
        return result

    @tool("Finalize the build. Pass a 2-3 sentence summary of what you shipped "
          "and why the prospect will care. Call this LAST — the founder reads "
          "this as your final answer.")
    def finalize_prototype(summary: str) -> dict[str, Any]:
        state.finalized = True
        return {
            "prospect_slug": state.prospect_slug,
            "workspace": str(state.workspace),
            "url": state.deployment_url or "",
            "deployer": state.deployer or "none",
            "files": state.paths(),
            "summary": summary,
        }

    return [write_prototype_file, list_prototype_files, deploy_prototype, finalize_prototype]
