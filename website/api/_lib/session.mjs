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

export function validCodes() {
  return (process.env.INVITE_CODES || "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean);
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

/** Returns the invite code string if the cookie is valid + still active, else null. */
export async function verifyCookie(cookieHeader, secret) {
  const raw = readCookie(cookieHeader);
  if (!raw) return null;
  const parts = raw.split(".");
  if (parts.length !== 3) return null;
  const [code, issuedAt, sig] = parts;

  const age = Math.floor(Date.now() / 1000) - Number(issuedAt);
  if (!Number.isFinite(age) || age < 0 || age > MAX_AGE_SECONDS) return null;

  const expected = await hmac(`${code}.${issuedAt}`, secret);
  if (!timingSafeEqual(expected, sig)) return null;

  // Re-check against the CURRENT list every request — this is what makes
  // revocation instant (edit INVITE_CODES + redeploy) without a session store.
  if (!validCodes().includes(code)) return null;

  return code;
}
