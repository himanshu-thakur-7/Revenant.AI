#!/usr/bin/env python3
"""Publish pipeline output into the console's static assets for the offline demo.

Copies ``out/ledger.json`` and ``out/sites/*`` into ``console/public/`` and
rewrites ``file://`` microsite URLs to relative ``/sites/...`` paths so the
console's iframe can load them from the dev server. In live mode the console
reads Convex directly and this script is unnecessary — it exists purely so the
whole thing is demoable with zero cloud dependencies.

    python scripts/sync_console.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
PUBLIC = ROOT / "console" / "public"


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    ledger_src = OUT / "ledger.json"
    if not ledger_src.exists():
        raise SystemExit("no out/ledger.json — run `ghost run` first")

    snap = json.loads(ledger_src.read_text())

    # copy sites/walkthroughs/voice and rewrite URLs to relative paths
    sites_src = OUT / "sites"
    sites_dst = PUBLIC / "sites"
    if sites_dst.exists():
        shutil.rmtree(sites_dst)
    if sites_src.exists():
        shutil.copytree(sites_src, sites_dst)

    walkthroughs_src = OUT / "walkthroughs"
    walkthroughs_dst = PUBLIC / "walkthroughs"
    if walkthroughs_dst.exists():
        shutil.rmtree(walkthroughs_dst)
    if walkthroughs_src.exists():
        shutil.copytree(walkthroughs_src, walkthroughs_dst)

    voice_src = OUT / "voice"
    voice_dst = PUBLIC / "voice"
    if voice_dst.exists():
        shutil.rmtree(voice_dst)
    if voice_src.exists():
        shutil.copytree(voice_src, voice_dst)

    for camp in snap.get("campaigns", []):
        url = camp.get("microsite_url", "")
        if url.startswith("file://") and "/sites/" in url:
            rel = "/sites/" + url.split("/sites/", 1)[1]
            camp["microsite_url"] = rel
        walk = camp.get("walkthrough_url", "")
        if walk.startswith("file://") and "/walkthroughs/" in walk:
            rel = "/walkthroughs/" + walk.split("/walkthroughs/", 1)[1]
            camp["walkthrough_url"] = rel
        voice = camp.get("voice_memo_ref", "")
        if "/voice/" in voice:
            camp["voice_memo_ref"] = "/voice/" + voice.split("/voice/", 1)[1]
        html = camp.get("microsite_html", "")
        if html:
            root = OUT.resolve().as_uri()
            html = html.replace(f"{root}/walkthroughs/", "/walkthroughs/")
            html = html.replace(str(OUT.resolve() / "voice") + "/", "/voice/")
            camp["microsite_html"] = html

    (PUBLIC / "ledger.json").write_text(json.dumps(snap, indent=2))
    n = len(snap.get("campaigns", []))
    print(f"synced {n} campaigns + sites → console/public/")


if __name__ == "__main__":
    main()
