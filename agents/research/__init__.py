"""Research — Agent 1. Given an ICP + brief, produces a prospect shortlist.

Uses Linkup for web recon and simple httpx page fetches for careers/status
pages. Emails are inferred from common patterns when a paid enrichment
provider isn't wired.
"""

from .agent import Research

__all__ = ["Research"]
