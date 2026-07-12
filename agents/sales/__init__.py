"""Sales — Agent 4. Drafts the outbound artifact stack: email + pitch deck.

Given a prospect + prototype URL + walkthrough URL, Sales composes a
personalised email, builds a short pitch deck (`.pptx`), deploys the deck
to Cloudflare Pages, and writes the whole draft to the Convex review queue
(and to a local file). A human clicks Send in the console.
"""

from .agent import Sales

__all__ = ["Sales"]
