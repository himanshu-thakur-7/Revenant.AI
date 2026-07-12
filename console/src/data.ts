// Data layer for the console.
//
// Two backends, same shape:
//   • live  — when VITE_CONVEX_URL is set, poll Convex `campaigns:list` so the
//             funnel updates in realtime (a full Convex client would use
//             useQuery; we poll to keep the console dependency-light and
//             demoable without a Convex codegen step).
//   • offline — fetch the pipeline's out/ledger.json (copied into public/).
//
// The pipeline is the source of truth either way; the console never mutates
// pipeline state except through explicit approve/kill actions.

export type Campaign = {
  id: string;
  campaign_id?: string;
  seller_id: string;
  state: string;
  tier?: string;
  combined_score?: number;
  microsite_url: string;
  microsite_html?: string;
  walkthrough_url: string;
  voice_memo_ref: string;
  email_subject: string;
  email_body: string;
  payment_link: string;
  cost_usd: number;
  lead: {
    company_name: string;
    company_domain: string;
    person_name: string;
    person_title: string;
    job_description: string;
    score?: {
      combined: number;
      tier: string;
      evidence: { source: string; url: string; excerpt: string; weight: number }[];
    };
  };
};

const CONVEX_URL = (import.meta as any).env?.VITE_CONVEX_URL as string | undefined;

export async function loadCampaigns(): Promise<Campaign[]> {
  // 1. live Convex (realtime) if configured
  if (CONVEX_URL) {
    try {
      const res = await fetch(`${CONVEX_URL}/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: "campaigns:list", args: {} }),
      });
      const json = await res.json();
      const rows = (json.value ?? json) as Campaign[];
      if (rows?.length) return rows;
    } catch {
      /* fall through */
    }
  }
  // 2. locally-synced ledger (dev)
  try {
    const res = await fetch("/ledger.json", { cache: "no-store" });
    if (res.ok) {
      const snap = await res.json();
      if (snap.campaigns?.length) return snap.campaigns as Campaign[];
    }
  } catch {
    /* fall through */
  }
  // 3. baked-in demo dataset (static deploy — Vercel)
  const { DEMO_CAMPAIGNS } = await import("./demoData");
  return DEMO_CAMPAIGNS;
}

export const TIER_COLOR: Record<string, string> = {
  promote: "text-emerald-400",
  corroborate: "text-sky-400",
  warm_only: "text-amber-400",
  kill: "text-rose-500",
};

export const STATE_BADGE: Record<string, string> = {
  awaiting_review: "bg-indigo-500/20 text-indigo-300 border-indigo-500/40",
  won: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  sent: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  warm_only: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  killed: "bg-rose-500/15 text-rose-400 border-rose-500/30",
};
