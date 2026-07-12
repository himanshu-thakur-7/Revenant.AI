"""Tools available to the Sales agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ghost.llm import COST

from ..context import FounderContext
from ..tools import Tool, tool
from . import deck as deck_render
from . import hosting, queue
from .draft import DraftState


def read_tools(prospect: dict[str, Any], prototype_url: str,
               walkthrough_url: str, ctx: FounderContext | None) -> list[Tool]:

    @tool("Return the prospect brief (company, contact, pain_evidence, fit).")
    def read_prospect_brief() -> dict[str, Any]:
        return prospect

    @tool("Return the URL of the prototype Engineer deployed for this prospect.")
    def read_prototype_url() -> str:
        return prototype_url

    @tool("Return the URL of the walkthrough video Director filmed.")
    def read_walkthrough_url() -> str:
        return walkthrough_url

    @tool("Return the founder's product summary — what the startup does, its "
          "ICP, and top pitch angles. Use for email copy grounded in real "
          "product capabilities.")
    def read_founder_pitch() -> str:
        if ctx is None:
            return "[no founder context bound — use the prospect brief only]"
        return ctx.summary()

    return [read_prospect_brief, read_prototype_url,
            read_walkthrough_url, read_founder_pitch]


def action_tools(state: DraftState, *, prospect: dict[str, Any],
                 prototype_url: str, walkthrough_url: str) -> list[Tool]:

    @tool(
        "Render the pitch deck as a .pptx. Pass `title` (deck subtitle) and "
        "`slides` — a list of dicts. Each slide dict: "
        "{title: str, bullets: [str], notes?: str, kind?: 'title'|'content'|'cta'}. "
        "The first slide should be `kind='title'`, the last `kind='cta'`. "
        "Returns {pptx_path, slide_count}."
    )
    def write_pitch_deck(title: str, slides: list[dict]) -> dict[str, Any]:
        if not slides:
            return {"error": "slides list is empty"}
        if len(slides) > 10:
            return {"error": f"deck too long ({len(slides)} slides); cap at 10"}
        state.slides = list(slides)
        state.deck_title = title
        out = state.workspace / f"{state.prospect_slug}-pitch.pptx"
        try:
            deck_render.render_deck(slides, out, deck_title=title)
        except Exception as exc:
            return {"error": f"deck render failed: {exc}"}
        state.deck_pptx_path = str(out)
        return {"pptx_path": str(out), "slide_count": len(slides)}

    @tool("Deploy the rendered .pptx to Cloudflare Pages. Returns "
          "{deck_url, deployer, warning?}. Call after write_pitch_deck.")
    def deploy_deck() -> dict[str, Any]:
        if not state.deck_pptx_path:
            return {"error": "no deck rendered yet — call write_pitch_deck first"}
        result = hosting.deploy_deck(Path(state.deck_pptx_path))
        state.deck_url = result.get("deck_url") or None
        return result

    @tool(
        "Save the email draft (subject + body) plus the prospect state to the "
        "Convex review queue and a local markdown file. Pass the final "
        "subject and body — the LLM should already have composed them. "
        "Returns {convex_ok, local_path, campaign_id}."
    )
    def save_draft(subject: str, body: str) -> dict[str, Any]:
        state.email_subject = subject.strip()
        state.email_body = body.strip()

        row = queue.draft_row(
            prospect=prospect,
            email_subject=state.email_subject,
            email_body=state.email_body,
            prototype_url=prototype_url,
            walkthrough_url=walkthrough_url,
            deck_url=state.deck_url or "",
            cost_usd=round(COST.cents / 100, 4),
        )
        state.convex_id = row["id"]

        # local markdown for the founder to eyeball
        md_path = state.workspace / f"{state.prospect_slug}-email.md"
        md_path.write_text(_render_markdown(row), encoding="utf-8")
        state.email_md_path = str(md_path)

        push = queue.push_draft(row)
        result: dict[str, Any] = {
            "convex_ok": push.get("convex_ok", False),
            "local_path": str(md_path),
            "campaign_id": row["id"],
        }
        if push.get("warning"):
            result["warning"] = push["warning"]
        return result

    @tool("Finalize the sales artifact. Pass a 2-3 sentence summary of what "
          "you shipped. Call LAST.")
    def finalize_sales(summary: str) -> dict[str, Any]:
        state.finalized = True
        return {
            "prospect_slug": state.prospect_slug,
            "workspace": str(state.workspace),
            "campaign_id": state.convex_id or "",
            "email_subject": state.email_subject,
            "email_md_path": state.email_md_path or "",
            "deck_url": state.deck_url or "",
            "deck_pptx_path": state.deck_pptx_path or "",
            "prototype_url": prototype_url,
            "walkthrough_url": walkthrough_url,
            "summary": summary,
        }

    return [write_pitch_deck, deploy_deck, save_draft, finalize_sales]


def _render_markdown(row: dict[str, Any]) -> str:
    lead = row["lead"]
    return (
        f"# Draft — {lead['company_name']}\n\n"
        f"- **To:** {lead['person_name']} · {lead['person_title']}, "
        f"{lead['company_name']}\n"
        f"- **State:** {row['state']}\n"
        f"- **Prototype:** {row['microsite_url']}\n"
        f"- **Walkthrough:** {row['walkthrough_url']}\n"
        f"- **Deck:** {row['deck_url']}\n"
        f"- **Cost so far:** ${row['cost_usd']:.4f}\n\n"
        f"---\n\n"
        f"**Subject:** {row['email_subject']}\n\n"
        f"{row['email_body']}\n"
    )
