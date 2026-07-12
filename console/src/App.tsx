import { useEffect, useMemo, useState } from "react";
import { Campaign, loadCampaigns, STATE_BADGE, TIER_COLOR } from "./data";

export function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [sentIds, setSentIds] = useState<Set<string>>(new Set());

  // Poll for realtime-ish updates (Convex live queries in prod; ledger.json here).
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const c = await loadCampaigns().catch(() => []);
      if (alive && c.length) {
        setCampaigns(c);
        setSelected((s) => s ?? c.find((x) => x.state === "awaiting_review")?.id ?? c[0]?.id ?? null);
      }
    };
    tick();
    const iv = setInterval(tick, 2500);
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
    return { counts, cost, total: campaigns.length };
  }, [campaigns]);

  const active = campaigns.find((c) => c.id === selected) ?? null;

  return (
    <div className="min-h-screen text-slate-100 font-sans">
      <Header funnel={funnel} />
      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6 px-6 pb-16">
        <Queue campaigns={campaigns} selected={selected} onSelect={setSelected} sentIds={sentIds} />
        <Preview
          campaign={active}
          sent={active ? sentIds.has(active.id) : false}
          onApprove={(id) => setSentIds((s) => new Set(s).add(id))}
        />
      </div>
    </div>
  );
}

