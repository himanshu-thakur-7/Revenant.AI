"""Telegram gateway — the founder commands Revenant from their phone.

The founder dictates a targeting brief in a Telegram chat. The bot runs the
full agent fleet, streams progress, then delivers the walkthrough video, the
prototype link, the pitch deck, and the email draft — with inline
Approve / Amend / Discard buttons. Approve sends via Resend (DRY_RUN aware);
Amend re-drafts from a natural-language change.
"""

from .bot import RevenantBot

__all__ = ["RevenantBot"]
