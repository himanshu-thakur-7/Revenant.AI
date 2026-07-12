// Cron — the persistence engine's nightly scan.
//
// Mirrors the master plan's re-engagement scheduler: each night, find memories
// whose deferral window has closed and spawn a follow-up. In the buildathon
// this is demoed via the Hermes `ghost-followup` skill, but wiring it here shows
// the loop is designed to run unattended.

import { cronJobs } from "convex/server";
import { internalMutation } from "./_generated/server";
import { internal } from "./_generated/api";

const crons = cronJobs();

// 3 AM local — the hour the whole system is themed around.
crons.daily(
  "re-engagement scan",
  { hourUTC: 21, minuteUTC: 30 }, // ~3 AM IST
  internal.crons.scanDueMemories,
);

export const scanDueMemories = internalMutation({
  args: {},
  handler: async (ctx) => {
    const now = Date.now();
    const due = await ctx.db
      .query("memories")
      .withIndex("by_reping", (q) => q.lte("re_ping_at", now))
      .collect();
    // Each due memory re-opens its campaign into the re-engagement lane. The
    // Copywriter is later forced to reference the specific prior commitment.
    for (const m of due) {
      const camp = await ctx.db
        .query("campaigns")
        .withIndex("by_campaign_id", (q) => q.eq("campaign_id", m.campaign_id))
        .unique();
      if (camp && camp.state !== "won") {
        await ctx.db.patch(camp._id, { state: "awaiting_review" });
      }
      // consume the trigger so we don't re-fire nightly
      await ctx.db.patch(m._id, { re_ping_at: undefined });
    }
    return { reengaged: due.length };
  },
});

export default crons;
