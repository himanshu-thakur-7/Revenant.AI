// GET /api/runs/:id/events
// Server-side SSE proxy to the Hermes gateway's GET /v1/runs/:id/events.
// Runs on the Edge so the upstream stream is piped straight through instead
// of buffered — the browser sees the same `data: {...}` lines Hermes emits,
// just without ever holding the bearer key. Requires a valid invite-code
// session (see _lib/session.mjs).

import { verifyCookie } from "../../_lib/session.mjs";

export const config = { runtime: "edge" };

export default async function handler(req) {
  const secret = process.env.SESSION_SECRET;
  const base = process.env.HERMES_BASE;
  const key = process.env.HERMES_API_KEY;
  if (!secret || !base || !key) {
    return new Response(JSON.stringify({ error: "server misconfigured" }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  const code = await verifyCookie(req.headers.get("cookie"), secret);
  if (!code) {
    return new Response(JSON.stringify({ error: "unauthorized — enter your invite code" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const id = new URL(req.url).pathname.split("/").filter(Boolean).slice(-2, -1)[0];
  if (!id) {
    return new Response(JSON.stringify({ error: "missing run id" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  let upstream;
  try {
    upstream = await fetch(`${base.replace(/\/$/, "")}/v1/runs/${id}/events`, {
      headers: { Authorization: `Bearer ${key}` },
    });
  } catch (err) {
    return new Response(
      JSON.stringify({ error: "gateway unreachable: " + String(err?.message || err) }),
      { status: 502, headers: { "Content-Type": "application/json" } }
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
    },
  });
}
