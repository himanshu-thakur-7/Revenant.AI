import { CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { Campaign, MissionEvent, convexSetState, convexUpdateDraft, loadAll } from "./data";

/* ────────────────────────────────────────────────────────────
   REVENANT · Mission Control
   The storyline, visible: five acts, a live agent feed, and a
   campaign board that moves while you watch.
   ──────────────────────────────────────────────────────────── */

const ACTS = [
  { n: 1, title: "The Human Bottleneck", sub: "the $15k/mo grind" },
  { n: 2, title: "Autonomous Recon", sub: "the detective loop" },
  { n: 3, title: "JIT Sales Engineering", sub: "it builds, not pitches" },
  { n: 4, title: "Interactive Theater", sub: "the cinematic pitch" },
  { n: 5, title: "The Conversion Loop", sub: "closing the circle" },
];

const AGENT_META: Record<string, { icon: string; cls: string }> = {
  "The Brain": { icon: "🧠", cls: "ag-brain" },
  "Human SDR": { icon: "SDR", cls: "ag-human" },
  Detective: { icon: "🔍", cls: "ag-detective" },
  "Truth Ledger": { icon: "📜", cls: "ag-ledger" },
  Gatekeeper: { icon: "⚖️", cls: "ag-gate" },
  Profiler: { icon: "🎭", cls: "ag-profiler" },
  Developer: { icon: "⌨️", cls: "ag-dev" },
  Sandbox: { icon: "🧪", cls: "ag-sandbox" },
  "Site Weaver": { icon: "🕸️", cls: "ag-weaver" },
  Copywriter: { icon: "✒️", cls: "ag-copy" },
  "Voice Director": { icon: "🎙️", cls: "ag-voice" },
  Director: { icon: "🎬", cls: "ag-director" },
  Outreach: { icon: "📨", cls: "ag-outreach" },
  Persistence: { icon: "🌒", cls: "ag-persist" },
  Closer: { icon: "🗣️", cls: "ag-closer" },
  "Human Closer": { icon: "🧑‍💼", cls: "ag-human" },
  Razorpay: { icon: "💳", cls: "ag-pay" },
};

export function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [source, setSource] = useState<string>("…");
  const [selected, setSelected] = useState<string | null>(null);
  const [sentIds, setSentIds] = useState<Set<string>>(new Set());

  // ── replay clock ──────────────────────────────────────────
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const speedRef = useRef(speed);
  speedRef.current = speed;

  useEffect(() => {
    loadAll().then(({ campaigns, events, source }) => {
      setCampaigns(campaigns);
      setEvents(events);
      setSource(source);
      setPlaying(true); // replay starts on arrival
    });
  }, []);

  // LIVE mode: while backed by Convex, poll for new events so a terminal
  // autopilot run fills this console in real time for the audience.
  useEffect(() => {
    if (source !== "convex") return;
    const iv = setInterval(async () => {
      const { campaigns: c, events: e } = await loadAll();
      setEvents((prev) => {
        const prevMax = prev.length ? Math.max(...prev.map((x) => x.at)) : -1;
        const nextMax = e.length ? Math.max(...e.map((x) => x.at)) : -1;
        if (e.length !== prev.length || nextMax !== prevMax) {
          setCampaigns(c);
          // viewer opened late or run restarted → jump near the live edge
          setElapsed((t) => {
            if (nextMax < prevMax) return 0; // fresh run reset
            return nextMax - t > 30 ? Math.max(0, nextMax - 5) : t;
          });
          setPlaying(true);
          return e;
        }
        return prev;
      });
    }, 3000);
    return () => clearInterval(iv);
  }, [source]);

  const totalTime = useMemo(
    () => (events.length ? Math.max(...events.map((e) => e.at)) + 2 : 0),
    [events],
  );

  // Wall-clock-delta accumulation: immune to background-tab timer throttling
  // (a fixed +0.1/tick clock runs 10× slow when the browser drops to 1Hz).
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    const iv = setInterval(() => {
      const now = performance.now();
      const dt = ((now - last) / 1000) * speedRef.current;
      last = now;
      setElapsed((t) => {
        const next = t + dt;
        if (next >= totalTime) setPlaying(false);
        return Math.min(next, totalTime);
      });
    }, 120);
    return () => clearInterval(iv);
  }, [playing, totalTime]);

  const visible = useMemo(() => events.filter((e) => e.at <= elapsed), [events, elapsed]);
  const done = elapsed >= totalTime && totalTime > 0;

  // live state per campaign, derived from the events seen so far
  const liveState = useMemo(() => {
    const m: Record<string, string> = {};
    for (const e of visible) {
      const s = (e.payload as any)?.state;
      if (e.campaign_id && s) m[e.campaign_id] = s;
    }
    return m;
  }, [visible]);

  // campaigns appear on the board when their first event fires
  const surfaced = useMemo(() => {
    const seen = new Set(visible.filter((e) => e.company).map((e) => e.company));
    return campaigns.filter((c) => done || seen.has(c.lead.company_name));
  }, [campaigns, visible, done]);

  const currentAct = visible.length ? visible[visible.length - 1].act : 1;
  const won = surfaced.find((c) => (liveState[c.id] ?? c.state) === "won");

  const active =
    surfaced.find((c) => c.id === selected) ??
    surfaced.find((c) => (liveState[c.id] ?? c.state) === "awaiting_review") ??
    surfaced[0] ??
    null;

  const replay = () => {
    setElapsed(0);
    setSentIds(new Set());
    setPlaying(true);
  };

  return (
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: "0 24px 70px" }}>
      <Header source={source} replay={replay} playing={playing} speed={speed} setSpeed={setSpeed}
        elapsed={elapsed} total={totalTime}
        skip={() => { setElapsed(totalTime); setPlaying(false); }} />
      <ActRail current={done ? 5 : currentAct} />
      {won && done && <WonBanner company={won.lead.company_name} />}
      <div className="rv-grid" style={{ display: "grid", gridTemplateColumns: "minmax(380px, 1fr) minmax(420px, 1.2fr)", gap: 20, marginTop: 18, alignItems: "start" }}>
        <MissionLog events={visible} playing={playing} />
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Board campaigns={surfaced} liveState={liveState} selected={active?.id ?? null}
            onSelect={setSelected} sentIds={sentIds} />
          <Detail campaign={active} liveState={liveState} sent={active ? sentIds.has(active.id) : false}
            onAmend={async (id, email_subject, email_body) => {
              setCampaigns((rows) => rows.map((c) => (
                c.id === id ? { ...c, email_subject, email_body, state: "awaiting_review" } : c
              )));
              return convexUpdateDraft(id, email_subject, email_body);
            }}
            onApprove={async (id) => {
              setSentIds((s) => new Set(s).add(id));
              await convexSetState(id, "sent");
            }} />
        </div>
      </div>
      <footer style={{ marginTop: 40, paddingTop: 18, borderTop: "1px solid var(--line)", display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <span className="rv-eyebrow">human-in-the-loop · nothing sends without a click</span>
        <span className="rv-eyebrow" style={{ color: "var(--faint)" }}>
          Hermes · OpenAI · Linkup · Cloudflare · Convex · ElevenLabs · Razorpay · Wispr Flow
        </span>
      </footer>
    </div>
  );
}

