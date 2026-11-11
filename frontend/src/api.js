function safeMessage(value, fallback) {
  if (typeof value !== "string" || !value.trim() || value.length > 500) return fallback;
  if (/[<>]/.test(value) || /(?:Traceback|Failed to get method|ccd_portal\.|frappe\.)/i.test(value)) return fallback;
  return value;
}

function messageFrom(error, fallback) {
  try {
    const messages = JSON.parse(error?._server_messages || "[]");
    if (messages.length) return safeMessage(JSON.parse(messages[0]).message, fallback);
  } catch (_) {
    // Return the allowlisted fallback below.
  }
  return safeMessage(error?.message, fallback);
}

export async function call(method, args = {}, post = true, requestOptions = {}) {
  const timeoutMs = Number(requestOptions.timeoutMs || 0);
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  const options = {
    method: post ? "POST" : "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
    cache: "no-store",
  };
  if (controller) options.signal = controller.signal;
  let url = `/api/method/${method}`;
  if (post) {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Frappe-CSRF-Token"] = window.csrf_token || "";
    options.body = JSON.stringify(args);
  } else if (Object.keys(args).length) {
    url += `?${new URLSearchParams(args)}`;
  }
  try {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.exc_type) {
      const gatewayTimeout = response.status === 408 || response.status === 504;
      const fallback = gatewayTimeout
        ? "The server timed out before returning a result. No result was applied; retry or narrow the criteria."
        : "The request could not be completed.";
      const error = new Error(gatewayTimeout ? fallback : messageFrom(body, fallback));
      error.status = response.status;
      if (gatewayTimeout) error.code = "REQUEST_TIMEOUT";
      throw error;
    }
    return body.message;
  } catch (error) {
    if (error?.name === "AbortError") {
      const timeoutError = new Error(
        requestOptions.timeoutMessage ||
          "The portal did not receive a response before its request deadline. No result was applied."
      );
      timeoutError.code = "REQUEST_TIMEOUT";
      timeoutError.status = 408;
      throw timeoutError;
    }
    throw error;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}
