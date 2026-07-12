// Data layer for the console.
//
// Fallback chain, same shapes throughout:
//   1. live   — VITE_CONVEX_URL set → query the deployed Convex truth ledger
//   2. dev    — /ledger.json synced by scripts/sync_console.py
//   3. static — baked-in demoData (the Vercel deploy path)
//
// The console never mutates pipeline state except through explicit actions.

export type Evidence = { id?: string; source: string; url: string; excerpt: string; weight: number };

export type SignalScore = {
  jd_confidence?: number;
  careers_score?: number;
  github_score?: number;
  status_score?: number;
  eng_blog_score?: number;
  combined: number;
  tier: string;
  evidence: Evidence[];
};

export type Campaign = {
  [key: string]: unknown;
  id: string;
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
  deck_url?: string;
  payment_link: string;
  cost_usd: number;
  lead: {
    id?: string;
    seller_id?: string;
    company_name: string;
    company_domain: string;
    person_name: string;
    person_title: string;
    job_description: string;
    score?: SignalScore;
  };
};

export type MissionEvent = {
  id: string;
  at: number; // seconds on the replay clock
  act: number; // 2..5
  agent: string;
  kind: string; // info|query|evidence|verdict|code|artifact|film|voice|mail|alert|payment|state
  message: string;
  campaign_id: string;
  company: string;
  payload: Record<string, unknown>;
};

const CONVEX_URL = (import.meta as any).env?.VITE_CONVEX_URL as string | undefined;

async function convexQuery(path: string): Promise<any | null> {
  if (!CONVEX_URL) return null;
  try {
    const res = await fetch(`${CONVEX_URL}/api/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, args: {}, format: "json" }),
    });
    const json = await res.json();
    if (json.status === "success") return json.value;
  } catch {
    /* fall through */
  }
  return null;
}

export async function loadAll(): Promise<{
  campaigns: Campaign[];
  events: MissionEvent[];
  source: "convex" | "ledger" | "demo";
}> {
  // 1. live Convex
  const [c, e] = await Promise.all([
    convexQuery("ledger:listCampaigns"),
    convexQuery("ledger:listEvents"),
  ]);
  if (c?.length) {
    return { campaigns: c, events: e ?? [], source: "convex" };
  }
  // 2. locally-synced ledger
  try {
    const res = await fetch("/ledger.json", { cache: "no-store" });
    if (res.ok) {
      const snap = await res.json();
      if (snap.campaigns?.length) {
        return { campaigns: snap.campaigns, events: snap.events ?? [], source: "ledger" };
      }
    }
  } catch {
    /* fall through */
  }
  // 3. baked-in demo dataset
  const { DEMO_CAMPAIGNS, DEMO_EVENTS } = await import("./demoData");
  return { campaigns: DEMO_CAMPAIGNS, events: DEMO_EVENTS, source: "demo" };
}

export async function convexSetState(campaign_id: string, state: string): Promise<boolean> {
  if (!CONVEX_URL) return false;
  try {
    const res = await fetch(`${CONVEX_URL}/api/mutation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: "ledger:setState", args: { campaign_id, state }, format: "json" }),
    });
    return (await res.json()).status === "success";
  } catch {
    return false;
  }
}
