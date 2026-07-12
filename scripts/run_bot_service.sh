#!/bin/bash
# Launchd wrapper for the standalone deterministic Revenant Telegram bot.
#
# WHY THIS EXISTS: the Hermes gateway drove @bot_revenant_bot via an LLM
# router (SOUL.md) that kept mis-routing the founder's messages (setup→find,
# build→find) and leaking raw HTML. The standalone bot has DETERMINISTIC
# routing we control + real inline callback buttons (no LLM guessing, no
# context loss), so it's the demo-safe driver. Runs as a background service
# so it's not "in a terminal".
#
# The Hermes gateway MUST be stopped first (both can't poll the same bot
# token — 409 conflict):  hermes gateway stop
#
# Manage this service:
#   launchctl load   ~/Library/LaunchAgents/ai.revenant.bot.plist
#   launchctl unload ~/Library/LaunchAgents/ai.revenant.bot.plist
#   tail -f ~/Revenant.AI/out/bot-service.log

set -euo pipefail
cd "$HOME/Revenant.AI"

export REVENANT_MODE=live
# Lip-sync ON so the walkthrough shows the Sana avatar (each run ~1 D-ID
# credit). Set to 1 to save credits when iterating (falls back to the
# static bubble).
export DIRECTOR_SKIP_LIPSYNC=0
export PYTHONUNBUFFERED=1
# Prefer Homebrew Node 22+ on PATH so wrangler 4 works if ever needed
# (deploy code pins wrangler@3, which is fine on Node 18 too).
export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:$PATH"

exec "$HOME/Revenant.AI/.venv/bin/python" -m agents.cli telegram --repo "$HOME/shroud"
