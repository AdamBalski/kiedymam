function csrfToken() {
  const item = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("umb_csrf="));
  return item ? decodeURIComponent(item.slice("umb_csrf=".length)) : "";
}

export function appPath(path) {
  const basePath = window.UMB_BASE_PATH || "";
  return `${basePath}${path}`;
}

async function request(path, options = {}) {
  const method = options.method || "GET";
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    headers.set("X-CSRF-Token", csrfToken());
  }
  const response = await fetch(appPath(path), {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const result = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(result?.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return result;
}

export const api = {
  me: () => request("/api/auth/me"),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  integrations: () => request("/api/integrations"),
  createIntegration: (body) => request("/api/integrations", { method: "POST", body }),
  updateGroup: (id, body) => request(`/api/integrations/${encodeURIComponent(id)}`, { method: "PATCH", body }),
  syncNow: (id) => request(`/api/integrations/${encodeURIComponent(id)}/sync`, { method: "POST" }),
  deleteIntegration: (id) => request(`/api/integrations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  notifications: ({ limit = 100, offset = 0 } = {}) => request(`/api/notifications?limit=${limit}&offset=${offset}`),
  markRead: (id) => request(`/api/notifications/${encodeURIComponent(id)}/read`, { method: "POST" }),
  markAllRead: () => request("/api/notifications/read-all", { method: "POST" }),
  changelog: () => request("/api/changelog"),
};
