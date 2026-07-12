// Internal mutations invoked by HTTP actions (webhook + beacon).

import { internalMutation } from "./_generated/server";
import { v } from "convex/values";

// The WON moment. Flips the campaign; the console's live feed shows it.
export const markWon = internalMutation({
  args: { campaign_id: v.string() },
  handler: async (ctx, { campaign_id }) => {
    const all = await ctx.db.query("campaigns").collect();
    const row: any = all.find((d: any) => d.id === campaign_id);
    if (row) {
      await ctx.db.patch(row._id, { state: "won" });
      await ctx.db.insert("memories", {
        campaign_id,
        person_name: row.lead?.person_name ?? "",
        kind: "commitment",
        body: `Paid the pilot for ${row.lead?.company_name ?? "the prospect"}. Human closer to take over.`,
        re_ping_at: null,
      });
      await ctx.db.insert("events", {
        at: 9999,
        act: 5,
        agent: "Razorpay",
        kind: "payment",
        message: `Pilot payment received from ${row.lead?.company_name ?? "prospect"} — deal WON.`,
        campaign_id,
        company: row.lead?.company_name ?? "",
        payload: { state: "won" },
      });
    }
  },
});

export const recordBeacon = internalMutation({
  args: { campaign_id: v.string(), event: v.string(), ts: v.number() },
  handler: async (ctx, args) => {
    await ctx.db.insert("beacons", args);
  },
});
