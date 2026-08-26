// POST /api/runs  { input, instructions, session_id, conversation_history }
// Server-side proxy to the Hermes gateway's POST /v1/runs. The browser never
// sees HERMES_BASE or HERMES_API_KEY — both live only in this function's
// environment. Requires a valid invite-code session (see _lib/session.mjs).

import { verifyCookie } from "./_lib/session.mjs";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "method not allowed" });
    return;
  }

  const secret = process.env.SESSION_SECRET;
  const base = process.env.HERMES_BASE;
  const key = process.env.HERMES_API_KEY;
  if (!secret || !base || !key) {
    res.status(500).json({ error: "server misconfigured" });
    return;
  }

  const code = await verifyCookie(req.headers.cookie, secret);
  if (!code) {
    res.status(401).json({ error: "unauthorized — enter your invite code" });
    return;
  }

  let body = req.body;
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch {
      body = {};
    }
  }

  let upstream;
  try {
    upstream = await fetch(`${base.replace(/\/$/, "")}/v1/runs`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
        // harmless if HERMES_BASE isn't ngrok; skips the free-tier browser
        // interstitial when it is (PLAN.md's ngrok gotcha).
        "ngrok-skip-browser-warning": "true",
      },
      body: JSON.stringify(body || {}),
    });
  } catch (err) {
    res.status(502).json({ error: "gateway unreachable: " + String(err?.message || err) });
    return;
  }

  const text = await upstream.text();
  res.status(upstream.status);
  res.setHeader("Content-Type", upstream.headers.get("content-type") || "application/json");
  res.send(text);
}
