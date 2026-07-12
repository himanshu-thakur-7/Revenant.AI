// Internal mutations invoked by HTTP actions (webhook + beacon).

import { internalMutation } from "./_generated/server";
import { v } from "convex/values";

// The WON moment. Flips the campaign and (in live wiring) triggers the Hermes
// Telegram alert to the human closer via a scheduled action.
export const markWon = internalMutation({
  args: { campaign_id: v.string() },
  handler: async (ctx, { campaign_id }) => {
    const row = await ctx.db
      .query("campaigns")
      .withIndex("by_campaign_id", (q) => q.eq("campaign_id", campaign_id))
      .unique();
    if (row) {
      await ctx.db.patch(row._id, { state: "won" });
      // Persistence-engine hook: a WON deal is a commitment worth remembering.
      await ctx.db.insert("memories", {
        campaign_id,
        person_name: row.person_name,
        kind: "commitment",
        body: `Paid the pilot for ${row.company_name}. Human closer to take over.`,
        re_ping_at: undefined,
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
