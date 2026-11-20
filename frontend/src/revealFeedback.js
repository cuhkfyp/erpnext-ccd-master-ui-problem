// Only feedback and the user's quota deadline live here, never revealed data.
export function createRevealFeedback(state, { now = Date.now, schedule = setInterval, cancel = clearInterval, onReset = () => {} } = {}) {
  let timer = null;
  function dispose() {
    if (timer !== null) cancel(timer);
    timer = null;
  }
  function update() {
    state.remainingSeconds = Math.max(0, Math.ceil((state.retryAt - now()) / 1000));
    if (state.retryAt && !state.remainingSeconds) {
      const previous = state.error;
      state.retryAt = 0;
      state.error = "";
      dispose();
      onReset(previous);
    }
  }
  function open() {
    update();
    if (!state.remainingSeconds) state.error = "";
  }
  function fail(error) {
    state.error = error.message || "The request could not be completed.";
    if (error.code === "RATE_LIMIT_EXCEEDED" && Number.isInteger(error.retryAfterSeconds)
        && error.retryAfterSeconds > 0 && error.retryAfterSeconds <= 3601) {
      dispose();
      state.retryAt = now() + error.retryAfterSeconds * 1000;
      update();
      timer = schedule(update, 1000);
    }
  }
  return { open, fail, update, dispose };
}
