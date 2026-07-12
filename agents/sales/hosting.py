"""Host the finished pitch deck (`.pptx`) on Cloudflare Pages.

Same pattern as Director's walkthrough hosting — separate CF Pages project
(``revenant-decks``) so slugs don't collide with prototypes or walkthroughs.
Each deploy gets a per-hash preview URL AND updates the production URL.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


PROJECT = "revenant-decks"
_URL_RX = re.compile(r"https://[A-Za-z0-9.\-]+\.pages\.dev")


class DeckHostError(RuntimeError):
    pass


def deploy_deck(pptx_path: Path) -> dict[str, str]:
    """Deploy the workspace containing the deck to Cloudflare Pages.

    Returns ``{base_url, deck_url, deployer, warning?}``. Never raises —
    falls back to ``file://`` when Cloudflare isn't configured.
    """
    from ghost.config import settings

    if not pptx_path.exists():
        return {"base_url": "", "deck_url": "", "deployer": "none",
                "warning": f"deck missing at {pptx_path}"}

    token = settings.cloudflare_api_token
    acct = settings.cloudflare_account_id
    if not (token and acct):
        return {
            "base_url": "",
            "deck_url": pptx_path.resolve().as_uri(),
            "deployer": "file",
            "warning": ("CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID not set — "
                        "deck saved locally only."),
        }

    workspace = pptx_path.parent
    try:
        base = _wrangler_deploy(workspace, PROJECT, token, acct)
    except DeckHostError as exc:
        return {
            "base_url": "",
            "deck_url": pptx_path.resolve().as_uri(),
            "deployer": "file",
            "warning": f"CF Pages deploy failed ({exc}); fell back to file://",
        }
    return {"base_url": base, "deck_url": f"{base}/{pptx_path.name}",
            "deployer": "cloudflare-pages"}


def _wrangler_deploy(workspace: Path, project: str, token: str, acct: str) -> str:
    env = {**os.environ,
           "CLOUDFLARE_API_TOKEN": token,
           "CLOUDFLARE_ACCOUNT_ID": acct}
    _ensure_project(project, env)
    cmd = ["npx", "--yes", "wrangler@latest",
           "pages", "deploy", str(workspace),
           "--project-name", project,
           "--branch", "main",
           "--commit-dirty=true"]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=180)
    except FileNotFoundError as exc:
        raise DeckHostError(f"npx not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired:
        raise DeckHostError("wrangler timed out after 180s") from None

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = _URL_RX.search(combined)
    if match:
        return match.group(0)
    tail = "\n".join(combined.strip().splitlines()[-8:])
    raise DeckHostError(f"wrangler exit {proc.returncode}: {tail}")


def _ensure_project(project: str, env: dict[str, str]) -> None:
    cmd = ["npx", "--yes", "wrangler@latest",
           "pages", "project", "create", project,
           "--production-branch", "main"]
    try:
        subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
