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
PROTOTYPE_URL = "https://816d6f43.revenant-prototypes.pages.dev"


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
                    f"{str(ev.get('preview', ''))[:60]}",
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


sid = f"filmctx-{int(time.time())}"
history = [
    {
        "role": "user",
        "content": "Build for #3",
    },
    {
        "role": "assistant",
        "content": (
            "The Engineer built and deployed Coursera's Razorpay prototype:\n\n"
            f"{PROTOTYPE_URL}\n\n"
            "Want the Director to film an AI walkthrough video of it? Say \"film it.\""
        ),
    },
    {
        "role": "assistant",
        "content": (
            "CONSOLE STATE: Last completed build is for Coursera selling Razorpay. "
            f"Prototype URL: {PROTOTYPE_URL}. If the founder says \"film it\", "
            "use this URL; do not ask them to paste it."
        ),
    },
]

print("-- FILM CONTEXT: prior assistant message contains prototype URL", flush=True)
l0 = _loglen()
tools, reply, status, secs = run("film it", sid, history, 210)
new = LOG.read_text().splitlines()[l0:] if LOG.exists() else []
films = [x for x in new if "film_walkthrough" in x]
asked = bool(re.search(r"send me|paste|prototype url", reply, re.I)) and not films
ok = (
    status == "run.completed"
    and len([x for x in tools if "delegate" in x]) == 1
    and len(films) == 1
    and PROTOTYPE_URL.replace("https://", "")[:8] in films[0]
    and re.search(r"https://\S+walkthrough\S*|walkthrough\.mp4", reply)
    and not asked
)
print(f"  [{'PASS' if ok else 'FAIL'}] {secs}s status={status}")
print(f"  film_walkthrough calls: {films}")
print(f"  reply: {reply[:240]!r}")
raise SystemExit(0 if ok else 1)
