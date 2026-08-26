// The truth ledger's public API — what the Python pipeline writes and the
// console reads. Docs are stored as-is (pipeline pydantic models are the
// source of truth); upserts key on the pipeline's ULID `id` field.

import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

async function upsertBy(ctx: any, table: string, doc: any) {
  const all = await ctx.db.query(table).collect();
  const existing = all.find((d: any) => d.id === doc.id);
  if (existing) {
    await ctx.db.replace(existing._id, doc);
    return existing._id;
  }
  return await ctx.db.insert(table, doc);
}

export const upsertSeller = mutation({
  args: { doc: v.any() },
  handler: async (ctx, { doc }) => upsertBy(ctx, "sellers", doc),
});

export const upsertCampaign = mutation({
  args: { doc: v.any() },
  handler: async (ctx, { doc }) => upsertBy(ctx, "campaigns", doc),
});

export const addMemory = mutation({
  args: { doc: v.any() },
  handler: async (ctx, { doc }) => ctx.db.insert("memories", doc),
});

// A new run replaces the board: wipe campaigns + events so the console shows
// exactly one coherent story at a time (demo-friendly; history isn't the point).
export const reset = mutation({
  args: {},
  handler: async (ctx) => {
    for (const table of ["campaigns", "events"] as const) {
      const rows = await ctx.db.query(table).collect();
      await Promise.all(rows.map((r) => ctx.db.delete(r._id)));
    }
  },
});

// Batch event ingest — one call per run, not one per event.
export const addEvents = mutation({
  args: { runId: v.string(), docs: v.array(v.any()) },
  handler: async (ctx, { runId, docs }) => {
    for (const doc of docs) {
      await ctx.db.insert("events", { ...doc, run_id: runId });
    }
    return docs.length;
  },
});

export const listCampaigns = query({
  args: {},
  handler: async (ctx) => {
    const rows = await ctx.db.query("campaigns").collect();
    return rows.map(({ _id, _creationTime, ...doc }) => doc);
  },
});

export const listEvents = query({
  args: {},
  handler: async (ctx) => {
    const rows = await ctx.db.query("events").collect();
    return rows
      .map(({ _id, _creationTime, ...doc }) => doc)
      .sort((a: any, b: any) => (a.at ?? 0) - (b.at ?? 0));
  },
});

// Console approve → state flip, mirrored for the pipeline to observe.
export const setState = mutation({
  args: { campaign_id: v.string(), state: v.string() },
  handler: async (ctx, { campaign_id, state }) => {
    const all = await ctx.db.query("campaigns").collect();
    const row = all.find((d: any) => d.id === campaign_id);
    if (row) await ctx.db.patch(row._id, { state });
  },
});

// Console human-in-the-loop amend → update the review draft in place.
export const updateDraft = mutation({
  args: {
    campaign_id: v.string(),
    email_subject: v.string(),
    email_body: v.string(),
  },
  handler: async (ctx, { campaign_id, email_subject, email_body }) => {
    const all = await ctx.db.query("campaigns").collect();
    const row = all.find((d: any) => d.id === campaign_id);
    if (!row) return false;

    await ctx.db.patch(row._id, {
      email_subject,
      email_body,
      state: "awaiting_review",
    });

    const events = await ctx.db.query("events").collect();
    const at = events.length
      ? Math.max(...events.map((e: any) => Number(e.at ?? 0))) + 0.1
      : 0;
    await ctx.db.insert("events", {
      id: `ev_console_amend_${campaign_id}_${Date.now()}`,
      run_id: "console",
      at,
      act: 5,
      agent: "Human Closer",
      kind: "mail",
      message: "Draft amended by the founder — awaiting final approval.",
      campaign_id,
      company: row.lead?.company_name ?? "",
      payload: { state: "awaiting_review", amended: true },
    });
    return true;
  },
});
