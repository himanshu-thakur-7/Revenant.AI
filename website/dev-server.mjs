#!/usr/bin/env node
// Local dev server for website/ — serves the static site AND mounts the
// same api/*.mjs handlers Vercel would run, with zero Vercel CLI / auth
// dependency. Good enough to develop and smoke-test the console + proxy +
// invite gate end to end against a real (or tunneled) Hermes gateway.
//
// Usage:  node website/dev-server.mjs [port]     (default port 8790)
// Reads website/.env.local if present (see .env.local.example).

import { createServer } from "node:http";
import { readFileSync, existsSync, createReadStream, statSync } from "node:fs";
import { join, extname, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.argv[2]) || 8790;

// ---- load .env.local (KEY=VALUE, # comments, no quoting needed here) ----
const envPath = join(ROOT, ".env.local");
if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
  }
}
for (const k of ["HERMES_BASE", "HERMES_API_KEY", "INVITE_CODES", "SESSION_SECRET"]) {
  if (!process.env[k]) console.warn(`[dev-server] warning: ${k} is not set`);
}

const authHandler = (await import("./api/auth.mjs")).default;
const runsHandler = (await import("./api/runs.mjs")).default;
const healthHandler = (await import("./api/health.mjs")).default;
const eventsHandler = (await import("./api/runs/[id]/events.mjs")).default;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => resolve(data));
  });
}

// Adapts our Vercel-Node-style handlers (req, res) => {status,json,send,setHeader}
// onto a real http.IncomingMessage/ServerResponse.
async function callNodeHandler(handler, req, res) {
  let body;
  if (req.method === "POST" || req.method === "PUT") {
    const raw = await readBody(req);
    try { body = raw ? JSON.parse(raw) : {}; } catch { body = raw; }
  }
  const vres = {
    _status: 200,
    status(c) { this._status = c; return this; },
    setHeader(k, v) { res.setHeader(k, v); return this; },
    json(o) { res.statusCode = this._status; res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(o)); },
    send(t) { res.statusCode = this._status; res.end(t); },
  };
  await handler({ method: req.method, headers: req.headers, body }, vres);
}

// Adapts our Edge-style handler (Request) => Response onto a real
// http.ServerResponse, streaming the body through as it arrives.
async function callEdgeHandler(handler, req, res, fullUrl) {
  const webReq = new Request(fullUrl, { headers: req.headers });
  const webRes = await handler(webReq);
  res.statusCode = webRes.status;
  for (const [k, v] of webRes.headers.entries()) res.setHeader(k, v);
  if (!webRes.body) { res.end(); return; }
  const reader = webRes.body.getReader();
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    res.write(value);
  }
  res.end();
}

function serveStatic(req, res) {
  let path = decodeURIComponent(req.url.split("?")[0]);
  if (path === "/") path = "/index.html";
  const full = join(ROOT, path);
  if (!full.startsWith(ROOT) || !existsSync(full) || statSync(full).isDirectory()) {
    res.statusCode = 404;
    res.end("not found");
    return;
  }
  res.setHeader("Content-Type", MIME[extname(full)] || "application/octet-stream");
  createReadStream(full).pipe(res);
}

const server = createServer(async (req, res) => {
  const url = req.url.split("?")[0];
  try {
    if (url === "/api/auth") return await callNodeHandler(authHandler, req, res);
    if (url === "/api/runs") return await callNodeHandler(runsHandler, req, res);
    if (url === "/api/health") return await callNodeHandler(healthHandler, req, res);
    const m = url.match(/^\/api\/runs\/([^/]+)\/events$/);
    if (m) return await callEdgeHandler(eventsHandler, req, res, `http://localhost${req.url}`);
    return serveStatic(req, res);
  } catch (err) {
    console.error(err);
    res.statusCode = 500;
    res.end("dev-server error: " + String(err?.message || err));
  }
});

server.listen(PORT, () => {
  console.log(`[dev-server] http://127.0.0.1:${PORT}  (console: /console.html)`);
});
