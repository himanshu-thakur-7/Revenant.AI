// POST /api/auth  { code: "..." }
// Validates an invite code against INVITE_CODES and, on success, sets the
// signed session cookie that /api/runs and /api/runs/[id]/events require.
// See _lib/session.mjs for the (stateless, no-database) cookie design.

import { issueCookie, validCodes } from "./_lib/session.mjs";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "method not allowed" });
    return;
  }

  const secret = process.env.SESSION_SECRET;
  if (!secret) {
    res.status(500).json({ error: "server misconfigured (SESSION_SECRET unset)" });
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
  const code = (body?.code || "").trim();

  if (!code || !validCodes().includes(code)) {
    res.status(401).json({ error: "Invalid invite code." });
    return;
  }

  const cookie = await issueCookie(code, secret);
  res.setHeader("Set-Cookie", cookie);
  res.status(200).json({ ok: true });
}
