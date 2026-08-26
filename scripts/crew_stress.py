import re, json, httpx, time, sys
from pathlib import Path
KEY=open("/tmp/hermes_api_key.txt").read().strip()
SYSTEM=re.search(r"const SYSTEM = `(.+?)`;", open("website/console.html").read(), re.S).group(1)
BASE="http://127.0.0.1:8642"; H={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}
LOG=Path.home()/".revenant"/"mcp_calls.log"
FORBIDDEN_STREAM={"terminal","shell","execute_code","bash","web_extract","session_search","browser","curl"}
FORBIDDEN_MCP={"find_prospects","build_campaign","setup_startup"}
HISTORY=[]
def _loglen(): return len(LOG.read_text().splitlines()) if LOG.exists() else 0
def run(text, sid, budget):
    history=HISTORY[-10:]
    r=httpx.post(f"{BASE}/v1/runs",headers=H,json={"input":text,"instructions":SYSTEM,"session_id":sid,"conversation_history":history},timeout=30)
    rid=r.json()["run_id"]; tools=[]; msg=[]; status="?"; t0=time.time()
    with httpx.stream("GET",f"{BASE}/v1/runs/{rid}/events",headers=H,timeout=budget+30) as s:
        for line in s.iter_lines():
            if not line.startswith("data:"): continue
            try: ev=json.loads(line[5:].strip())
            except: continue
            et=ev.get("event","")
            if et=="tool.started": tools.append(ev.get("tool","?")); print(f"    ·{time.time()-t0:.0f}s {ev.get('tool')}",flush=True)
            elif et=="message.delta": msg.append(ev.get("delta",""))
            elif et in ("run.completed","run.failed"): status=et; break
            if time.time()-t0>budget: status="timeout"; break
    reply="".join(msg)
    HISTORY.append({"role":"user","content":text})
    if reply.strip(): HISTORY.append({"role":"assistant","content":reply.strip()})
    del HISTORY[:-12]
    return {"tools":tools,"reply":reply,"status":status,"secs":round(time.time()-t0,1)}
t=int(time.time()); sc=sys.argv[1]
S={"verify":("is https://razorpay-magic-demo.pages.dev up? go check it",40),
   "build_all":("build all three at once",70),
   "find":("My startup is Razorpay. Find me 3 merchants to win.",90)}
text,budget=S[sc]
print(f"── {sc}: {text!r}",flush=True)
l0=_loglen(); res=run(text,f"stress-{sc}-{t}",budget); new=LOG.read_text().splitlines()[l0:] if LOG.exists() else []
forb_s=[x for x in res["tools"] if any(f in x for f in FORBIDDEN_STREAM)]
forb_m=[x for x in new if any(f in x for f in FORBIDDEN_MCP)]
deleg=len([x for x in res["tools"] if "delegate" in x])
ok = res["status"]=="run.completed" and not forb_s and not forb_m and deleg<=1
print(f"  [{'PASS' if ok else 'FAIL'}] {res['secs']}s status={res['status']} delegates={deleg}")
if forb_s: print(f"  ✗ forbidden stream tools: {forb_s}")
if forb_m: print(f"  ✗ forbidden MCP calls: {forb_m}")
print(f"  new mcp_calls: {[x.split('  ',1)[-1][:40] for x in new]}")
print(f"  reply: {res['reply'][:180]!r}")
