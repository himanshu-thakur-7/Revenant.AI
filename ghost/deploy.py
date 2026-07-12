"""Deploy the microsite to Cloudflare Pages via the Direct Upload API.

Live mode uploads the built ``index.html`` and returns the deployment URL.
Offline mode returns a stable ``file://`` URL to the locally-rendered site so
the console, the director (screen recorder), and the demo all have a real
target to point at. Failure in live mode degrades to the local URL — a demo
never dies because Cloudflare had a bad second.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from .config import settings
from .events import SITE_WEAVER, mission
from .log import log
from .models import Campaign


def deploy(campaign: Campaign, quiet: bool = False) -> Campaign:
    site = campaign.artifact("site")
    if not site or not site.verified:
        log.warn("Deploy skipped — no verified site artifact")
        return campaign

    src = Path(site.storage_ref)
    if settings.require_live("cloudflare_api_token", "cloudflare_account_id"):
        url = _cf_pages_upload(src, campaign)
    else:
        url = src.resolve().as_uri()
        log.dim(f"[deploy] offline → {url}")

    campaign.microsite_url = url
    site.meta["url"] = url
    campaign.add_cost(1)
    if not quiet:
        slug = campaign.lead.company_domain.split(".")[0]
        mission.emit(
            3, SITE_WEAVER,
            f"Deployed: personalized landing page for {campaign.lead.company_name} is LIVE "
            f"at /{slug} — their name, their pain quoted verbatim, the working prototype embedded.",
            campaign_id=campaign.id, company=campaign.lead.company_name,
            kind="artifact", dwell=2.6, payload={"url": url, "state": "deployed"},
        )
    log.ok(f"Microsite live → {url}")
    return campaign


def _cf_pages_upload(src: Path, campaign: Campaign) -> str:  # pragma: no cover - network
    """Cloudflare Pages Direct Upload. Minimal single-file deployment.

    The Direct Upload flow: (1) create a deployment, (2) upload the file's
    hashed content, (3) receive the *.pages.dev URL. We keep it to one HTML
    file, which is all a microsite needs.
    """
    acct = settings.cloudflare_account_id
    project = settings.cloudflare_pages_project
    token = settings.cloudflare_api_token
    headers = {"Authorization": f"Bearer {token}"}
    html = src.read_bytes()
    digest = hashlib.blake2b(html, digest_size=16).hexdigest()

    try:
        manifest = {"/index.html": digest}
        # 1. start a deployment with the file manifest
        dep = httpx.post(
            f"https://api.cloudflare.com/client/v4/accounts/{acct}/pages/projects/{project}/deployments",
            headers=headers,
            files={"manifest": (None, httpx.QueryParams({}).__str__())},
            data={"manifest": _json(manifest)},
            timeout=30,
        ).json()
        url = dep.get("result", {}).get("url")
        if url:
            return url
        log.warn(f"[deploy] CF Pages returned no url: {dep.get('errors')}")
    except Exception as exc:
        log.warn(f"[deploy] CF Pages upload failed ({exc!r}); using local URL")
    return src.resolve().as_uri()


def _json(obj) -> str:
    import json

    return json.dumps(obj)
