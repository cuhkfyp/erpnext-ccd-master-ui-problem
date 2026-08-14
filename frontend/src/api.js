function messageFrom(error, fallback) {
  try {
    const messages = JSON.parse(error?._server_messages || "[]");
    if (messages.length) return JSON.parse(messages[0]).message || fallback;
  } catch (_) {
    // Return the allowlisted fallback below.
  }
  return error?.message || fallback;
}

export async function call(method, args = {}, post = true) {
  const options = {
    method: post ? "POST" : "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
    cache: "no-store",
  };
  let url = `/api/method/${method}`;
  if (post) {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Frappe-CSRF-Token"] = window.csrf_token || "";
    options.body = JSON.stringify(args);
  } else if (Object.keys(args).length) {
    url += `?${new URLSearchParams(args)}`;
  }
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.exc_type) {
    const error = new Error(messageFrom(body, "The request could not be completed."));
    error.status = response.status;
    throw error;
  }
  return body.message;
}
