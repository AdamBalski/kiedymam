import { useState } from "react";
import { api, appPath } from "../api.js";

function applicationServerKey(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(value.replace(/-/g, "+").replace(/_/g, "/") + padding);
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

export default function usePushSubscription(setError, setSuccess) {
  const [subscribing, setSubscribing] = useState(false);

  async function subscribeToPush(deviceName, afterSubscribed) {
    setSubscribing(true);
    setError("");
    setSuccess("");
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        throw new Error("Ta przeglądarka nie obsługuje powiadomień push.");
      }
      if (window.matchMedia("(display-mode: standalone)").matches === false && /iPad|iPhone|iPod/.test(navigator.userAgent)) {
        throw new Error("Na iPhonie dodaj KiedyMam do ekranu początkowego i uruchom aplikację z ikony.");
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("Zezwól przeglądarce na powiadomienia, aby włączyć subskrypcję.");
      const config = await api.pushConfig();
      if (!config.enabled || !config.public_key) throw new Error("Powiadomienia push nie są jeszcze skonfigurowane na serwerze.");
      const registration = await navigator.serviceWorker.register(appPath("/service-worker.js"), { scope: appPath("/") });
      const ready = await navigator.serviceWorker.ready;
      const pushManager = ready.pushManager || registration.pushManager;
      let subscription = await pushManager.getSubscription();
      if (!subscription) {
        try {
          subscription = await pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: applicationServerKey(config.public_key),
          });
        } catch (error) {
          const reason = `${error?.name || ""} ${error?.message || ""}`.toLowerCase();
          if (reason.includes("aborterror") || reason.includes("push service error")) {
            throw new Error(
              "Przeglądarka nie mogła połączyć się ze swoją usługą powiadomień push. Może ona nie obsługiwać Web Push albo mieć wyłączoną usługę push. W Brave otwórz brave://settings/privacy, włącz „Use Google services for push messaging”, uruchom ponownie przeglądarkę i spróbuj ponownie.",
            );
          }
          throw error;
        }
      }
      await api.savePushSubscription(subscription.toJSON(), deviceName);
      setSuccess("To urządzenie będzie otrzymywać powiadomienia push.");
      await afterSubscribed();
    } catch (error) {
      setError(error.message);
    } finally {
      setSubscribing(false);
    }
  }

  return { subscribeToPush, subscribing };
}
