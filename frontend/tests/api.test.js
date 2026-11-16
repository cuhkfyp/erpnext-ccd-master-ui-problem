import assert from "node:assert/strict";
import test from "node:test";

import { call } from "../src/api.js";

global.window = { csrf_token: "test-token" };

test("a browser request deadline is explicit and does not return a stale result", async () => {
  const originalFetch = global.fetch;
  global.fetch = (_url, options) =>
    new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    });
  try {
    await assert.rejects(
      call("ccd_portal.api.search", {}, true, {
        timeoutMs: 5,
        timeoutMessage: "Search request timed out.",
      }),
      (error) => error.code === "REQUEST_TIMEOUT" && error.message === "Search request timed out.",
    );
  } finally {
    global.fetch = originalFetch;
  }
});

test("a gateway timeout is distinguished from an ordinary server error", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => ({
    ok: false,
    status: 504,
    json: async () => ({}),
  });
  try {
    await assert.rejects(
      call("ccd_portal.api.search"),
      (error) => error.code === "REQUEST_TIMEOUT" && error.status === 504,
    );
  } finally {
    global.fetch = originalFetch;
  }
});

for (const [type, code, message] of [
  ["SearchProcessingLimitError", "SEARCH_PROCESSING_LIMIT", "Search processing limit reached. Add a more specific criterion or retry shortly."],
  ["RateLimitExceededError", "RATE_LIMIT_EXCEEDED", "Too many requests. Try again later."],
]) {
  test(`${type} has a public explanation even without server messages`, async () => {
    const originalFetch = global.fetch;
    global.fetch = async () => ({ ok: false, status: 417, json: async () => ({ exc_type: type }) });
    try {
      await assert.rejects(call("ccd_portal.api.search"), (error) => error.code === code && error.message === message);
    } finally {
      global.fetch = originalFetch;
    }
  });
}

test("a serialized server message reaches the user without exposing a traceback", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => ({
    ok: false, status: 417,
    json: async () => ({
      exc_type: "SearchProcessingLimitError",
      exception: "Traceback: private server information",
      _server_messages: JSON.stringify([JSON.stringify({ message: "Search processing limit reached. Retry shortly." })]),
    }),
  });
  try {
    await assert.rejects(call("ccd_portal.api.search"), (error) => error.code === "SEARCH_PROCESSING_LIMIT" && error.message === "Search processing limit reached. Retry shortly.");
  } finally { global.fetch = originalFetch; }
});

test("unknown server exceptions remain generic", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => ({ ok: false, status: 500, json: async () => ({ exc_type: "InternalError", exception: "Private database contents" }) });
  try {
    await assert.rejects(call("ccd_portal.api.search"), (error) => error.message === "The request could not be completed.");
  } finally { global.fetch = originalFetch; }
});
