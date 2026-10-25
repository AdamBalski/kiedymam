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
  timetableLinks: () => request("/api/timetable-links"),
  createIntegration: (body) => request("/api/integrations", { method: "POST", body }),
  workbookInfo: (sourceUrl) => request(`/api/integrations/workbook-info?source_url=${encodeURIComponent(sourceUrl)}`),
  updateGroup: (id, body) => request(`/api/integrations/${encodeURIComponent(id)}`, { method: "PATCH", body }),
  syncNow: (id) => request(`/api/integrations/${encodeURIComponent(id)}/sync`, { method: "POST" }),
  deleteIntegration: (id) => request(`/api/integrations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  notifications: ({ limit = 100, offset = 0 } = {}) => request(`/api/notifications?limit=${limit}&offset=${offset}`),
  pushConfig: () => request("/api/push/config"),
  pushSubscriptions: () => request("/api/push/subscriptions"),
  savePushSubscription: (subscription, deviceName) => request("/api/push/subscriptions", {
    method: "POST", body: { ...subscription, device_name: deviceName },
  }),
  deletePushSubscription: (id) => request(`/api/push/subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  markRead: (id) => request(`/api/notifications/${encodeURIComponent(id)}/read`, { method: "POST" }),
  markAllRead: () => request("/api/notifications/read-all", { method: "POST" }),
  feedback: () => request("/api/feedback"),
  saveFeedback: (body) => request("/api/feedback", { method: "PUT", body: { body } }),
  deleteFeedback: () => request("/api/feedback", { method: "DELETE" }),
  changelog: () => request("/api/changelog"),
  metricCatalog: () => request("/api/admin/metrics/catalog"),
  queryMetrics: (body) => request("/api/admin/metrics/query", { method: "POST", body }),
  sendTestPush: (body) => request("/api/admin/send-push", { method: "POST", body }),
};
