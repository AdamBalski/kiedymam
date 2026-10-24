self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = { body: event.data?.text() || "" }; }
  event.waitUntil(self.registration.showNotification(payload.title || "KiedyMam", {
    body: payload.body || "Twój plan zajęć się zmienił.",
    icon: new URL("kiedymam-icon.svg", self.registration.scope).href,
    data: { url: payload.url || "/?tab=notifications" },
    tag: payload.id ? `kiedymam-${payload.id}` : undefined,
    renotify: true,
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/?tab=notifications", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
    for (const client of clients) {
      if (new URL(client.url).origin === self.location.origin) {
        return client.navigate(target).then(() => client.focus());
      }
    }
    return self.clients.openWindow(target);
  }));
});
