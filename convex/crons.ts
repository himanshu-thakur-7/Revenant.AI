// Cron — the persistence engine's nightly scan (~3 AM IST).
//
// Finds memories whose deferral window has closed and re-opens their campaigns
// into the review queue. Mirrors skills/ghost-followup so the loop runs
// unattended in the cloud, not just when Hermes is asked.

import { cronJobs } from "convex/server";
import { internalMutation } from "./_generated/server";
import { internal } from "./_generated/api";

const crons = cronJobs();

crons.daily(
  "re-engagement scan",
  { hourUTC: 21, minuteUTC: 30 }, // ~3 AM IST
  internal.crons.scanDueMemories,
);

export const scanDueMemories = internalMutation({
  args: {},
  handler: async (ctx) => {
    const now = Date.now();
    const memories = await ctx.db.query("memories").collect();
    const due = memories.filter(
      (m: any) => m.re_ping_at != null && m.re_ping_at <= now,
    );
    const campaigns = await ctx.db.query("campaigns").collect();
    for (const m of due as any[]) {
      const camp: any = campaigns.find((c: any) => c.id === m.campaign_id);
      if (camp && camp.state !== "won") {
        await ctx.db.patch(camp._id, { state: "awaiting_review" });
      }
      await ctx.db.patch(m._id, { re_ping_at: null }); // consume the trigger
    }
    return { reengaged: due.length };
  },
});

export default crons;
