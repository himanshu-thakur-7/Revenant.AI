"""Fast smoke test for the main_test Razorpay Route split demo.

No Telegram, GitHub, Cloudflare, or real sleeps. This verifies the branch can:
- arm only on the trigger repo,
- shortlist Rigi/Convosight/Coto,
- hit the deterministic Rigi artifact path, and
- accept the manual /demo_pr rehearsal command after setup.
"""

from __future__ import annotations

import time

from agents import demo_razorpay, demo_razorpay_split
from agents.runner import build_campaign_for, find_shortlist
from agents.telegram.bot import RevenantBot


class FakeAPI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, _chat_id: int, text: str, **_kwargs):
        self.messages.append(text)
        return {"ok": True, "result": {"message_id": len(self.messages)}}

    def send_chat_action(self, *_args, **_kwargs):
        return {"ok": True}


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

    bot = RevenantBot("dummy-token", ctx)
    fake = FakeAPI()
    bot.api = fake
    sess = bot.session(8135896882)
    sess.ctx = ctx
    sess.ctx_label = "razorpay.com + github.com/razorpayInc/Razorpay"
    sess.setup_done = True
    bot._on_command(8135896882, "/demo_pr feat(route): Marketplace Payout Splits v1")

    # /demo_pr runs in a daemon thread. Wait for the first ack, not the full
    # staged shortlist. This keeps the smoke test under a second.
    deadline = time.time() + 2
    while time.time() < deadline and not fake.messages:
        time.sleep(0.02)
    assert any("PR #47 just merged" in msg for msg in fake.messages)
    assert any("Marketplace Payout Splits" in msg for msg in fake.messages)

    print("split demo smoke ok")


if __name__ == "__main__":
    main()
