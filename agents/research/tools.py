"""Tools available to the Research agent.

Two categories:
* **World tools** — web_search, fetch_page, extract_pain_signals, guess_emails
* **Shortlist tools** — add_prospect, list_prospects, finalize_shortlist —
  they mutate the agent's own in-memory shortlist so the final answer is
  structured, not a re-formatted prose blob.
"""

from __future__ import annotations

from typing import Any

from ghost.config import settings
from ghost.llm import complete_json

from ..tools import Tool, tool
from . import apollo, linkup, web
from .email_guess import guess as guess_emails_impl


class ShortlistState:
    """One instance per Research run; scopes the accumulated prospects."""

    def __init__(self) -> None:
        self.prospects: list[dict[str, Any]] = []
        self.finalized: bool = False

    def add(self, p: dict[str, Any]) -> int:
        self.prospects.append(p)
        return len(self.prospects)

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.prospects)


# ── coercion helpers ───────────────────────────────────────────
# Nous Hermes occasionally sends fields declared as list[str] / list[dict]
# as bare strings / bare dicts. These normalise the shape so we never index
# characters or unpack a dict as a list of pairs.
def _as_str_list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None and str(x).strip()]
    if isinstance(v, str):
        # A single email string, or a comma-separated string.
        parts = [p.strip() for p in v.split(",") if p.strip()]
        return parts or [v]
    return [str(v)]


def _as_dict_list(v: Any) -> list[dict[str, str]]:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def world_tools() -> list[Tool]:
    """Tools that talk to the outside world — no state."""

    # Per-instance memory: the same tool call closure is reused across the
    # agent's whole run so a repeated query returns a hard-stop error instead
    # of another API hit that costs money and wastes an iteration.
    seen_queries: set[str] = set()
    seen_urls: set[str] = set()

    @tool("Search the web via Linkup. Use for finding companies that leak a pain "
          "signal — jobs, status pages, engineering blogs. Returns a list of "
          "{name, url, snippet}. Pick 2-3 promising results and fetch_page each. "
          "DO NOT re-issue an identical query — same input gives the same "
          "results. If nothing useful came back, change the query wording, the "
          "vertical, or the signal type before searching again.")
    def web_search(query: str, max_results: int = 6) -> list[dict[str, str]]:
        norm = " ".join(query.lower().split())
        if norm in seen_queries:
            return [{
                "error": "duplicate_query",
                "note": (f"You already searched '{query}'. Re-issuing gives "
                         "the same results. PIVOT: change the wording, the "
                         "vertical, or the pain signal you're looking for. "
                         "Or call fetch_page on a URL you already have."),
            }]
        seen_queries.add(norm)
        try:
            return linkup.search(query, max_results=max_results)
        except RuntimeError as exc:
            return [{"error": str(exc)}]

    @tool("Fetch a web page → cleaned plain text. Use to VERIFY a search snippet "
          "on the actual page — never quote something you only saw in a snippet. "
          "Duplicate URL fetches within one run are rejected.")
    def fetch_page(url: str) -> dict[str, str]:
        if url in seen_urls:
            return {"url": url,
                    "error": "duplicate_url",
                    "note": "you already fetched this URL; use the result above"}
        seen_urls.add(url)
        return web.fetch(url)

    @tool("Given a page's plain text and the seller's product framing, extract "
          "structured pain signals: {problem, verbatim_excerpt, severity_0_1}. "
          "Use this only after fetch_page — feed it the ACTUAL page text.")
    def extract_pain_signals(page_text: str, product_framing: str) -> dict[str, Any]:
        prompt = (
            "Read the page text below. It came from a prospect's public site. "
            f"Our product framing:\n{product_framing}\n\n"
            "Extract ONLY signals that a decision-maker at this company has publicly "
            "confirmed a matching pain. For each signal:\n"
            "- problem: 1-line description of the pain in the prospect's own terms\n"
            "- verbatim_excerpt: a quote from the page (≤ 30 words) that grounds it\n"
            "- severity: 0.0 to 1.0 — how directly the excerpt matches OUR product's fit\n\n"
            "If nothing on the page qualifies, return an empty signals list. Do NOT invent.\n\n"
            f"PAGE TEXT:\n{page_text[:12000]}"
        )
        return complete_json(
            prompt,
            agent="research.extract",
            system="You are an evidence-only extractor. If unsure, exclude.",
            offline={"signals": []},
        )

    @tool("Generate candidate email addresses for (first_name, last_name, "
          "company_domain). Returns ranked patterns — not verified sends. "
          "FALLBACK ONLY: prefer find_contact (Apollo, verified emails) "
          "when it's configured.")
    def guess_emails(first_name: str, last_name: str, company_domain: str) -> list[dict[str, str]]:
        return guess_emails_impl(first_name, last_name, company_domain)

    @tool("Find the best decision-maker at a company via Apollo.io — returns "
          "{name, title, linkedin_url, email, email_verified, alternates}. "
          "Spends at most ONE email-reveal credit per call, so call it once "
          "per company, only for prospects you intend to add. THE preferred "
          "way to get a real contact — use before guess_emails.")
    def find_contact(company_domain: str) -> dict:
        if not settings.apollo_api_key:
            return {"error": "Apollo not configured (APOLLO_API_KEY missing) — "
                             "fall back to guess_emails."}
        try:
            return apollo.find_best_contact(company_domain)
        except apollo.ApolloError as exc:
            return {"error": str(exc)}

    return [web_search, fetch_page, extract_pain_signals, guess_emails, find_contact]


def shortlist_tools(state: ShortlistState) -> list[Tool]:
    """Tools that mutate the shortlist. Bound to a per-run state."""

    @tool("Add ONE prospect to the shortlist. Every field must be grounded in a "
          "page you actually fetched. `pain_evidence` is a list of "
          "{source_url, excerpt}. `fit_score` is 0.0-1.0. Returns the new count.")
    def add_prospect(
        company_name: str,
        company_domain: str,
        industry: str,
        person_name: str,
        person_title: str,
        pain_evidence: list[dict[str, str]],
        fit_score: float,
        fit_rationale: str,
        email_candidates: list[str] | None = None,
        linkedin_url: str | None = None,
    ) -> str:
        # Nous sometimes sends list fields as bare strings/dicts. Coerce.
        emails_list = _as_str_list(email_candidates)
        evidence_list = _as_dict_list(pain_evidence)
        row = {
            "company_name": company_name,
            "company_domain": company_domain,
            "industry": industry,
            "contact": {
                "name": person_name,
                "title": person_title,
                "email_candidates": emails_list,
                "linkedin_url": linkedin_url,
            } if person_name else None,
            "pain_evidence": evidence_list,
            "fit_score": max(0.0, min(1.0, float(fit_score))),
            "fit_rationale": fit_rationale,
        }
        count = state.add(row)
        return f"added: {company_name} (#{count})"

    @tool("Show the current shortlist. Useful before finalizing to review "
          "what you have.")
    def list_prospects() -> list[dict[str, Any]]:
        return state.snapshot()

    @tool("Freeze the shortlist and return it as the final answer. Call this "
          "exactly once when you are done. After calling this, stop adding.")
    def finalize_shortlist(summary: str) -> dict[str, Any]:
        state.finalized = True
        return {
            "summary": summary,
            "count": len(state.prospects),
            "prospects": state.snapshot(),
        }

    return [add_prospect, list_prospects, finalize_shortlist]
