import re, json, httpx, time
from pathlib import Path
KEY=open("/tmp/hermes_api_key.txt").read().strip()
SYSTEM=re.search(r"const SYSTEM = `(.+?)`;", open("website/console.html").read(), re.S).group(1)
BASE="http://127.0.0.1:8642"; H={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}
LOG=Path.home()/".revenant"/"mcp_calls.log"
FORB_MCP={"find_prospects","build_campaign","setup_startup"}
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
            if e=="tool.started": tools.append(ev.get("tool","?")); print(f"    ·{time.time()-t0:.0f}s {ev.get('tool')} {str(ev.get('preview',''))[:55]}",flush=True)
            elif e=="message.delta": msg.append(ev.get("delta",""))
            elif e in ("run.completed","run.failed"): st=e; break
            if time.time()-t0>budget: st="timeout"; break
    reply="".join(msg)
    HISTORY.append({"role":"user","content":text})
    if reply.strip(): HISTORY.append({"role":"assistant","content":reply.strip()})
    del HISTORY[:-12]
    return tools,reply,st,round(time.time()-t0,1)
sid=f"chain-{int(time.time())}"; ok=True
for name,text,budget,urlpat in [
    ("FIND","My startup is Razorpay. Spin up the crew and find me 3 merchants.",90,None),
    ("BUILD","Build #1.",160,r"https://\S+pages\.dev"),
    ("FILM","Perfect — now film it.",210,r"walkthrough\S*\.mp4|https://\S+walkthrough")]:
    print(f"── {name}",flush=True); l0=_ll()
    tools,reply,st,secs=run(text,sid,budget)
    new=LOG.read_text().splitlines()[l0:] if LOG.exists() else []
    fm=[x for x in new if any(f in x for f in FORB_MCP)]
    d=len([x for x in tools if "delegate" in x])
    turn_ok = st=="run.completed" and not fm and d<=1 and (not urlpat or re.search(urlpat,reply))
    ok=ok and turn_ok
    print(f"  [{'PASS' if turn_ok else 'FAIL'}] {secs}s status={st} delegates={d} forbidden_mcp={fm}")
    print(f"  mcp_calls: {[x.split('  ',1)[-1][:45] for x in new]}")
    print(f"  reply: {reply[:220]!r}\n",flush=True)
print(f"FULL CHAIN: {'✅ PASS' if ok else '❌ FAIL'}")
