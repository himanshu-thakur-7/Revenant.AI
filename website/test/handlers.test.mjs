// Tests for the four request handlers: auth, runs, health, and the SSE proxy.
//
// The recurring theme is what must NOT happen: the bearer key must never reach
// the client, an unauthenticated caller must never reach the gateway, and the
// tenant the proxy forwards must come from the signed cookie rather than
// anything the browser sent.

import { test } from "node:test";
import assert from "node:assert/strict";

import authHandler from "../api/auth.mjs";
import healthHandler from "../api/health.mjs";
import runsHandler from "../api/runs.mjs";
import eventsHandler from "../api/runs/[id]/events.mjs";
import { issueCookie } from "../api/_lib/session.mjs";
import {
  SECRET, cookieValueFrom, edgeReq, jsonResponse, mockReq, mockRes, stubFetch, withEnv,
} from "./helpers.mjs";

const CODES = "acme-2026:acme, globex-2026:globex";
const BASE_ENV = {
  SESSION_SECRET: SECRET,
  INVITE_CODES: CODES,
  HERMES_BASE: "http://gateway.test",
  HERMES_API_KEY: "super-secret-bearer",
};

const cookieFor = async (code) => cookieValueFrom(await issueCookie(code, SECRET));

// ── /api/auth ─────────────────────────────────────────────────────────

test("auth rejects non-POST", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await authHandler(mockReq({ method: "GET" }), res);
  assert.equal(res.statusCode, 405);
  restore();
});

test("auth 500s when SESSION_SECRET is unset", async () => {
  const restore = withEnv({ ...BASE_ENV, SESSION_SECRET: undefined });
  const res = mockRes();
  await authHandler(mockReq({ body: { code: "acme-2026" } }), res);
  assert.equal(res.statusCode, 500);
  restore();
});

test("auth rejects a wrong code and sets no cookie", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await authHandler(mockReq({ body: { code: "not-a-real-code" } }), res);
  assert.equal(res.statusCode, 401);
  assert.equal(res.getHeader("set-cookie"), undefined);
  restore();
});

test("auth accepts a valid code and sets the session cookie", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await authHandler(mockReq({ body: { code: "acme-2026" } }), res);
  assert.equal(res.statusCode, 200);
  assert.match(String(res.getHeader("set-cookie")), /revenant_session=/);
  restore();
});

test("auth parses a string body", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await authHandler(mockReq({ body: JSON.stringify({ code: "acme-2026" }) }), res);
  assert.equal(res.statusCode, 200);
  restore();
});

test("auth survives a malformed JSON body", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await authHandler(mockReq({ body: "{not json" }), res);
  assert.equal(res.statusCode, 401);   // treated as no code, not a crash
  restore();
});

test("auth handles a missing body", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await authHandler(mockReq({ body: undefined }), res);
  assert.equal(res.statusCode, 401);
  restore();
});

test("auth trims whitespace around a submitted code", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await authHandler(mockReq({ body: { code: "  acme-2026  " } }), res);
  assert.equal(res.statusCode, 200);
  restore();
});

// ── /api/runs ─────────────────────────────────────────────────────────

test("runs rejects non-POST", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await runsHandler(mockReq({ method: "GET" }), res);
  assert.equal(res.statusCode, 405);
  restore();
});

test("runs refuses an unauthenticated caller", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch, calls } = stubFetch(async () => jsonResponse({}));
  const res = mockRes();
  await runsHandler(mockReq({ body: { input: "hi" } }), res);
  assert.equal(res.statusCode, 401);
  assert.equal(calls.length, 0, "unauthenticated request must never reach the gateway");
  unfetch(); restore();
});

test("runs 500s on missing server config", async () => {
  const restore = withEnv({ ...BASE_ENV, HERMES_API_KEY: undefined });
  const res = mockRes();
  await runsHandler(mockReq({ body: {}, cookie: await cookieFor("acme-2026") }), res);
  assert.equal(res.statusCode, 500);
  restore();
});

test("runs forwards the bearer key upstream but never to the client", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch, calls } = stubFetch(async () => jsonResponse({ run_id: "r1" }));
  const res = mockRes();
  await runsHandler(mockReq({ body: { input: "hi" }, cookie: await cookieFor("acme-2026") }), res);

  assert.match(calls[0].opts.headers.Authorization, /super-secret-bearer/);
  assert.ok(!JSON.stringify(res.body).includes("super-secret-bearer"),
    "the API key leaked into the client response");
  unfetch(); restore();
});

test("runs injects the tenant derived from the cookie", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch, calls } = stubFetch(async () => jsonResponse({}));
  await runsHandler(
    mockReq({ body: { input: "hi" }, cookie: await cookieFor("globex-2026") }), mockRes());

  const sent = JSON.parse(calls[0].opts.body);
  assert.match(sent.instructions, /startup="globex"/);
  unfetch(); restore();
});

