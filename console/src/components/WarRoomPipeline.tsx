import { useEffect, useRef, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Campaign, MissionEvent, loadAll } from "../data";

const STAGES = [
  { id: "recon", label: "Reconnaissance" },
  { id: "sandbox", label: "Engineering Sandbox" },
  { id: "voice", label: "Voice Synthesis" },
  { id: "delivered", label: "Payload Delivered" },
];

const STAGE_AGENTS: Record<string, number> = {
  Detective: 0, "Truth Ledger": 0, Gatekeeper: 0,
  Profiler: 1, Developer: 1, Sandbox: 1, "Site Weaver": 1,
  Copywriter: 2, "Voice Director": 2, Director: 2,
  Outreach: 3, Persistence: 3, Closer: 3, "Human Closer": 3, Razorpay: 3,
};

const AGENT_CLR: Record<string, string> = {
  Detective: "#52e0c4", "Truth Ledger": "#b9c6dd", Gatekeeper: "#f5a623",
  Profiler: "#b98cff", Developer: "#5ef2a0", Sandbox: "#9df25e",
  "Site Weaver": "#5aa9ff", Copywriter: "#e0aaff", "Voice Director": "#ff9ecb",
  Director: "#ffd166", Outreach: "#7cc0ff", Persistence: "#8a93ab",
  Closer: "#ff9e6d", "Human Closer": "#ffffff", Razorpay: "#5ef2a0",
};

