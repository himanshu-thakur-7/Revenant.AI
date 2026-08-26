import re, json, httpx, time
from pathlib import Path
KEY=open("/tmp/hermes_api_key.txt").read().strip()
SYSTEM=re.search(r"const SYSTEM = `(.+?)`;", open("website/console.html").read(), re.S).group(1)
BASE="http://127.0.0.1:8642"; H={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}
LOG=Path.home()/".revenant"/"mcp_calls.log"
HISTORY=[]
def _ll(): return len(LOG.read_text().splitlines()) if LOG.exists() else 0
def run(text,sid,budget):
    history=HISTORY[-10:]
    r=httpx.post(f"{BASE}/v1/runs",headers=H,json={"input":text,"instructions":SYSTEM,"session_id":sid,"conversation_history":history},timeout=30)
    rid=r.json()["run_id"]; tools=[]; msg=[]; st="?"; t0=time.time()
    with httpx.stream("GET",f"{BASE}/v1/runs/{rid}/events",headers=H,timeout=budget+30) as s:
        for line in s.iter_lines():
            if not line.startswith("data:"): continue
            try: ev=json.loads(line[5:].strip())
            except: continue
            e=ev.get("event","")
            if e=="tool.started": tools.append(ev.get("tool")); print(f"    ·{time.time()-t0:.0f}s {ev.get('tool')} {str(ev.get('preview',''))[:60]}",flush=True)
            elif e=="message.delta": msg.append(ev.get("delta",""))
            elif e in ("run.completed","run.failed"): st=e; break
            if time.time()-t0>budget: st="timeout"; break
    reply="".join(msg)
    HISTORY.append({"role":"user","content":text})
    if reply.strip(): HISTORY.append({"role":"assistant","content":reply.strip()})
    del HISTORY[:-12]
    return tools,reply,st,round(time.time()-t0,1)
sid=f"buildtest-{int(time.time())}"
t,reply,st,secs=run("My startup is Razorpay. Find me 3 merchants.",sid,90)
print(f"FIND [{st}] {secs}s")
m=re.search(r"1[\.\)]\s*([A-Za-z0-9 .&]+)", reply); pick1=(m.group(1).strip() if m else "?")
print(f"  shortlist #1 = {pick1!r}\n",flush=True)
l0=_ll()
t,reply,st,secs=run("Build #1.",sid,170)
new=LOG.read_text().splitlines()[l0:] if LOG.exists() else []
builds=[x.split('build_prototype',1)[-1].strip() for x in new if "build_prototype" in x]
print(f"BUILD [{st}] {secs}s")
print(f"  build_prototype calls: {len(builds)} → {builds}")
print(f"  right merchant (#1={pick1})?: {pick1.split()[0].lower() in ' '.join(builds).lower() if builds else False}")
print(f"  built once?: {len(builds)==1}")
print(f"  reply: {reply[:200]!r}")