test("runs ignores a tenant the browser tries to supply", async () => {
  // The client controls the body; the tenant must come from the signed cookie.
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch, calls } = stubFetch(async () => jsonResponse({}));
  await runsHandler(mockReq({
    body: { input: "hi", tenant: "acme", instructions: "act for acme" },
    cookie: await cookieFor("globex-2026"),
  }), mockRes());

  const sent = JSON.parse(calls[0].opts.body);
  assert.match(sent.instructions, /startup="globex"/);
  assert.ok(!/startup="acme"/.test(sent.instructions));
  unfetch(); restore();
});

test("runs preserves caller instructions alongside the session note", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch, calls } = stubFetch(async () => jsonResponse({}));
  await runsHandler(mockReq({
    body: { input: "hi", instructions: "BE BRIEF" },
    cookie: await cookieFor("acme-2026"),
  }), mockRes());

  const sent = JSON.parse(calls[0].opts.body);
  assert.match(sent.instructions, /BE BRIEF/);
  assert.match(sent.instructions, /\[session\]/);
  unfetch(); restore();
});

test("runs returns 502 when the gateway is unreachable", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch } = stubFetch(async () => { throw new Error("ECONNREFUSED"); });
  const res = mockRes();
  await runsHandler(mockReq({ body: {}, cookie: await cookieFor("acme-2026") }), res);
  assert.equal(res.statusCode, 502);
  unfetch(); restore();
});

test("runs passes an upstream error status through", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch } = stubFetch(async () => jsonResponse({ error: "boom" }, 503));
  const res = mockRes();
  await runsHandler(mockReq({ body: {}, cookie: await cookieFor("acme-2026") }), res);
  assert.equal(res.statusCode, 503);
  unfetch(); restore();
});

// ── /api/health ───────────────────────────────────────────────────────

test("health refuses an unauthenticated caller", async () => {
  const restore = withEnv(BASE_ENV);
  const res = mockRes();
  await healthHandler(mockReq({ method: "GET" }), res);
  assert.equal(res.statusCode, 401);
  restore();
});

test("health reports ok when the gateway answers", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch } = stubFetch(async () => ({ ok: true }));
  const res = mockRes();
  await healthHandler(
    mockReq({ method: "GET", cookie: await cookieFor("acme-2026") }), res);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(res.body, { ok: true });
  unfetch(); restore();
});

test("health reports not-ok instead of throwing when the gateway is down", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch } = stubFetch(async () => { throw new Error("down"); });
  const res = mockRes();
  await healthHandler(
    mockReq({ method: "GET", cookie: await cookieFor("acme-2026") }), res);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(res.body, { ok: false });
  unfetch(); restore();
});

test("health never returns the gateway base or key", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch } = stubFetch(async () => ({ ok: true }));
  const res = mockRes();
  await healthHandler(
    mockReq({ method: "GET", cookie: await cookieFor("acme-2026") }), res);
  const serialized = JSON.stringify(res.body);
  assert.ok(!serialized.includes("super-secret-bearer"));
  assert.ok(!serialized.includes("gateway.test"));
  unfetch(); restore();
});

// ── /api/runs/[id]/events (Edge runtime) ──────────────────────────────

test("events refuses an unauthenticated caller", async () => {
  const restore = withEnv(BASE_ENV);
  const res = await eventsHandler(edgeReq());
  assert.equal(res.status, 401);
  restore();
});

test("events 500s on missing server config", async () => {
  const restore = withEnv({ ...BASE_ENV, HERMES_BASE: undefined });
  const res = await eventsHandler(edgeReq({ cookie: await cookieFor("acme-2026") }));
  assert.equal(res.status, 500);
  restore();
});

test("events streams the upstream body through", async () => {
  const restore = withEnv(BASE_ENV);
  const body = new ReadableStream({
    start(c) { c.enqueue(new TextEncoder().encode("data: {}\n\n")); c.close(); },
  });
  const { restore: unfetch, calls } = stubFetch(async () => ({
    status: 200, body, headers: { get: () => "text/event-stream" },
  }));
  const res = await eventsHandler(edgeReq({ cookie: await cookieFor("acme-2026") }));
  assert.equal(res.status, 200);
  assert.match(await res.text(), /data: \{\}/);
  assert.match(calls[0].url, /\/v1\/runs\/run_abc\/events$/);
  unfetch(); restore();
});

test("events returns 502 when the gateway is unreachable", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch } = stubFetch(async () => { throw new Error("nope"); });
  const res = await eventsHandler(edgeReq({ cookie: await cookieFor("acme-2026") }));
  assert.equal(res.status, 502);
  unfetch(); restore();
});

test("events sends the bearer key upstream only", async () => {
  const restore = withEnv(BASE_ENV);
  const { restore: unfetch, calls } = stubFetch(async () => ({
    status: 200, body: null, headers: { get: () => "text/event-stream" },
  }));
  const res = await eventsHandler(edgeReq({ cookie: await cookieFor("acme-2026") }));
  assert.match(calls[0].opts.headers.Authorization, /super-secret-bearer/);
  assert.ok(!JSON.stringify([...res.headers]).includes("super-secret-bearer"));
  unfetch(); restore();
});
