// Tests for api/_lib/session.mjs — the auth core.
//
// This file gets the most adversarial treatment in the suite: it is the only
// thing standing between a request and another customer's data. Tests are
// grouped as round-trip / tampering / expiry / parsing / tenant-mapping, and
// several encode bugs that were real (see the dotted-code case).

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  COOKIE_NAME, inviteEntries, issueCookie, readCookie,
  tenantForCode, validCodes, verifyCookie,
} from "../api/_lib/session.mjs";
import { SECRET, cookieValueFrom, withEnv } from "./helpers.mjs";

const CODES = "plaincode, acme.corp:acme, globex-code:globex, spaced :  padded";

async function cookieFor(code, secret = SECRET) {
  return cookieValueFrom(await issueCookie(code, secret));
}

// ── round trip ────────────────────────────────────────────────────────

test("a freshly issued cookie verifies", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const got = await verifyCookie(await cookieFor("plaincode"), SECRET);
  assert.equal(got.code, "plaincode");
  restore();
});

test("a bare code maps to the default tenant", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const got = await verifyCookie(await cookieFor("plaincode"), SECRET);
  assert.equal(got.tenant, "default");
  restore();
});

test("code:tenant maps to its tenant", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const got = await verifyCookie(await cookieFor("globex-code"), SECRET);
  assert.equal(got.tenant, "globex");
  restore();
});

test("REGRESSION: an invite code containing a dot still verifies", async () => {
  // Was a real bug: verifyCookie required exactly 3 dot-separated parts, so a
  // dotted code was accepted at login (200 + Set-Cookie) and then 401'd on
  // every later request — a silent, permanent, unexplainable lockout.
  const restore = withEnv({ INVITE_CODES: CODES });
  const got = await verifyCookie(await cookieFor("acme.corp"), SECRET);
  assert.equal(got.code, "acme.corp");
  assert.equal(got.tenant, "acme");
  restore();
});

test("entries are trimmed of surrounding whitespace", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  assert.ok(validCodes().includes("spaced"));
  assert.equal(tenantForCode("spaced"), "padded");
  restore();
});

test("a code:  with an empty tenant falls back to default", async () => {
  const restore = withEnv({ INVITE_CODES: "lonely:" });
  assert.equal(tenantForCode("lonely"), "default");
  restore();
});

// ── tampering ─────────────────────────────────────────────────────────

test("a tampered signature is rejected", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const c = await cookieFor("plaincode");
  assert.equal(await verifyCookie(c.slice(0, -4) + "AAAA", SECRET), null);
  restore();
});

test("a cookie signed with a different secret is rejected", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const c = await cookieFor("plaincode", "some-other-secret");
  assert.equal(await verifyCookie(c, SECRET), null);
  restore();
});

test("swapping the code while keeping a valid signature is rejected", async () => {
  // The signature covers code+issuedAt, so substituting a different code must
  // invalidate it — otherwise one tenant's cookie could be edited into another's.
  const restore = withEnv({ INVITE_CODES: CODES });
  const raw = await cookieFor("plaincode");
  const parts = raw.split(".");
  const forged = `${COOKIE_NAME}=globex-code.${parts.at(-2)}.${parts.at(-1)}`;
  assert.equal(await verifyCookie(forged, SECRET), null);
  restore();
});

test("revoking a code invalidates existing cookies immediately", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const c = await cookieFor("globex-code");
  assert.ok(await verifyCookie(c, SECRET));           // valid now
  const restore2 = withEnv({ INVITE_CODES: "plaincode" });   // revoked
  assert.equal(await verifyCookie(c, SECRET), null);
  restore2(); restore();
});

test("a code not in INVITE_CODES is rejected even if well-signed", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const c = await cookieFor("never-configured");
  assert.equal(await verifyCookie(c, SECRET), null);
  restore();
});

// ── expiry / clock ────────────────────────────────────────────────────

function signedWithTimestamp(code, issuedAt, secret = SECRET) {
  // Rebuild the cookie shape by hand so we can control issuedAt.
  return (async () => {
    const { createHmac } = await import("node:crypto");
    const sig = createHmac("sha256", secret)
      .update(`${code}.${issuedAt}`)
      .digest("base64")
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    return `${COOKIE_NAME}=${code}.${issuedAt}.${sig}`;
  })();
}

test("an expired cookie is rejected", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const longAgo = Math.floor(Date.now() / 1000) - (60 * 60 * 24 * 31); // 31d
  assert.equal(await verifyCookie(await signedWithTimestamp("plaincode", longAgo), SECRET), null);
  restore();
});

test("a future-dated cookie is rejected", async () => {
  // Guards against a clock-skew or hand-rolled cookie extending its own life.
  const restore = withEnv({ INVITE_CODES: CODES });
  const future = Math.floor(Date.now() / 1000) + 9999;
  assert.equal(await verifyCookie(await signedWithTimestamp("plaincode", future), SECRET), null);
  restore();
});

test("a non-numeric timestamp is rejected", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  assert.equal(await verifyCookie(await signedWithTimestamp("plaincode", "abc"), SECRET), null);
  restore();
});

// ── malformed input ───────────────────────────────────────────────────

test("missing cookie header is rejected", async () => {
  assert.equal(await verifyCookie(undefined, SECRET), null);
  assert.equal(await verifyCookie("", SECRET), null);
});

test("a cookie with too few segments is rejected", async () => {
  assert.equal(await verifyCookie(`${COOKIE_NAME}=onlyone`, SECRET), null);
  assert.equal(await verifyCookie(`${COOKIE_NAME}=two.parts`, SECRET), null);
});

test("an empty code segment is rejected", async () => {
  assert.equal(await verifyCookie(`${COOKIE_NAME}=.123.sig`, SECRET), null);
});

test("an unrelated cookie name is ignored", async () => {
  assert.equal(await verifyCookie("other_cookie=whatever", SECRET), null);
});

test("the session cookie is found among several cookies", async () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  const c = await cookieFor("plaincode");
  const header = `theme=dark; ${c}; other=1`;
  assert.ok(await verifyCookie(header, SECRET));
  restore();
});

test("readCookie tolerates values containing '='", () => {
  assert.equal(readCookie(`${COOKIE_NAME}=a=b=c`), "a=b=c");
});

test("no INVITE_CODES configured means nothing validates", async () => {
  const restore = withEnv({ INVITE_CODES: "" });
  assert.deepEqual(validCodes(), []);
  assert.equal(await verifyCookie(await cookieFor("plaincode"), SECRET), null);
  restore();
});

test("inviteEntries drops blank entries from a trailing comma", () => {
  const restore = withEnv({ INVITE_CODES: "a:1,,b:2," });
  assert.deepEqual(inviteEntries().map((e) => e.code), ["a", "b"]);
  restore();
});

test("tenantForCode returns null for an unknown code", () => {
  const restore = withEnv({ INVITE_CODES: CODES });
  assert.equal(tenantForCode("nope"), null);
  restore();
});

// ── cookie attributes ─────────────────────────────────────────────────

test("the issued cookie carries the expected security flags", async () => {
  const header = await issueCookie("plaincode", SECRET);
  assert.match(header, /HttpOnly/);
  assert.match(header, /Secure/);
  assert.match(header, /SameSite=Lax/);
  assert.match(header, /Path=\//);
  assert.match(header, /Max-Age=\d+/);
});
