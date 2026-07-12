"""Host the finished walkthrough MP4 on Cloudflare Pages.

Reuses the same ``wrangler pages deploy`` pattern the Engineer uses for
prototypes — a separate project (``revenant-walkthroughs``) keeps videos and
sites cleanly separated. Each walkthrough becomes a distinct deployment,
producing a unique preview URL ``<hash>.revenant-walkthroughs.pages.dev``
plus updating the production URL.

The returned URL for the MP4 is ``{deployment_url}/walkthrough.mp4`` so the
Sales agent can drop it into an email as either a direct link or a native
HTML5 ``<video>`` embed.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from ghost.config import settings


PROJECT = "revenant-walkthroughs"
_URL_RX = re.compile(r"https://[A-Za-z0-9.\-]+\.pages\.dev")


class HostError(RuntimeError):
    pass


def deploy_walkthrough(mp4_path: Path) -> dict[str, str]:
    """Deploy the folder containing the final MP4 to Cloudflare Pages. Returns
    ``{base_url, mp4_url, deployer, warning?}``. Never raises — falls back to
    a local ``file://`` URL if Cloudflare isn't configured or the deploy
    fails."""
    if not mp4_path.exists():
        return {
            "base_url": "",
            "mp4_url": "",
            "deployer": "none",
            "warning": f"MP4 not found at {mp4_path}",
        }

    token = settings.cloudflare_api_token
    acct = settings.cloudflare_account_id
    if not (token and acct):
        file_url = mp4_path.resolve().as_uri()
        return {
            "base_url": "",
            "mp4_url": file_url,
            "deployer": "file",
            "warning": ("CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID not set — "
                        "walkthrough saved locally only."),
        }

    # Deploy the parent workspace of the MP4 as-is. Sibling files (audio, WebM,
    # etc.) get deployed too, which is fine — CF Pages ignores unreferenced
    # assets from the browser's perspective; they just sit at path URLs.
    workspace = mp4_path.parent
    try:
        base = _wrangler_deploy(workspace, PROJECT, token, acct)
    except HostError as exc:
        return {
            "base_url": "",
            "mp4_url": mp4_path.resolve().as_uri(),
            "deployer": "file",
            "warning": f"CF Pages deploy failed ({exc}); fell back to file://",
        }
    return {
        "base_url": base,
        "mp4_url": f"{base}/{mp4_path.name}",
        "deployer": "cloudflare-pages",
    }


def _wrangler_deploy(workspace: Path, project: str, token: str, acct: str) -> str:
    env = {**os.environ,
           "CLOUDFLARE_API_TOKEN": token,
           "CLOUDFLARE_ACCOUNT_ID": acct}
    _ensure_project(project, env)

    cmd = [
        "npx", "--yes", "wrangler@latest",
        "pages", "deploy", str(workspace),
        "--project-name", project,
        "--branch", "main",
        "--commit-dirty=true",
    ]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=240)
    except FileNotFoundError as exc:
        raise HostError(f"npx not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired:
        raise HostError("wrangler timed out after 240s") from None

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = _URL_RX.search(combined)
    if match:
        return match.group(0)
    tail = "\n".join(combined.strip().splitlines()[-8:])
    raise HostError(f"wrangler exit {proc.returncode}: {tail}")


def _ensure_project(project: str, env: dict[str, str]) -> None:
    cmd = ["npx", "--yes", "wrangler@latest",
           "pages", "project", "create", project,
           "--production-branch", "main"]
    try:
        subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # deploy step will surface a clearer error