function Header({ funnel }: { funnel: { counts: Record<string, number>; cost: number; total: number } }) {
  const order = ["awaiting_review", "warm_only", "killed", "sent", "won"];
  return (
    <header className="border-b border-white/10 mb-6">
      <div className="max-w-7xl mx-auto px-6 py-5 flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl">🕯️</span>
          <div>
            <h1 className="text-lg font-bold tracking-tight">Revenant · Review Console</h1>
            <p className="text-xs text-slate-500">human-in-the-loop · nothing sends without a click</p>
          </div>
        </div>
        <div className="flex items-center gap-5 text-sm">
          {order
            .filter((s) => funnel.counts[s])
            .map((s) => (
              <div key={s} className="text-center">
                <div className="text-xl font-bold">{funnel.counts[s]}</div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500">{s.replace("_", " ")}</div>
              </div>
            ))}
          <div className="text-center pl-4 border-l border-white/10">
            <div className="text-xl font-bold text-indigo-400">${funnel.cost.toFixed(2)}</div>
            <div className="text-[10px] uppercase tracking-wider text-slate-500">spend / {funnel.total} leads</div>
          </div>
        </div>
      </div>
    </header>
  );
}

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
    <aside className="space-y-2">
      <h2 className="text-xs uppercase tracking-widest text-slate-500 mb-2">Queue</h2>
      {campaigns.length === 0 && (
        <div className="text-slate-500 text-sm border border-dashed border-white/10 rounded-lg p-6 text-center">
          No campaigns yet. Run <code className="text-indigo-400">ghost run</code> and refresh.
        </div>
      )}
      {campaigns.map((c) => {
        const tier = c.tier ?? c.lead.score?.tier ?? "";
        const isSent = sentIds.has(c.id);
        return (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`w-full text-left rounded-lg border p-3 transition ${
              selected === c.id
                ? "border-indigo-500/60 bg-indigo-500/10"
                : "border-white/10 bg-white/[0.02] hover:bg-white/5"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium truncate">{c.lead.company_name}</span>
              <StateBadge state={isSent ? "sent" : c.state} />
            </div>
            <div className="flex items-center justify-between mt-1 text-xs text-slate-500">
              <span className="truncate">{c.lead.person_name || c.lead.company_domain}</span>
              {tier && (
                <span className={TIER_COLOR[tier] ?? "text-slate-400"}>
                  {tier} · {(c.combined_score ?? c.lead.score?.combined ?? 0).toFixed(2)}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </aside>
  );
}

function StateBadge({ state }: { state: string }) {
  const cls = STATE_BADGE[state] ?? "bg-slate-500/15 text-slate-400 border-slate-500/30";
  return <span className={`text-[10px] px-2 py-0.5 rounded-full border ${cls}`}>{state.replace("_", " ")}</span>;
}

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
      <main className="border border-white/10 rounded-xl p-10 text-center text-slate-500">
        Select a campaign to review.
      </main>
    );

  const evidence = campaign.lead.score?.evidence ?? [];
  const canReview = campaign.state === "awaiting_review" && !sent;

  return (
    <main className="space-y-5">
      {/* email preview */}
      <section className="border border-white/10 rounded-xl overflow-hidden">
        <div className="bg-white/5 px-5 py-3 border-b border-white/10">
          <div className="text-xs text-slate-500">To: {campaign.lead.person_name} · {campaign.lead.company_name}</div>
          <div className="font-semibold mt-0.5">{campaign.email_subject || "—"}</div>
        </div>
        <pre className="px-5 py-4 text-sm text-slate-300 whitespace-pre-wrap font-sans leading-relaxed">
          {campaign.email_body || "(no body — this lead didn't reach outreach)"}
        </pre>
      </section>

      <div className="grid md:grid-cols-2 gap-5">
        {/* microsite iframe */}
        <section className="border border-white/10 rounded-xl overflow-hidden">
          <div className="bg-white/5 px-4 py-2 text-xs text-slate-500 flex justify-between">
            <span>Live microsite</span>
            {campaign.microsite_url && (
              <a className="text-indigo-400" href={campaign.microsite_url} target="_blank" rel="noreferrer">
                open ↗
              </a>
            )}
          </div>
          {campaign.microsite_url ? (
            <iframe src={campaign.microsite_url} className="w-full h-72 bg-white" title="microsite" />
          ) : (
            <div className="h-72 flex items-center justify-center text-slate-600 text-sm">no microsite</div>
          )}
        </section>

        {/* evidence + artifacts */}
        <section className="border border-white/10 rounded-xl p-4 space-y-3">
          <h3 className="text-xs uppercase tracking-widest text-slate-500">Evidence (verbatim)</h3>
          {evidence.length === 0 && <p className="text-slate-600 text-sm">No evidence — killed or warm-only.</p>}
          {evidence.map((e, i) => (
            <div key={i} className="text-sm">
              <span className="text-indigo-400 text-xs">[{e.source}]</span>{" "}
              <span className="text-slate-300 italic">"{e.excerpt}"</span>
            </div>
          ))}
          <div className="pt-2 border-t border-white/10 text-xs text-slate-500 space-y-1">
            <div>🎬 walkthrough: {campaign.walkthrough_url ? "ready" : "—"}</div>
            <div>🎙️ voice memo: {campaign.voice_memo_ref ? "ready" : "—"}</div>
            <div>💳 pilot link: {campaign.payment_link ? "ready" : "—"}</div>
            <div>💰 all-in cost: ${campaign.cost_usd.toFixed(2)}</div>
          </div>
        </section>
      </div>

      {/* action bar */}
      <section className="flex items-center gap-3">
        <button
          disabled={!canReview}
          onClick={() => onApprove(campaign.id)}
          className={`px-5 py-2.5 rounded-lg font-semibold transition ${
            canReview
              ? "bg-indigo-500 hover:bg-indigo-400 text-white"
              : "bg-white/5 text-slate-600 cursor-not-allowed"
          }`}
        >
          {sent ? "✓ Approved & sent" : "Approve & Send"}
        </button>
        <button className="px-4 py-2.5 rounded-lg border border-white/10 text-slate-300 hover:bg-white/5" disabled={!canReview}>
          Edit copy
        </button>
        <button className="px-4 py-2.5 rounded-lg border border-white/10 text-slate-300 hover:bg-white/5" disabled={!canReview}>
          Regenerate
        </button>
        <button className="px-4 py-2.5 rounded-lg border border-rose-500/30 text-rose-400 hover:bg-rose-500/10 ml-auto" disabled={!canReview}>
          Kill
        </button>
      </section>
      {sent && (
        <p className="text-emerald-400 text-sm">
          Sent (DRY_RUN honored on the backend). Prospect pays the pilot → Razorpay webhook flips this to <b>WON</b>.
        </p>
      )}
    </main>
  );
}
