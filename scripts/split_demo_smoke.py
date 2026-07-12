"""Fast smoke test for the main_test Razorpay Route split demo.

No Telegram, GitHub, Cloudflare, or real sleeps. This verifies the branch can:
- arm only on the trigger repo,
- shortlist Rigi/Convosight/Coto,
- hit the deterministic Rigi artifact path, and
- fire a watcher callback when a merged PR appears.
"""

from __future__ import annotations

import time

from agents import demo_razorpay, demo_razorpay_split
from agents.runner import build_campaign_for, find_shortlist
from agents.telegram.pr_watcher import MergedPR, PRWatcher


def _no_sleep(_seconds: float) -> None:
    return None


def main() -> None:
    demo_razorpay_split.deactivate()
    assert demo_razorpay_split.matches_trigger_repo(
        "razorpay.com github.com/razorpayInc/Razorpay")
    assert not demo_razorpay_split.matches_trigger_repo("razorpay.com")

    ctx = demo_razorpay.razorpay_context()
    demo_razorpay_split.activate()
    shortlist = find_shortlist("PR-triggered split demo", ctx, want=3)
    assert [p["company_name"] for p in shortlist] == ["Rigi", "Convosight", "Coto"]

    original_staged_build = demo_razorpay_split.run_staged_build
    try:
        demo_razorpay_split.run_staged_build = (
            lambda on_stage: original_staged_build(on_stage, sleep=_no_sleep)
        )
        art = build_campaign_for(shortlist[0], ctx, on_stage=lambda *_: None)
    finally:
        demo_razorpay_split.run_staged_build = original_staged_build
    assert art.ok
    assert art.company == "Rigi"
    assert art.prototype_url == demo_razorpay_split.RIGI_PROTOTYPE_URL
    assert art.walkthrough_url == demo_razorpay_split.RIGI_WALKTHROUGH_URL
    assert demo_razorpay_split.RIGI_WALKTHROUGH_MP4.exists()

    fired: list[MergedPR] = []
    watcher = PRWatcher(
        "razorpayInc/Razorpay",
        on_merge=lambda pr: fired.append(pr),
        poll_seconds=60,
    )
    watcher._fetch_once = lambda: [MergedPR(
        number=47,
        title="feat(route): Marketplace Payout Splits v1",
        body="dummy",
        merged_at="2026-07-12T09:00:00Z",
        author="demo",
        html_url="https://github.com/razorpayInc/Razorpay/pull/47",
    )]
    watcher.start()
    deadline = time.time() + 2
    while time.time() < deadline and not fired:
        time.sleep(0.02)
    watcher.stop()
    assert len(fired) == 1
    assert fired[0].title == "feat(route): Marketplace Payout Splits v1"

    print("split demo smoke ok")


if __name__ == "__main__":
    main()
