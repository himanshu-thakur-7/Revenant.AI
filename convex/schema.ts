// Convex schema — the truth ledger.
//
// The Python pipeline writes here (via ghost/ledger.py in live mode); the React
// console reads via live queries so the whole funnel updates in realtime. This
// is the "immutable profile of the target's exact operational gaps" from the
// storyline — every claim on a microsite traces back to an `evidence` row.

import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  sellers: defineTable({
    slug: v.string(),
    name: v.string(),
    one_liner: v.string(),
    product: v.string(),
    icp: v.string(),
    pain_keywords: v.array(v.string()),
    prototype_kind: v.string(),
    value_prop: v.string(),
    pilot_price_inr: v.number(),
  }).index("by_slug", ["slug"]),

  campaigns: defineTable({
    // mirrors ghost.models.Campaign, flattened for the console
    campaign_id: v.string(), // ULID from the pipeline (stable across upserts)
    seller_id: v.string(),
    company_name: v.string(),
    company_domain: v.string(),
    person_name: v.string(),
    person_title: v.string(),
    state: v.string(), // scouting|scored|building|deployed|filming|awaiting_review|sent|replied|won|killed|warm_only
    tier: v.optional(v.string()),
    combined_score: v.optional(v.number()),
    microsite_url: v.string(),
    walkthrough_url: v.string(),
    voice_memo_ref: v.string(),
    email_subject: v.string(),
    email_body: v.string(),
    payment_link: v.string(),
    cost_usd: v.number(),
    job_description: v.optional(v.string()),
  })
    .index("by_campaign_id", ["campaign_id"])
    .index("by_state", ["state"]),

  evidence: defineTable({
    campaign_id: v.string(),
    source: v.string(), // jd|careers|github|status|eng_blog|news
    url: v.string(),
    excerpt: v.string(), // verbatim — never summarized
    weight: v.number(),
  }).index("by_campaign", ["campaign_id"]),

  // anonymized engagement events from the microsite beacon
  beacons: defineTable({
    campaign_id: v.string(),
    event: v.string(), // view|play_video|play_audio|cta_click|call_click|copy_code
    ts: v.number(),
  }).index("by_campaign", ["campaign_id"]),

  // long-term prospect memory → powers the persistence engine (follow-ups)
  memories: defineTable({
    campaign_id: v.string(),
    person_name: v.string(),
    kind: v.string(), // preference|constraint|commitment|fact
    body: v.string(),
    re_ping_at: v.optional(v.number()), // epoch ms; the cron scans for due ones
  }).index("by_reping", ["re_ping_at"]),
});
