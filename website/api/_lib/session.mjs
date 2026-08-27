// Stateless invite-code sessions — no database.
//
// A cookie is `<code>.<issuedAt>.<hmac>` where hmac = HMAC-SHA256(`<code>.<issuedAt>`, SESSION_SECRET).
// Validity requires BOTH a matching signature AND that `code` still appears in the
// current INVITE_CODES env var — so revoking access is just editing INVITE_CODES
// and redeploying; no per-session store needed, no cookie to individually invalidate.
//
// Written against Web-standard APIs only (crypto.subtle, TextEncoder, btoa) —
// no Buffer, no `node:` imports — so this same file runs unchanged from both
// the Node.js and the Edge Vercel runtimes.

export const COOKIE_NAME = "revenant_session";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days

function toBase64Url(buf) {
  let bin = "";
  for (const b of new Uint8Array(buf)) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(message, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return toBase64Url(sig);
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// INVITE_CODES entries are either `code` or `code:tenant`.
//
// The tenant is the authoritative customer identity for that code, and it is
// looked up SERVER-SIDE from the code on every request — it is deliberately
// not stored in the cookie and never read from the request body. A tenant the
// client could supply would be a tenant the client could change.
//
// A bare `code` maps to the "default" tenant, so existing single-tenant
// deployments keep working unchanged.
const DEFAULT_TENANT = "default";

function parseEntry(entry) {
  const i = entry.indexOf(":");
  if (i === -1) return { code: entry, tenant: DEFAULT_TENANT };
  return {
    code: entry.slice(0, i).trim(),
    tenant: entry.slice(i + 1).trim() || DEFAULT_TENANT,
  };
}

/** [{code, tenant}] for every configured invite code. */
export function inviteEntries() {
  return (process.env.INVITE_CODES || "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean)
    .map(parseEntry)
    .filter((e) => e.code);
}

export function validCodes() {
  return inviteEntries().map((e) => e.code);
}

/** The tenant a code belongs to, or null if the code isn't configured. */
export function tenantForCode(code) {
  const hit = inviteEntries().find((e) => e.code === code);
  return hit ? hit.tenant : null;
}

/** Build a Set-Cookie header value for a freshly-validated invite code. */
export async function issueCookie(code, secret) {
  const issuedAt = Math.floor(Date.now() / 1000);
  const payload = `${code}.${issuedAt}`;
  const sig = await hmac(payload, secret);
  const value = `${payload}.${sig}`;
  return (
    `${COOKIE_NAME}=${value}; HttpOnly; Secure; SameSite=Lax; Path=/; ` +
    `Max-Age=${MAX_AGE_SECONDS}`
  );
}

export function readCookie(cookieHeader) {
  if (!cookieHeader) return null;
  const parts = cookieHeader.split(";").map((p) => p.trim());
  for (const p of parts) {
    const eq = p.indexOf("=");
    if (eq === -1) continue;
    if (p.slice(0, eq) === COOKIE_NAME) return p.slice(eq + 1);
  }
  return null;
}

/**
 * Returns {code, tenant} if the cookie is valid + still active, else null.
 *
 * Parsing note: this used to require exactly 3 dot-separated parts, which
 * silently and permanently locked out any invite code CONTAINING a dot —
 * auth.mjs would accept the code and set a 200 cookie, then every later
 * request 401'd with no explanation. The signature and timestamp are both
 * dot-free by construction, so the code is now everything before the last
 * two segments and may itself contain dots.
 */
export async function verifyCookie(cookieHeader, secret) {
  const raw = readCookie(cookieHeader);
  if (!raw) return null;
  const parts = raw.split(".");
  if (parts.length < 3) return null;

  const sig = parts[parts.length - 1];
  const issuedAt = parts[parts.length - 2];
  const code = parts.slice(0, -2).join(".");
  if (!code) return null;

  const age = Math.floor(Date.now() / 1000) - Number(issuedAt);
  if (!Number.isFinite(age) || age < 0 || age > MAX_AGE_SECONDS) return null;

  const expected = await hmac(`${code}.${issuedAt}`, secret);
  if (!timingSafeEqual(expected, sig)) return null;

  // Re-check against the CURRENT list every request — this is what makes
  // revocation instant (edit INVITE_CODES + redeploy) without a session store.
  const tenant = tenantForCode(code);
  if (tenant === null) return null;

  return { code, tenant };
}
