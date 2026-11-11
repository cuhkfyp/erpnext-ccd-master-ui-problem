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
