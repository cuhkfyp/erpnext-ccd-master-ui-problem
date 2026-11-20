import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRevealFeedback } from "../src/revealFeedback.js";

function fixture() {
  let now = 1000, tick, cancelled = 0, reset;
  const state = { error: "", retryAt: 0, remainingSeconds: 0 };
  const controller = createRevealFeedback(state, {
    now: () => now,
    schedule: (callback) => { tick = callback; return 1; },
    cancel: () => { cancelled++; },
    onReset: (previous) => { reset = previous; },
  });
  return { state, controller, advance(ms) { now += ms; tick?.(); }, get cancelled() { return cancelled; }, get reset() { return reset; } };
}

test("quota error survives reopening; expiry re-enables without sending any request", () => {
  const f = fixture();
  f.controller.fail({code: "RATE_LIMIT_EXCEEDED", message: "Hourly reveal limit reached.", retryAfterSeconds: 120});
  assert.equal(f.state.remainingSeconds, 120);
  f.advance(30000);
  f.controller.open();
  assert.equal(f.state.remainingSeconds, 90);
  assert.equal(f.state.error, "Hourly reveal limit reached.");
  // Wall-clock time, not ticks, also handles a background/suspended tab.
  f.advance(100000);
  assert.equal(f.state.remainingSeconds, 0);
  assert.equal(f.state.retryAt, 0);
  assert.equal(f.state.error, "");
  assert.equal(f.reset, "Hourly reveal limit reached.");
  assert.equal(f.cancelled, 1);
});

test("validation and network errors display without inventing a quota lockout", () => {
  const f = fixture();
  f.controller.fail(new Error("Choose an approved reason."));
  assert.equal(f.state.error, "Choose an approved reason.");
  assert.equal(f.state.remainingSeconds, 0);
  f.controller.open();
  assert.equal(f.state.error, "");
});

test("missing or invalid retry metadata never fabricates an hour-long block", () => {
  for (const value of [undefined, null, -1, 0, "120", 3602, Infinity]) {
    const f = fixture();
    f.controller.fail({code:"RATE_LIMIT_EXCEEDED", message:"Wait for reset.", retryAfterSeconds:value});
    assert.equal(f.state.remainingSeconds, 0);
    assert.equal(f.state.error, "Wait for reset.");
  }
});

test("unmount disposes the countdown timer", () => {
  const f = fixture();
  f.controller.fail({code:"RATE_LIMIT_EXCEEDED", message:"Wait.", retryAfterSeconds:20});
  f.controller.dispose();
  f.controller.dispose();
  assert.equal(f.cancelled, 1);
});

test("reveal dialog renders its own alert and guards both button and submission", () => {
  const app = readFileSync(new URL("../src/App.vue", import.meta.url), "utf8");
  const dialog = app.slice(app.indexOf('<div v-if="showReveal"'), app.indexOf('<div v-if="showCorrection'));
  assert.match(dialog, /revealFeedback.error[\s\S]*role="alert"/);
  assert.match(dialog, /:disabled="busy \|\| revealFeedback.remainingSeconds > 0"/);
  assert.match(app, /if\(busy.value \|\| revealFeedback.remainingSeconds > 0 \|\| !selected.value\)return/);
  assert.match(app, /setError\(e\);revealFeedbackController.fail\(e\)/);
  assert.match(app, /!showReveal.value \|\| document.hidden \|\| selected.value\?\.id!==recordId/);
});