function ts(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `03:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export function WarRoomPipeline() {
  const nav = useNavigate();
  const [camps, setCamps] = useState<Campaign[]>([]);
  const [evts, setEvts] = useState<MissionEvent[]>([]);
  const [src, setSrc] = useState("…");
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const sRef = useRef(speed);
  sRef.current = speed;
  const termRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadAll().then(({ campaigns, events, source }) => {
      setCamps(campaigns);
      setEvts(events);
      setSrc(source);
      setPlaying(true);
    });
  }, []);

  const total = useMemo(
    () => (evts.length ? Math.max(...evts.map((e) => e.at)) + 2 : 0),
    [evts],
  );

  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    const iv = setInterval(() => {
      const now = performance.now();
      const dt = ((now - last) / 1000) * sRef.current;
      last = now;
      setElapsed((t) => {
        const next = t + dt;
        if (next >= total) setPlaying(false);
        return Math.min(next, total);
      });
    }, 80);
    return () => clearInterval(iv);
  }, [playing, total]);

  const vis = useMemo(() => evts.filter((e) => e.at <= elapsed), [evts, elapsed]);

  const target = useMemo(
    () =>
      camps.find((c) => c.tier === "promote") ??
      camps.find((c) => c.state === "awaiting_review" || c.state === "won") ??
      camps[0] ?? null,
    [camps],
  );

  const curStage = useMemo(() => {
    if (!vis.length) return -1;
    let mx = -1;
    for (const e of vis) {
      const s = STAGE_AGENTS[e.agent];
      if (s !== undefined && s > mx) mx = s;
    }
    return mx;
  }, [vis]);

  const liveState = useMemo(() => {
    if (!target) return "";
    for (let i = vis.length - 1; i >= 0; i--) {
      const s = (vis[i].payload as any)?.state;
      if (vis[i].campaign_id === target.id && s) return s as string;
    }
    return target.state;
  }, [vis, target]);

  const won = liveState === "won";
  const done = elapsed >= total && total > 0;

  useEffect(() => {
    termRef.current?.scrollTo({ top: termRef.current.scrollHeight, behavior: "smooth" });
  }, [vis.length]);

  const replay = () => { setElapsed(0); setPlaying(true); };
  const skip = () => { setElapsed(total); setPlaying(false); };
  const pct = total ? Math.min(100, (elapsed / total) * 100) : 0;

  return (
    <div className="wr-root">
      {/* ── header ─────────────────────────────── */}
      <header className="wr-hdr">
        <div className="wr-hdr-l">
          <h1 className="rv-wordmark" style={{ fontSize: 30, margin: 0, lineHeight: 1 }}>REVENANT</h1>
          <span className="rv-mono wr-sub">autonomous outbound engineer</span>
        </div>
        <div className="wr-hdr-r">
          <span className={`rv-badge ${src === "convex" ? "b-won" : "b-default"}`}>
            {src === "convex" ? "● LIVE" : "● REPLAY"}
          </span>
          <span className="rv-mono wr-clock">{ts(elapsed)}</span>
          <button className="wr-btn rv-mono" onClick={() => setSpeed(speed >= 10 ? 1 : speed >= 3 ? 10 : 3)}>{speed}×</button>
          <button className="wr-btn" onClick={skip} title="skip to end">⏭</button>
          <button className="wr-run rv-mono" onClick={replay}>{playing ? "◉ RUNNING" : "▶ INITIATE"}</button>
        </div>
      </header>

      {/* progress sweep */}
      <div className="wr-prog"><div className="wr-prog-fill" style={{ width: `${pct}%` }} /></div>

      {/* ── pipeline ───────────────────────────── */}
      <div className="wr-pipe">
        {STAGES.map((st, i) => {
          const state = i < curStage ? "done" : i === curStage ? "on" : "off";
          return (
            <div key={st.id} className="wr-step">
              <div className={`wr-node wr-node-${state}`}>
                {state === "done" ? "✓" : state === "on" ? <span className="wr-pulse" /> : null}
              </div>
              <span className={`wr-label rv-mono wr-label-${state}`}>{st.label}</span>
              {i < STAGES.length - 1 && (
                <div className={`wr-conn ${i < curStage ? "wr-conn-lit" : ""}`} />
              )}
            </div>
          );
        })}
      </div>

      {/* ── target card ────────────────────────── */}
      {target && (
        <div className={`wr-tgt ${won ? "wr-tgt-won" : ""}`} onClick={() => nav(`/pitch/${target.id}`)}>
          <div className="wr-tgt-top">
            <span className="rv-eyebrow">{won ? "◆ DEAL WON" : "◎ TARGET ACQUIRED"}</span>
            <span className={`rv-badge ${won ? "b-won" : "b-awaiting_review"}`}>
              {won ? "WON" : (target.tier ?? liveState).toUpperCase()}
            </span>
          </div>
          <div className="wr-tgt-name">{target.lead.company_name}</div>
          <div className="rv-mono wr-tgt-meta">
            {target.lead.person_name} · {target.lead.person_title}
          </div>
          <span className="rv-mono wr-tgt-link">View pitch →</span>
        </div>
      )}

      {/* ── terminal ───────────────────────────── */}
      <div className="wr-term">
        <div className="wr-term-chrome">
          <div className="wr-dots"><i /><i /><i /></div>
          <span className="rv-mono wr-term-title">revenant@ghost:~/mission</span>
          <span className="rv-mono wr-term-cnt">{vis.length}</span>
        </div>
        <div className="wr-term-body" ref={termRef}>
          {vis.length === 0 && (
            <pre className="wr-idle rv-mono">
              {"$ ghost run --seller echodesk --limit 3\n\n  Waiting for mission clock…"}
            </pre>
          )}
          {vis.map((e) => (
            <div key={e.id} className={`wr-ln ${e.kind === "alert" || e.kind === "payment" ? "wr-ln-hi" : ""}`}>
              <span className="wr-ln-t">[{ts(e.at)}]</span>
              <span className="wr-ln-a" style={{ color: AGENT_CLR[e.agent] ?? "#7b869c" }}>
                {e.agent}
              </span>
              <span className="wr-ln-s">›</span>
              <span className="wr-ln-m">{e.message}</span>
            </div>
          ))}
          {playing && <span className="wr-blink rv-mono">▋</span>}
        </div>
      </div>

      {/* ── won banner ─────────────────────────── */}
      {won && done && (
        <div className="wr-won">
          <span style={{ fontSize: 20 }}>◆</span>
          <div>
            <strong>{target?.lead.company_name} — WON.</strong>{" "}
            <span style={{ color: "var(--muted)" }}>
              Found at 3 AM · engineered by breakfast · pilot paid via Razorpay.
            </span>
          </div>
        </div>
      )}

      <footer className="wr-foot rv-mono">
        Hermes · OpenAI · Linkup · Cloudflare · Convex · ElevenLabs · Razorpay · Wispr Flow
      </footer>
    </div>
  );
}
