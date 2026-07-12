import { CSSProperties, useEffect, useMemo, useState } from "react";
import { Campaign, loadCampaigns } from "./data";

const MODE = ((import.meta as any).env?.VITE_CONVEX_URL ? "live" : "séance") as string;

export function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [sentIds, setSentIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const c = await loadCampaigns().catch(() => [] as Campaign[]);
      if (alive && c.length) {
        setCampaigns(c);
        setSelected(
          (s) => s ?? c.find((x) => x.state === "awaiting_review")?.id ?? c[0]?.id ?? null,
        );
      }
    };
    tick();
    const iv = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  const funnel = useMemo(() => {
    const counts: Record<string, number> = {};
    let cost = 0;
    for (const c of campaigns) {
      counts[c.state] = (counts[c.state] ?? 0) + 1;
      cost += c.cost_usd ?? 0;
    }
    return { counts, cost };
  }, [campaigns]);

  const active = campaigns.find((c) => c.id === selected) ?? null;

  return (
    <div style={{ maxWidth: 1220, margin: "0 auto", padding: "0 24px 80px" }}>
      <Hero funnel={funnel} total={campaigns.length} />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(300px, 380px) 1fr",
          gap: 22,
          alignItems: "start",
        }}
        className="rv-grid"
      >
        <Queue campaigns={campaigns} selected={selected} onSelect={setSelected} sentIds={sentIds} />
        <Preview
          campaign={active}
          sent={active ? sentIds.has(active.id) : false}
          onApprove={(id) => setSentIds((s) => new Set(s).add(id))}
        />
      </div>
      <Footer />
    </div>
  );
}

/* ── hero / séance banner ──────────────────────────────────── */
function Hero({
  funnel,
  total,
}: {
  funnel: { counts: Record<string, number>; cost: number };
  total: number;
}) {
  const stats: { label: string; value: string; cls?: string }[] = [
    { label: "summoned", value: String(funnel.counts["awaiting_review"] ?? 0), cls: "t-promote" },
    { label: "closed / won", value: String(funnel.counts["won"] ?? 0), cls: "" },
    { label: "warm", value: String(funnel.counts["warm_only"] ?? 0), cls: "t-warm_only" },
    { label: "laid to rest", value: String(funnel.counts["killed"] ?? 0), cls: "t-kill" },
  ];

  return (
    <header style={{ padding: "44px 0 22px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 20,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <span className="rv-candle" style={{ fontSize: 34 }}>
              🕯️
            </span>
            <h1
              className="rv-wordmark"
              style={{ fontSize: "clamp(38px, 6vw, 64px)", margin: 0, lineHeight: 0.95 }}
            >
              REVENANT
            </h1>
          </div>
          <p className="rv-eyebrow" style={{ margin: "16px 0 8px" }}>
            the autonomous outbound engineer
          </p>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 16, maxWidth: 560, lineHeight: 1.5 }}>
            It rises at{" "}
            <span style={{ color: "var(--ember)" }}>3&nbsp;AM</span>, hunts pain, ships a{" "}
            <span style={{ color: "var(--wisp)" }}>working prototype</span>, films its own walkthrough,
            and follows up while they sleep.
          </p>
        </div>
        <RitualClock />
      </div>

      {/* ritual readouts */}
      <div style={{ display: "flex", gap: 30, flexWrap: "wrap", marginTop: 30, alignItems: "flex-end" }}>
        {stats.map((s) => (
          <div key={s.label}>
            <div className={`rv-stat-num ${s.cls ?? ""}`} style={{ fontSize: 30 }}>
              {s.value}
            </div>
            <div className="rv-eyebrow" style={{ marginTop: 4 }}>
              {s.label}
            </div>
          </div>
        ))}
        <div style={{ borderLeft: "1px solid var(--line)", paddingLeft: 30 }}>
          <div className="rv-stat-num" style={{ fontSize: 30, color: "var(--wisp)" }}>
            ${funnel.cost.toFixed(2)}
          </div>
          <div className="rv-eyebrow" style={{ marginTop: 4 }}>
            spend · {total} souls
          </div>
        </div>
      </div>
      <div className="rv-signal" style={{ marginTop: 26 }} />
    </header>
  );
}

