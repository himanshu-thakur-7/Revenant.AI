// GET /api/health
// Two things folded into one poll (the console calls this every 15s):
//  1. Is the caller's invite-code session still valid? (401 if not — the
//     console reopens the invite-code gate on this.)
//  2. Is the Hermes gateway itself reachable right now? ({ok:false} if not —
//     ok:true never proves Hermes is *healthy*, only that HERMES_BASE answered.)
// Never returns HERMES_BASE or HERMES_API_KEY to the client.

import { verifyCookie } from "./_lib/session.mjs";

export default async function handler(req, res) {
  const secret = process.env.SESSION_SECRET;
  const base = process.env.HERMES_BASE;
  if (!secret || !base) {
    res.status(500).json({ error: "server misconfigured" });
    return;
  }

  const code = await verifyCookie(req.headers.cookie, secret);
  if (!code) {
    res.status(401).json({ error: "unauthorized" });
    return;
  }

  try {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 3500);
    const upstream = await fetch(`${base.replace(/\/$/, "")}/health`, {
      signal: ctrl.signal,
    });
    clearTimeout(to);
    res.status(200).json({ ok: upstream.ok });
  } catch {
    res.status(200).json({ ok: false });
  }
}
