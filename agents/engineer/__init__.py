"""Engineer — Agent 2. Reads the founder's code, ships a per-prospect prototype.

Given a prospect brief from Research + the founder's context, the Engineer:

1. Studies the founder's product (docs, patterns, brand cues) via founder tools.
2. Reasons about the sharpest pain-fit angle for this prospect.
3. Writes a self-contained single-page HTML prototype tailored to that
   company (their name, their industry's sample data, three fit-bullets).
4. Deploys the prototype to Cloudflare Pages (or `file://` locally).
5. Returns the deployment URL + a one-paragraph build summary.
"""

from .agent import Engineer

__all__ = ["Engineer"]