/* ── header ────────────────────────────────────────────────── */
function Header(props: {
  source: string; replay: () => void; playing: boolean;
  speed: number; setSpeed: (n: number) => void; elapsed: number; total: number;
  skip: () => void;
}) {
  const { source, replay, playing, speed, setSpeed, elapsed, total, skip } = props;
  const pct = total ? Math.min(100, (elapsed / total) * 100) : 0;
  return (
    <header style={{ padding: "30px 0 16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="rv-candle" style={{ fontSize: 26 }}>🕯️</span>
          <div>
            <h1 className="rv-wordmark" style={{ fontSize: "clamp(28px, 4vw, 44px)", margin: 0, lineHeight: 1 }}>
              REVENANT
            </h1>
            <p className="rv-eyebrow" style={{ margin: "6px 0 0" }}>
              mission control · the autonomous outbound engineer
            </p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className={`rv-badge ${source === "convex" ? "b-won" : "b-default"}`}>
            {source === "convex" ? "● convex live" : source === "ledger" ? "● local ledger" : "● replay"}
          </span>
          <span className="rv-mono" style={{ color: "var(--ember)", fontSize: 13 }}>03:00 AM</span>
          <button className="rv-btn rv-btn-ghost" style={{ padding: "7px 14px", fontSize: 13 }}
            onClick={() => setSpeed(speed === 1 ? 3 : 1)}>
            {speed}×
          </button>
          <button className="rv-btn rv-btn-ghost" style={{ padding: "7px 14px", fontSize: 13 }}
            onClick={skip} title="skip to the end of the run">
            ⏭
          </button>
          <button className="rv-btn rv-btn-primary" style={{ padding: "7px 18px", fontSize: 13 }} onClick={replay}>
            {playing ? "⟳ running…" : "▶ run the 3 AM loop"}
          </button>
        </div>
      </div>
      <div style={{ marginTop: 14, height: 2, background: "var(--line)", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: "linear-gradient(90deg, var(--wisp), var(--necro))", transition: "width .2s linear", boxShadow: "0 0 12px var(--wisp)" }} />
      </div>
    </header>
  );
}

/* ── act rail ──────────────────────────────────────────────── */
function ActRail({ current }: { current: number }) {
  return (
    <div className="rv-acts">
      {ACTS.map((a) => {
        const state = a.n < current ? "past" : a.n === current ? "now" : "later";
        return (
          <div key={a.n} className={`rv-act rv-act-${state}`}>
            <div className="rv-act-num rv-display">{["I", "II", "III", "IV", "V"][a.n - 1]}</div>
            <div>
              <div className="rv-act-title">{a.title}</div>
              <div className="rv-act-sub">{a.sub}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── mission log ───────────────────────────────────────────── */
function MissionLog({ events, playing }: { events: MissionEvent[]; playing: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  return (
    <section className="rv-panel" style={{ overflow: "hidden", position: "sticky", top: 14 }}>
      <div style={{ padding: "12px 18px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="rv-eyebrow">the mission log · agents at work</span>
        <span className="rv-mono" style={{ fontSize: 10, color: playing ? "var(--wisp)" : "var(--faint)" }}>
          {playing ? "● LIVE" : events.length ? "run complete" : "standing by"}
        </span>
      </div>
      <div ref={ref} style={{ height: "62vh", overflowY: "auto", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
        {events.length === 0 && (
          <div style={{ color: "var(--faint)", fontSize: 13, textAlign: "center", marginTop: 60 }}>
            The office is dark. The loop begins at 03:00…
          </div>
        )}
        {events.map((e) => <LogRow key={e.id} e={e} />)}
        {playing && <div className="rv-cursor rv-mono">▋</div>}
      </div>
    </section>
  );
}

function LogRow({ e }: { e: MissionEvent }) {
  const meta = AGENT_META[e.agent] ?? { icon: "◆", cls: "ag-default" };
  const isEvidence = e.kind === "evidence";
  const isAlert = e.kind === "alert" || e.kind === "payment";
  return (
    <div className={`rv-log ${meta.cls} ${isAlert ? "rv-log-alert" : ""}`}>
      <div className="rv-log-head">
        <span className="rv-log-icon">{meta.icon}</span>
        <span className="rv-log-agent rv-mono">{e.agent}</span>
        {e.company && <span className="rv-log-company">{e.company}</span>}
        <span className="rv-log-time rv-mono">t+{e.at.toFixed(0)}s · act {["I","II","III","IV","V"][e.act-1]}</span>
      </div>
      <div className={`rv-log-msg ${isEvidence ? "rv-quote" : ""}`} style={isEvidence ? { fontStyle: "italic" } : undefined}>
        {e.message}
      </div>
    </div>
  );
}

/* ── campaign board ────────────────────────────────────────── */
function Board({ campaigns, liveState, selected, onSelect, sentIds }: {
  campaigns: Campaign[]; liveState: Record<string, string>;
  selected: string | null; onSelect: (id: string) => void; sentIds: Set<string>;
}) {
  return (
    <section>
      <div className="rv-eyebrow" style={{ marginBottom: 8 }}>the board · prospects in pipeline</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
        {campaigns.length === 0 && (
          <div className="rv-panel" style={{ padding: 18, color: "var(--faint)", fontSize: 13, gridColumn: "1/-1", textAlign: "center" }}>
            No targets surfaced yet — the Detective is out there.
          </div>
        )}
        {campaigns.map((c) => {
          const st = sentIds.has(c.id) ? "sent" : liveState[c.id] ?? c.state;
          const tier = c.tier ?? c.lead.score?.tier ?? "";
          return (
            <div key={c.id} onClick={() => onSelect(c.id)}
              className={`rv-card spine-${st === "won" ? "won" : tier || st} ${selected === c.id ? "rv-active" : ""}`}
              style={{ padding: "12px 12px 12px 16px" }}>
              <div style={{ fontWeight: 600, fontSize: 13.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {c.lead.company_name}
              </div>
              <div style={{ marginTop: 6, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                <StateBadge state={st} />
                {tier && <span className={`rv-mono t-${tier}`} style={{ fontSize: 10 }}>{(c.combined_score ?? 0).toFixed(2)}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function StateBadge({ state }: { state: string }) {
  const known = ["awaiting_review", "won", "sent", "warm_only", "killed"];
  const cls = known.includes(state) ? `b-${state}` : "b-default";
  return <span className={`rv-badge ${cls}`}>{state.replace(/_/g, " ")}</span>;
}

/* ── detail / review pane ──────────────────────────────────── */
function Detail({ campaign, liveState, sent, onApprove, onAmend }: {
  campaign: Campaign | null; liveState: Record<string, string>;
  sent: boolean; onApprove: (id: string) => void;
  onAmend: (id: string, email_subject: string, email_body: string) => Promise<boolean>;
}) {
  const [tab, setTab] = useState<"missive" | "site" | "proof">("missive");
  const [editing, setEditing] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [draftSubject, setDraftSubject] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [amendStatus, setAmendStatus] = useState("");

  useEffect(() => {
    setEditing(false);
    setInstruction("");
    setDraftSubject(campaign?.email_subject ?? "");
    setDraftBody(campaign?.email_body ?? "");
    setAmendStatus("");
  }, [campaign?.id]);

  if (!campaign) return null;
  const st = sent ? "sent" : liveState[campaign.id] ?? campaign.state;
  const canReview = st === "awaiting_review";
  const evidence = campaign.lead.score?.evidence ?? [];
  const shownSubject = editing ? draftSubject : campaign.email_subject;
  const shownBody = editing ? draftBody : campaign.email_body;

  const applyAmend = async () => {
    const next = amendDraft({
      subject: draftSubject || campaign.email_subject || "",
      body: draftBody || campaign.email_body || "",
      instruction,
      company: campaign.lead.company_name,
      contact: campaign.lead.person_name,
    });
    setDraftSubject(next.subject);
    setDraftBody(next.body);
    setAmendStatus("Updating draft…");
    const persisted = await onAmend(campaign.id, next.subject, next.body);
    setAmendStatus(
      persisted
        ? "Draft updated and synced to the live console."
        : "Draft updated locally. Live persistence is unavailable in this mode.",
    );
  };

  return (
    <section className="rv-panel rv-panel-glow" style={{ overflow: "hidden" }}>
      <div style={{ display: "flex", borderBottom: "1px solid var(--line)" }}>
        {(["missive", "site", "proof"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rv-tab ${tab === t ? "rv-tab-on" : ""}`}>
            {t === "missive" ? "✉ outreach" : t === "site" ? "👁 live prototype" : "📜 proof"}
          </button>
        ))}
        <div style={{ marginLeft: "auto", padding: "8px 14px" }}><StateBadge state={st} /></div>
      </div>

      {tab === "missive" && (
        <div>
          <div style={{ padding: "12px 18px", borderBottom: "1px solid var(--line)" }}>
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              to <b style={{ color: "var(--ink)" }}>{campaign.lead.person_name}</b> · {campaign.lead.person_title}, {campaign.lead.company_name}
            </div>
            <div style={{ fontWeight: 600, marginTop: 3, fontSize: 14 }}>{shownSubject || "—"}</div>
          </div>
          <pre style={{ margin: 0, padding: "14px 18px", fontFamily: "'Space Grotesk', sans-serif", fontSize: 13, color: "#c3cbdb", whiteSpace: "pre-wrap", lineHeight: 1.6, maxHeight: 240, overflowY: "auto" }}>
            {shownBody || "(this campaign stopped before outreach)"}
          </pre>
          {editing && (
            <div style={{ padding: "12px 16px", borderTop: "1px solid var(--line)", display: "grid", gap: 10 }}>
              <input
                className="rv-input"
                value={draftSubject}
                onChange={(e) => setDraftSubject(e.target.value)}
                placeholder="Subject"
              />
              <textarea
                className="rv-input rv-textarea"
                value={draftBody}
                onChange={(e) => setDraftBody(e.target.value)}
                placeholder="Email body"
              />
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 10 }}>
                <input
                  className="rv-input"
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  placeholder='e.g. "make it more formal", "shorter", "stronger CTA"'
                />
                <button className="rv-btn rv-btn-primary" style={{ padding: "9px 14px", fontSize: 13 }}
                  disabled={!canReview} onClick={applyAmend}>
                  Apply
                </button>
              </div>
              {amendStatus && <div className="rv-mono" style={{ fontSize: 11, color: "var(--wisp)" }}>{amendStatus}</div>}
            </div>
          )}
        </div>
      )}

      {tab === "site" && (
        campaign.microsite_html
          ? <iframe srcDoc={campaign.microsite_html} style={frame} title="microsite" />
          : campaign.microsite_url && !campaign.microsite_url.startsWith("file:")
            ? <iframe src={campaign.microsite_url} style={frame} title="microsite" />
            : <div style={{ ...frame, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--faint)" }}>no prototype generated for this one</div>
      )}

      {tab === "proof" && (
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          {evidence.length === 0 && <p style={{ color: "var(--faint)", fontSize: 13, margin: 0 }}>No proof gathered — killed or warm-only.</p>}
          {evidence.map((e, i) => (
            <div key={i} className="rv-quote" style={{ fontSize: 12.5 }}>
              <span className="rv-mono t-promote" style={{ fontSize: 10 }}>[{e.source}]</span>{" "}
              <span style={{ color: "#c3cbdb", fontStyle: "italic" }}>"{e.excerpt}"</span>
            </div>
          ))}
          <div className="rv-mono" style={{ fontSize: 11, color: "var(--muted)", paddingTop: 8, borderTop: "1px solid var(--line)", display: "flex", flexWrap: "wrap", gap: 10 }}>
            {campaign.walkthrough_url && (
              <a href={campaign.walkthrough_url} target="_blank" rel="noreferrer" className="rv-link">🎬 walkthrough</a>
            )}
            {campaign.deck_url && (
              <a href={campaign.deck_url} target="_blank" rel="noreferrer" className="rv-link">📊 pitch deck</a>
            )}
            {campaign.microsite_url && (
              <a href={campaign.microsite_url} target="_blank" rel="noreferrer" className="rv-link">🕸 prototype</a>
            )}
            <span style={{ marginLeft: "auto", color: "var(--wisp)" }}>${campaign.cost_usd.toFixed(4)} all-in</span>
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 10, padding: "12px 16px", borderTop: "1px solid var(--line)", alignItems: "center" }}>
        <button className="rv-btn rv-btn-primary" style={{ padding: "9px 18px", fontSize: 13 }}
          disabled={!canReview} onClick={() => onApprove(campaign.id)}>
          {sent ? "✓ sent" : "Approve & Send"}
        </button>
        <button className="rv-btn rv-btn-ghost" style={{ padding: "9px 14px", fontSize: 13 }}
          disabled={!canReview}
          onClick={() => {
            setDraftSubject(campaign.email_subject || "");
            setDraftBody(campaign.email_body || "");
            setEditing((v) => !v);
            setTab("missive");
          }}>
          {editing ? "Close editor" : "Edit"}
        </button>
        <button className="rv-btn rv-btn-danger" style={{ padding: "9px 14px", fontSize: 13, marginLeft: "auto" }} disabled={!canReview}>Suppress</button>
      </div>
    </section>
  );
}

function amendDraft(input: {
  subject: string;
  body: string;
  instruction: string;
  company: string;
  contact: string;
}): { subject: string; body: string } {
  const instruction = input.instruction.trim().toLowerCase();
  let subject = input.subject.trim();
  let body = input.body.trim();

  if (!instruction) return { subject, body };

  if (/\bformal|professional|polished|exec|executive\b/.test(instruction)) {
    subject = subject
      .replace(/\bnot a pitch\b/gi, "a tailored prototype for review")
      .replace(/\bfor you\b/gi, `for ${input.company}`);

    body = body
      .replace(/^Hi\b/m, "Hello")
      .replace(/\bI've\b/g, "I have")
      .replace(/\bit's\b/g, "it is")
      .replace(/\bI'd\b/g, "I would")
      .replace(/\bthat's\b/g, "that is")
      .replace(/\bSo instead of pitching,\s*/g, "")
      .replace(/\bto poke at\b/g, "to review")
      .replace(/Reply "yes" and I'll send a slot\./g, "If useful, I would be glad to share a few available times.")
      .replace(/\nHimanshu\s*$/g, "\n\nRegards,\nHimanshu");

    if (!/^Hello\b/m.test(body)) {
      body = `Hello ${input.contact || "there"},\n\n${body}`;
    }
    if (!/Regards,\nHimanshu$/.test(body)) {
      body = body
        .replace(/\n+Himanshu\s*$/g, "\n\nRegards,\nHimanshu")
        .replace(/\n+[—-]\s*The .+ team\s*$/i, "\n\nRegards,\nHimanshu");
    }
    if (!/Regards,\nHimanshu$/.test(body)) {
      body = `${body}\n\nRegards,\nHimanshu`;
    }
  }

  if (/\bshort|shorter|concise|tighten\b/.test(instruction)) {
    const lines = body.split("\n").filter((line) => line.trim());
    body = lines.slice(0, 8).join("\n\n");
  }

  if (/\bcta|call to action|stronger ask\b/.test(instruction)) {
    body = body.replace(
      /(?:15 minutes this week[\s\S]*?)(?:\n\nRegards,|\n\nHimanshu|$)/,
      "Would you be open to a 15-minute walkthrough this week to review the prototype against a real workflow?\n\nRegards,",
    );
  }

  return { subject, body };
}

function WonBanner({ company }: { company: string }) {
  return (
    <div className="rv-won-banner">
      <span style={{ fontSize: 16, fontWeight: 800, letterSpacing: 0 }}>WON</span>
      <div>
        <b>{company} — WON.</b>{" "}
        <span style={{ color: "var(--muted)" }}>
          Found at 3 AM · engineered by breakfast · pilot paid via Razorpay · human closer sealed it.
        </span>
      </div>
    </div>
  );
}

const frame: CSSProperties = { width: "100%", height: 320, border: "none", background: "#0a0a0f", display: "block" };