function RitualClock() {
  const [blink, setBlink] = useState(true);
  useEffect(() => {
    const iv = setInterval(() => setBlink((b) => !b), 700);
    return () => clearInterval(iv);
  }, []);
  return (
    <div className="rv-panel" style={{ padding: "14px 20px", textAlign: "right" }}>
      <div className="rv-eyebrow" style={{ marginBottom: 6 }}>
        the witching hour
      </div>
      <div className="rv-mono" style={{ fontSize: 26, color: "var(--ember)", letterSpacing: 2 }}>
        03<span style={{ opacity: blink ? 1 : 0.2 }}>:</span>00
      </div>
      <div className="rv-eyebrow" style={{ marginTop: 6, color: "var(--wisp)" }}>
        ● {MODE} mode
      </div>
    </div>
  );
}

/* ── queue ─────────────────────────────────────────────────── */
function Queue({
  campaigns,
  selected,
  onSelect,
  sentIds,
}: {
  campaigns: Campaign[];
  selected: string | null;
  onSelect: (id: string) => void;
  sentIds: Set<string>;
}) {
  return (
    <aside style={{ display: "flex", flexDirection: "column", gap: 10, position: "sticky", top: 18 }}>
      <div className="rv-eyebrow" style={{ marginBottom: 2 }}>
        the summoning queue
      </div>
      {campaigns.length === 0 && (
        <div
          className="rv-panel"
          style={{ padding: 24, textAlign: "center", color: "var(--muted)", fontSize: 14 }}
        >
          The circle is empty. Run <span className="rv-mono" style={{ color: "var(--wisp)" }}>ghost run</span>{" "}
          to summon.
        </div>
      )}
      {campaigns.map((c) => {
        const tier = c.tier ?? c.lead.score?.tier ?? "";
        const displayState = sentIds.has(c.id) ? "sent" : c.state;
        const score = c.combined_score ?? c.lead.score?.combined ?? 0;
        return (
          <div
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`rv-card spine-${c.state === "won" ? "won" : tier || c.state} ${
              selected === c.id ? "rv-active" : ""
            }`}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 15, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {c.lead.company_name}
              </span>
              <StateBadge state={displayState} />
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginTop: 6,
                fontSize: 12,
                color: "var(--muted)",
              }}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {c.lead.person_name || c.lead.company_domain}
              </span>
              {tier && (
                <span className={`rv-mono t-${tier}`}>
                  {tier} · {score.toFixed(2)}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </aside>
  );
}

function StateBadge({ state }: { state: string }) {
  const known = ["awaiting_review", "won", "sent", "warm_only", "killed"];
  const cls = known.includes(state) ? `b-${state}` : "b-default";
  return <span className={`rv-badge ${cls}`}>{state.replace(/_/g, " ")}</span>;
}

/* ── preview ───────────────────────────────────────────────── */
function Preview({
  campaign,
  sent,
  onApprove,
}: {
  campaign: Campaign | null;
  sent: boolean;
  onApprove: (id: string) => void;
}) {
  if (!campaign)
    return (
      <main
        className="rv-panel"
        style={{ padding: 60, textAlign: "center", color: "var(--muted)" }}
      >
        Select a soul from the queue to conduct the rite.
      </main>
    );

  const evidence = campaign.lead.score?.evidence ?? [];
  const canReview = campaign.state === "awaiting_review" && !sent;
  const won = campaign.state === "won";

  return (
    <main style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* the missive */}
      <section className="rv-panel" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line)", background: "var(--panel)" }}>
          <div className="rv-eyebrow">the missive · outbound draft</div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
            to <span style={{ color: "var(--ink)" }}>{campaign.lead.person_name}</span> ·{" "}
            {campaign.lead.company_name}
          </div>
          <div style={{ fontWeight: 600, marginTop: 3, fontSize: 15 }}>
            {campaign.email_subject || "—"}
          </div>
        </div>
        <pre
          style={{
            margin: 0,
            padding: "16px 20px",
            fontFamily: "'Space Grotesk', sans-serif",
            fontSize: 13.5,
            color: "#c3cbdb",
            whiteSpace: "pre-wrap",
            lineHeight: 1.65,
          }}
        >
          {campaign.email_body || "(this soul was laid to rest before outreach)"}
        </pre>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }} className="rv-two">
        {/* the apparition · live microsite */}
        <section className="rv-panel rv-panel-glow" style={{ overflow: "hidden" }}>
          <div style={sectionHead}>
            <span className="rv-eyebrow">the apparition · live microsite</span>
            {campaign.microsite_url && !campaign.microsite_url.startsWith("file:") && (
              <a className="rv-link rv-mono" style={{ fontSize: 11 }} href={campaign.microsite_url} target="_blank" rel="noreferrer">
                open ↗
              </a>
            )}
          </div>
          {campaign.microsite_html ? (
            <iframe srcDoc={campaign.microsite_html} style={frame} title="microsite" />
          ) : campaign.microsite_url && !campaign.microsite_url.startsWith("file:") ? (
            <iframe src={campaign.microsite_url} style={frame} title="microsite" />
          ) : (
            <div style={{ ...frame, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--faint)" }}>
              no apparition conjured
            </div>
          )}
        </section>

        {/* proof · evidence + artifacts */}
        <section className="rv-panel" style={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="rv-eyebrow">proof · their own words</span>
          {evidence.length === 0 && (
            <p style={{ color: "var(--faint)", fontSize: 13, margin: 0 }}>
              No proof gathered — killed or warm-only.
            </p>
          )}
          {evidence.slice(0, 4).map((e, i) => (
            <div key={i} className="rv-quote" style={{ fontSize: 12.5 }}>
              <span className="rv-mono t-promote" style={{ fontSize: 10 }}>
                [{e.source}]
              </span>{" "}
              <span style={{ color: "#c3cbdb", fontStyle: "italic" }}>"{e.excerpt}"</span>
            </div>
          ))}
          <div style={{ marginTop: "auto", paddingTop: 12, borderTop: "1px solid var(--line)", fontSize: 12, color: "var(--muted)", display: "grid", gap: 5 }}>
            <Relic icon="🎬" label="walkthrough" ready={!!campaign.walkthrough_url} />
            <Relic icon="🎙️" label="voice memo" ready={!!campaign.voice_memo_ref} />
            <Relic icon="💳" label="pilot link" ready={!!campaign.payment_link} />
            <div className="rv-mono" style={{ color: "var(--wisp)", marginTop: 2 }}>
              ${campaign.cost_usd.toFixed(2)} all-in
            </div>
          </div>
        </section>
      </div>

      {/* the rite · action bar */}
      <section style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button
          className="rv-btn rv-btn-primary"
          disabled={!canReview}
          onClick={() => onApprove(campaign.id)}
        >
          {sent ? "✓ sent — the rite is done" : "Approve & Send"}
        </button>
        <button className="rv-btn rv-btn-ghost" disabled={!canReview}>
          Edit copy
        </button>
        <button className="rv-btn rv-btn-ghost" disabled={!canReview}>
          Regenerate
        </button>
        <button className="rv-btn rv-btn-danger" disabled={!canReview} style={{ marginLeft: "auto" }}>
          Lay to rest
        </button>
      </section>

      {won && (
        <div
          className="rv-panel"
          style={{ padding: "14px 18px", borderColor: "rgba(94,242,160,0.35)", color: "var(--summon)", fontSize: 13.5 }}
        >
          💀→💚 <b>WON.</b> The prospect paid the pilot. Razorpay's webhook flipped this deal and
          Revenant pinged the human closer on Telegram.
        </div>
      )}
      {sent && !won && (
        <div style={{ color: "var(--wisp)", fontSize: 13 }}>
          Sent (DRY_RUN honored). When they pay the pilot, the Razorpay webhook flips this to{" "}
          <b>WON</b>.
        </div>
      )}
    </main>
  );
}

function Relic({ icon, label, ready }: { icon: string; label: string; ready: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between" }}>
      <span>
        {icon} {label}
      </span>
      <span className="rv-mono" style={{ color: ready ? "var(--summon)" : "var(--faint)", fontSize: 11 }}>
        {ready ? "conjured" : "—"}
      </span>
    </div>
  );
}

function Footer() {
  return (
    <footer style={{ marginTop: 44, paddingTop: 20, borderTop: "1px solid var(--line)", display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
      <span className="rv-eyebrow">human-in-the-loop · nothing sends without a click</span>
      <span className="rv-eyebrow" style={{ color: "var(--faint)" }}>
        Hermes · OpenAI · Linkup · Cloudflare · Convex · ElevenLabs · Razorpay · Wispr Flow
      </span>
    </footer>
  );
}

/* ── shared styles ─────────────────────────────────────────── */
const sectionHead: CSSProperties = {
  padding: "10px 16px",
  borderBottom: "1px solid var(--line)",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  background: "var(--panel)",
};
const frame: CSSProperties = {
  width: "100%",
  height: 300,
  border: "none",
  background: "#0a0a0f",
};
