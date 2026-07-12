import json
import re
import time
from pathlib import Path

import httpx

KEY = open("/tmp/hermes_api_key.txt").read().strip()
SYSTEM = re.search(
    r"const SYSTEM = `(.+?)`;",
    open("website/console.html").read(),
    re.S,
).group(1)
BASE = "http://127.0.0.1:8642"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
LOG = Path.home() / ".revenant" / "mcp_calls.log"
PROTOTYPE_URL = "https://example.revenant-prototypes.pages.dev"
WALKTHROUGH_URL = "https://example.revenant-walkthroughs.pages.dev/walkthrough.mp4"


def _loglen():
    return len(LOG.read_text().splitlines()) if LOG.exists() else 0


def run(text, sid, history, budget):
    r = httpx.post(
        f"{BASE}/v1/runs",
        headers=H,
        json={
            "input": text,
            "instructions": SYSTEM,
            "session_id": sid,
            "conversation_history": history,
        },
        timeout=30,
    )
    r.raise_for_status()
    rid = r.json()["run_id"]
    tools, msg, status = [], [], "?"
    t0 = time.time()
    with httpx.stream(
        "GET", f"{BASE}/v1/runs/{rid}/events", headers=H, timeout=budget + 30
    ) as s:
        for line in s.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            et = ev.get("event", "")
            if et == "tool.started":
                tools.append(ev.get("tool", "?"))
                print(
                    f"    *{time.time() - t0:.0f}s {ev.get('tool')} "
                    f"{str(ev.get('preview', ''))[:65]}",
                    flush=True,
                )
            elif et == "message.delta":
                msg.append(ev.get("delta", ""))
            elif et in ("run.completed", "run.failed"):
                status = et
                break
            if time.time() - t0 > budget:
                status = "timeout"
                break
    return tools, "".join(msg), status, round(time.time() - t0, 1)


sid = f"salesctx-{int(time.time())}"
history = [
    {
        "role": "user",
        "content": (
            "My startup is Shroud. It helps engineering teams ship secure "
            "redaction and compliance workflows for AI apps."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "CONSOLE STATE: Last completed build is for HubSpot selling Shroud. "
            f"Prototype URL: {PROTOTYPE_URL}. Pain: High-volume CRM teams need "
            "safer AI-assisted support handoffs without leaking customer data. "
            "If the founder says \"draft outreach\", use this URL."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "CONSOLE STATE: Last completed walkthrough is for HubSpot selling "
            f"Shroud. Walkthrough URL: {WALKTHROUGH_URL}. If the founder says "
            "\"draft outreach\", use this video URL."
        ),
    },
]

print("-- SALES CONTEXT: non-Razorpay startup + remembered artifacts", flush=True)
l0 = _loglen()
tools, reply, status, secs = run("Draft the outreach email.", sid, history, 180)
new = LOG.read_text().splitlines()[l0:] if LOG.exists() else []
sales = [x for x in new if "draft_outreach" in x]
forbidden = [x for x in new if any(name in x for name in ["build_campaign", "find_prospects", "setup_startup"])]
ok = (
    status == "run.completed"
    and len([x for x in tools if "delegate" in x]) == 1
    and len(sales) == 1
    and "Shroud -> HubSpot" in sales[0]
    and not forbidden
    and "Subject:" in reply
    and "Email draft:" in reply
    and "Razorpay" not in reply
)
print(f"  [{'PASS' if ok else 'FAIL'}] {secs}s status={status}")
print(f"  draft_outreach calls: {sales}")
print(f"  forbidden calls: {forbidden}")
print(f"  reply: {reply[:300]!r}")
raise SystemExit(0 if ok else 1)
