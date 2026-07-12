"""Tools the Orchestrator can call.

Two families:
1. **Context tools** — read the founder's startup (files, search).
2. **Delegation tools** — spawn Research / Engineer / Director / Sales sub-agents.

The delegation tools take the prospect as a JSON *string* (``prospect_json``)
rather than a dict/object parameter. This dodges a real Nous Hermes-4 quirk:
when a tool schema declares a nested-object argument, Nous sometimes falls
back to emitting the whole call as raw ``<tool_call>{…}</tool_call>`` text
instead of using the native ``tool_calls`` mechanism — which the harness
then treats as a final answer and the pipeline stalls. Strings are safe.
"""

from __future__ import annotations

import json
from typing import Any

from ..context import FounderContext
from ..tools import Tool, tool


def context_tools(ctx: FounderContext) -> list[Tool]:
    """Bind the founder-context reader tools to a specific context instance."""

    @tool("List files ingested from the founder's startup. "
          "Use before reading — the paths are exactly what the founder wrote.")
    def list_startup_files(pattern: str = "") -> list[str]:
        paths = ctx.paths()
        if pattern:
            pl = pattern.lower()
            paths = [p for p in paths if pl in p.lower()]
        return paths[:200]

    @tool("Read one file from the founder's startup. Path is relative to the "
          "repo root, e.g. 'README.md' or 'src/api/router.py'. Returns the full "
          "text, or an error if not found.")
    def read_startup_file(path: str) -> str:
        body = ctx.read(path)
        if body is None:
            return f"[not-found] {path} is not in the ingested file set."
        return body[:20_000]

    @tool("Regex-search across every ingested file. Returns matching lines with "
          "path and line number. Use for 'where do we do X' questions.")
    def search_startup_context(pattern: str) -> list[dict[str, str]]:
        return ctx.search(pattern)

    @tool("Print a summary of the founder's startup — the same briefing you were "
          "handed at boot. Useful if you want to re-read your own context.")
    def show_startup_briefing() -> str:
        return ctx.summary()

    return [list_startup_files, read_startup_file, search_startup_context, show_startup_briefing]


def _parse_prospect(prospect_json: Any) -> dict | str:
    """Best-effort parse of the ``prospect_json`` argument. Returns a dict on
    success, or an error string that the tool relays to the LLM."""
    if isinstance(prospect_json, dict):
        return prospect_json
    if not isinstance(prospect_json, str):
        return f"prospect_json must be a JSON string, got {type(prospect_json).__name__}"
    s = prospect_json.strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        return f"prospect_json is not valid JSON: {exc.msg} at pos {exc.pos}"
    if isinstance(data, dict):
        return data
    # Nous double-encoding: it may have wrapped a JSON object in another string.
    if isinstance(data, str):
        try:
            inner = json.loads(data)
            if isinstance(inner, dict):
                return inner
        except json.JSONDecodeError:
            pass
    return f"prospect_json parsed to {type(data).__name__}, expected an object"


def delegation_stubs() -> list[Tool]:
    """The four sub-agent delegation tools — all live, not stubs."""

    @tool("Delegate to the Research agent — finds fit companies and decision-makers "
          "for a given ideal customer profile. Pass a *briefing* describing the ICP, "
          "the pain signals to look for, and how many prospects you want (default 3). "
          "Returns a JSON shortlist. Uses the founder's own startup context as the "
          "product framing — do NOT re-describe the product in the brief.")
    def spawn_research_agent(brief: str, max_prospects: int = 3) -> str:
        from ..base import current_sink
        from ..research import Research

        briefing = f"{brief.strip()}\n\nTarget shortlist size: {max_prospects}."
        r = Research()
        result = r.run_brief(briefing, on_event=current_sink())
        return json.dumps(result, default=str)

    @tool("Delegate to the Engineer agent — reads the founder's docs/code, picks the "
          "sharpest pain-fit angle, and builds + deploys a working single-page "
          "prototype tailored to this prospect. Pass `prospect_json` — the ENTIRE "
          "prospect object from Research's shortlist as a JSON string, e.g. "
          "'{\"company_name\":\"Acme\",\"company_domain\":\"acme.com\",...}'. "
          "Returns {url, workspace, files, summary}.")
    def spawn_engineer_agent(prospect_json: str) -> str:
        from ..base import current_founder_ctx, current_sink
        from ..engineer import Engineer

        prospect = _parse_prospect(prospect_json)
        if isinstance(prospect, str):
            return json.dumps({"error": prospect})

        ctx = current_founder_ctx()
        if ctx is None:
            return json.dumps({
                "error": ("no founder context loaded; attach one with "
                          "/context before delegating to Engineer.")
            })

        eng = Engineer(founder_context=ctx, prospect=prospect)
        result = eng.build(on_event=current_sink())
        return json.dumps(result, default=str)

    @tool("Delegate to the Director agent — films a Loom-style walkthrough of the "
          "already-deployed prototype with an AI voiceover, uploads to Cloudflare "
          "Pages, returns a video URL. Pass the `prototype_url` (from Engineer's "
          "result) and `prospect_json` — the same prospect object as a JSON string. "
          "Returns {video_url, mp4_path, duration_s, ...}.")
    def spawn_director_agent(prototype_url: str, prospect_json: str) -> str:
        from ..base import current_sink
        from ..director import Director

        prospect = _parse_prospect(prospect_json)
        if isinstance(prospect, str):
            return json.dumps({"error": prospect})

        d = Director(prototype_url=prototype_url, prospect=prospect)
        result = d.film(on_event=current_sink())
        return json.dumps(result, default=str)

    @tool("Delegate to the Sales agent — drafts a personalised outbound email + "
          "renders a 5-6 slide pitch deck (.pptx) tailored to this prospect, "
          "deploys the deck to Cloudflare Pages, and writes the whole draft to "
          "the Convex review queue. Pass `prospect_json` (the prospect object "
          "as a JSON string), `prototype_url` from Engineer, and `walkthrough_url` "
          "from Director. Returns {email_subject, deck_url, campaign_id, ...}.")
    def spawn_sales_agent(prospect_json: str, prototype_url: str,
                          walkthrough_url: str) -> str:
        from ..base import current_founder_ctx, current_sink
        from ..sales import Sales

        prospect = _parse_prospect(prospect_json)
        if isinstance(prospect, str):
            return json.dumps({"error": prospect})

        s = Sales(
            founder_context=current_founder_ctx(),
            prospect=prospect,
            prototype_url=prototype_url,
            walkthrough_url=walkthrough_url,
        )
        result = s.draft(on_event=current_sink())
        return json.dumps(result, default=str)

    return [
        spawn_research_agent,
        spawn_engineer_agent,
        spawn_director_agent,
        spawn_sales_agent,
    ]
