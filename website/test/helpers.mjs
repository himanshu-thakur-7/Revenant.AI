// Shared harness for the website/api handler tests.
//
// These run on node's built-in test runner (`node --test`) so the web layer
// gains real coverage without adding a dependency to a project that currently
// has none. Run with: npm test  (from website/), or `make test-web`.
//
// The handlers are written against two different runtimes — Node-style
// (req/res) for auth/runs/health, and Edge-style (Request -> Response) for the
// SSE proxy. Both shapes are modelled here so each handler is exercised the
// way Vercel actually calls it, rather than through a shim that could hide a
// runtime-specific bug.

export const SECRET = "test-secret-do-not-use-in-prod";

/** Set the env a handler expects. Returns a restore() to put it back. */
export function withEnv(vars) {
  const prev = {};
  for (const [k, v] of Object.entries(vars)) {
    prev[k] = process.env[k];
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  return () => {
    for (const [k, v] of Object.entries(prev)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  };
}

/** Minimal Node-style res that records what the handler did. */
export function mockRes() {
  return {
    statusCode: null,
    headers: {},
    body: undefined,
    status(code) { this.statusCode = code; return this; },
    json(obj) { this.body = obj; return this; },
    send(text) { this.body = text; return this; },
    setHeader(k, v) { this.headers[k.toLowerCase()] = v; return this; },
    getHeader(k) { return this.headers[k.toLowerCase()]; },
  };
}

export function mockReq({ method = "POST", cookie, body, url = "/" } = {}) {
  return { method, url, headers: cookie ? { cookie } : {}, body };
}

/** Edge-style Request for the SSE handler. */
export function edgeReq({ cookie, url = "https://x/api/runs/run_abc/events" } = {}) {
  const headers = new Headers();
  if (cookie) headers.set("cookie", cookie);
  return new Request(url, { headers });
}

/**
 * Replace global fetch for one test. `impl` receives (url, opts).
 * Returns { restore, calls } so a test can assert on what was sent upstream —
 * which is how we check the bearer key never leaks and the tenant is injected.
 */
export function stubFetch(impl) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), opts });
    return impl(url, opts);
  };
  return { restore: () => { globalThis.fetch = original; }, calls };
}

/** A plausible upstream JSON response. */
export function jsonResponse(obj, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => JSON.stringify(obj),
    body: null,
    headers: { get: (k) => (k.toLowerCase() === "content-type" ? "application/json" : null) },
  };
}

/** Extract just the cookie value from a Set-Cookie header. */
export function cookieValueFrom(setCookieHeader) {
  return String(setCookieHeader).split(";")[0];
}
