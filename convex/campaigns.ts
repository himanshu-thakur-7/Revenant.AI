// Campaign mutations + queries — the state machine and the console's data source.

import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

// Upsert a campaign row keyed by the pipeline's stable campaign_id.
export const upsert = mutation({
  args: {
    campaign_id: v.string(),
    seller_id: v.string(),
    company_name: v.string(),
    company_domain: v.string(),
    person_name: v.optional(v.string()),
    person_title: v.optional(v.string()),
    state: v.string(),
    tier: v.optional(v.string()),
    combined_score: v.optional(v.number()),
    microsite_url: v.optional(v.string()),
    walkthrough_url: v.optional(v.string()),
    voice_memo_ref: v.optional(v.string()),
    email_subject: v.optional(v.string()),
    email_body: v.optional(v.string()),
    payment_link: v.optional(v.string()),
    cost_usd: v.optional(v.number()),
    job_description: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("campaigns")
      .withIndex("by_campaign_id", (q) => q.eq("campaign_id", args.campaign_id))
      .unique();
    const row = {
      campaign_id: args.campaign_id,
      seller_id: args.seller_id,
      company_name: args.company_name,
      company_domain: args.company_domain,
      person_name: args.person_name ?? "",
      person_title: args.person_title ?? "",
      state: args.state,
      tier: args.tier,
      combined_score: args.combined_score,
      microsite_url: args.microsite_url ?? "",
      walkthrough_url: args.walkthrough_url ?? "",
      voice_memo_ref: args.voice_memo_ref ?? "",
      email_subject: args.email_subject ?? "",
      email_body: args.email_body ?? "",
      payment_link: args.payment_link ?? "",
      cost_usd: args.cost_usd ?? 0,
      job_description: args.job_description,
    };
    if (existing) {
      await ctx.db.patch(existing._id, row);
      return existing._id;
    }
    return await ctx.db.insert("campaigns", row);
  },
});

// Explicit state transition (used by the console's approve/kill buttons).
export const setState = mutation({
  args: { campaign_id: v.string(), state: v.string() },
  handler: async (ctx, { campaign_id, state }) => {
    const row = await ctx.db
      .query("campaigns")
      .withIndex("by_campaign_id", (q) => q.eq("campaign_id", campaign_id))
      .unique();
    if (row) await ctx.db.patch(row._id, { state });
  },
});

// Inline edit from the console — the (before, after) diff can seed an eval set.
export const editEmail = mutation({
  args: { campaign_id: v.string(), subject: v.string(), body: v.string() },
  handler: async (ctx, { campaign_id, subject, body }) => {
    const row = await ctx.db
      .query("campaigns")
      .withIndex("by_campaign_id", (q) => q.eq("campaign_id", campaign_id))
      .unique();
    if (row) await ctx.db.patch(row._id, { email_subject: subject, email_body: body });
  },
});

// All campaigns, newest first — the console dashboard.
export const list = query({
  args: {},
  handler: async (ctx) => {
    return await ctx.db.query("campaigns").order("desc").take(200);
  },
});

// Just the review queue.
export const queue = query({
  args: {},
  handler: async (ctx) => {
    return await ctx.db
      .query("campaigns")
      .withIndex("by_state", (q) => q.eq("state", "awaiting_review"))
      .collect();
  },
});

// Evidence for one campaign — rendered in the preview pane.
export const evidenceFor = query({
  args: { campaign_id: v.string() },
  handler: async (ctx, { campaign_id }) => {
    return await ctx.db
      .query("evidence")
      .withIndex("by_campaign", (q) => q.eq("campaign_id", campaign_id))
      .collect();
  },
});

// Funnel counts for the header stats.
export const funnel = query({
  args: {},
  handler: async (ctx) => {
    const all = await ctx.db.query("campaigns").collect();
    const counts: Record<string, number> = {};
    let cost = 0;
    for (const c of all) {
      counts[c.state] = (counts[c.state] ?? 0) + 1;
      cost += c.cost_usd ?? 0;
    }
    return { counts, cost: Math.round(cost * 100) / 100, total: all.length };
  },
});
