// HTTP endpoints — the Razorpay webhook (the WON moment) and the microsite beacon.

import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { internal } from "./_generated/api";

const http = httpRouter();

// ── Razorpay webhook ──────────────────────────────────────────
// When a prospect pays the paid-pilot link, Razorpay POSTs here. We verify the
// signature, then flip the campaign to `won`. On stage: judge pays ₹1 test-mode,
// the dashboard flips live, and Hermes pings the human closer on Telegram.
http.route({
  path: "/razorpay/webhook",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const body = await request.text();
    const signature = request.headers.get("x-razorpay-signature") ?? "";
    const secret = process.env.RAZORPAY_WEBHOOK_SECRET ?? "";

    if (secret && !(await verify(body, signature, secret))) {
      return new Response("bad signature", { status: 401 });
    }

    let payload: any = {};
    try {
      payload = JSON.parse(body);
    } catch {
      return new Response("bad json", { status: 400 });
    }

    // campaign_id was stashed in the payment link's notes at creation time
    const notes =
      payload?.payload?.payment_link?.entity?.notes ??
      payload?.payload?.payment?.entity?.notes ??
      {};
    const campaignId = notes.campaign_id;

    if (campaignId) {
      await ctx.runMutation(internal.internal.markWon, { campaign_id: campaignId });
    }
    return new Response("ok", { status: 200 });
  }),
});

// ── Microsite beacon ──────────────────────────────────────────
// Anonymized engagement events (view/play/cta) → the reply-classifier's signal.
http.route({
  path: "/beacon",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    try {
      const { c, e, t } = JSON.parse(await request.text());
      if (c && e) {
        await ctx.runMutation(internal.internal.recordBeacon, {
          campaign_id: c,
          event: e,
          ts: t ?? Date.now(),
        });
      }
    } catch {
      /* best-effort; never fail a beacon */
    }
    return new Response("", { status: 204 });
  }),
});

// HMAC-SHA256 verification using WebCrypto (Convex runtime).
async function verify(body: string, signature: string, secret: string): Promise<boolean> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const hex = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return hex === signature;
}

export default http;
