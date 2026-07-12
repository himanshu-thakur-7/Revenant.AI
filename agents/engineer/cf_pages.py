"""Cloudflare Pages Direct Upload — via the ``wrangler`` CLI.

We use `wrangler pages deploy <dir>` because it handles the six-step
manifest / hash-check / asset-upload dance for us. Wrangler is invoked via
`npx --yes wrangler` so the caller doesn't need it pinned in a package.json —
just Node + a working ``npx`` (which the console setup already requires).

If Cloudflare isn't configured (`CLOUDFLARE_API_TOKEN` /
`CLOUDFLARE_ACCOUNT_ID` missing), the deployer falls back to a ``file://``
URL pointing at the workspace's ``index.html``. The founder can still open
that in a browser; the Director can still screen-record it later.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from ghost.config import settings


CF_PROJECT_DEFAULT = "revenant-prototypes"

# Wrangler's success line looks like:
#   ✨ Deployment complete! Take a peek over at https://<hash>.<project>.pages.dev
# The hostname can carry both dots AND hyphens (hash.project.pages.dev), so
# match a full URL up to the .pages.dev tail.
_URL_RX = re.compile(r"https://[A-Za-z0-9.\-]+\.pages\.dev")


class DeployError(RuntimeError):
    pass


def deploy_dir(workspace: Path, *, project: Optional[str] = None) -> dict[str, str]:
    """Deploy a directory to Cloudflare Pages. Never raises; returns a dict
    with ``url``, ``deployer``, and (on failure) ``warning``.

    ``deployer`` is ``"cloudflare-pages"`` on live upload, ``"file"`` on
    local fallback.
    """
    index = workspace / "index.html"
    if not index.exists():
        return {
            "url": "",
            "deployer": "none",
            "warning": "no index.html in workspace; nothing to deploy",
        }

    project = project or settings.cloudflare_pages_project or CF_PROJECT_DEFAULT
    token = settings.cloudflare_api_token
    acct = settings.cloudflare_account_id

    if not (token and acct):
        return {
            "url": index.resolve().as_uri(),
            "deployer": "file",
            "warning": ("CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID not set — "
                        "wrote file:// URL only. Add them to .env and rerun."),
        }

    try:
        url = _wrangler_deploy(workspace, project, token, acct)
    except DeployError as exc:
        return {
            "url": index.resolve().as_uri(),
            "deployer": "file",
            "warning": f"cloudflare deploy failed ({exc}); fell back to file://",
        }
    return {"url": url, "deployer": "cloudflare-pages"}


def _wrangler_deploy(workspace: Path, project: str, token: str, acct: str) -> str:
    env = {**os.environ,
           "CLOUDFLARE_API_TOKEN": token,
           "CLOUDFLARE_ACCOUNT_ID": acct}

    # As of wrangler 4.x Cloudflare no longer auto-creates the Pages project
    # on first deploy — you have to `pages project create` first. Do it here
    # unconditionally; wrangler returns a friendly "already exists" that we
    # ignore. Safer than a two-phase check-then-create dance.
    _ensure_project(project, env)

    # `--branch` is set to "main" so the URL is stable across a chain of
    # deployments to the same project. `--commit-dirty` skips wrangler's
    # dirty-git-tree warning (we intentionally deploy from an out-tree dir).
    cmd = [
        "npx", "--yes", "wrangler@latest",
        "pages", "deploy", str(workspace),
        "--project-name", project,
        "--branch", "main",
        "--commit-dirty=true",
    ]
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=180,
        )
    except FileNotFoundError as exc:
        raise DeployError(f"npx not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired:
        raise DeployError("wrangler timed out after 180s") from None

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = _URL_RX.search(combined)
    if match:
        return match.group(0)

    if proc.returncode != 0:
        tail = "\n".join(combined.strip().splitlines()[-8:])
        raise DeployError(f"wrangler exit {proc.returncode}: {tail}")

    raise DeployError("wrangler completed but no *.pages.dev URL emitted")


def _ensure_project(project: str, env: dict[str, str]) -> None:
    """Create the Pages project if it doesn't exist; no-op if it does."""
    cmd = [
        "npx", "--yes", "wrangler@latest",
        "pages", "project", "create", project,
        "--production-branch", "main",
    ]
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # let deploy try anyway; it'll surface a clearer error
    # "already exists" is fine; treat any non-zero without an explicit
    # 'exists' hint as a warning-but-continue.
    stderr = proc.stderr or ""
    if proc.returncode != 0 and "already" not in stderr.lower() and "exists" not in stderr.lower():
        # let deploy fail if this was a real error, but don't hard-stop here
        pass
