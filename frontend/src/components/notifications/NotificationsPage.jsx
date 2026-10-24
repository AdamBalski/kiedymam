import { useEffect, useState } from "react";
import { api, appPath } from "../../api.js";
import { dateTime } from "../shared/dateTime.js";
import { Message } from "../shared/Message.jsx";
import PushDevicesPanel from "./PushDevicesPanel.jsx";
import usePushSubscription from "../../hooks/usePushSubscription.js";

function endpointHash(endpoint) {
  return window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint)).then((digest) =>
    Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(""),
  );
}

function triggerLabel(value) {
  return ({ scheduled: "Kontrola automatyczna", manual: "Synchronizacja ręczna",
    source_changed: "Aktualizacja planu UMB", integration_created: "Utworzenie integracji",
    integration_changed: "Zmiana ustawień", integration_deleted: "Usunięcie integracji" })[value] || value;
}

export default function NotificationsPage({ setError: setGlobalError, setSuccess }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [innerTab, setInnerTab] = useState("notifications");
  const [devices, setDevices] = useState([]);
  const [devicesLoading, setDevicesLoading] = useState(true);
  const [currentEndpointHash, setCurrentEndpointHash] = useState("");
  const [deviceName, setDeviceName] = useState("Moje urządzenie");
  const [removingDevice, setRemovingDevice] = useState("");
  const { subscribeToPush, subscribing } = usePushSubscription(setGlobalError, setSuccess);

  async function reload(append = false) {
    const offset = append ? items.length : 0;
    if (append) setLoadingMore(true);
    try {
      const page = await api.notifications({ limit: 100, offset });
      setItems((current) => append ? [...current, ...page.items] : page.items);
      setHasMore(page.has_more);
      setError("");
    } catch (error) {
      setError(error.message);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }

  useEffect(() => { reload(); }, []);

  async function reloadDevices() {
    setDevicesLoading(true);
    try {
      const records = await api.pushSubscriptions();
      setDevices(records);
      const registration = await navigator.serviceWorker?.getRegistration(appPath("/"));
      const subscription = await registration?.pushManager.getSubscription();
      const hash = subscription ? await endpointHash(subscription.endpoint) : "";
      setCurrentEndpointHash(hash);
      const current = records.find((device) => device.endpoint_hash === hash);
      if (current?.device_name) setDeviceName(current.device_name);
    } catch (error) {
      setError(error.message);
    } finally {
      setDevicesLoading(false);
    }
  }

  useEffect(() => { reloadDevices(); }, []);

  const currentDevice = devices.find((device) => device.endpoint_hash === currentEndpointHash);

  async function togglePush() {
    if (!currentDevice) {
      if (!deviceName.trim()) {
        setGlobalError("Wpisz nazwę tego urządzenia.");
        return;
      }
      await subscribeToPush(deviceName.trim(), reloadDevices);
      return;
    }

    setRemovingDevice(currentDevice.id);
    setGlobalError("");
    try {
      await api.deletePushSubscription(currentDevice.id);
      try {
        const registration = await navigator.serviceWorker?.getRegistration(appPath("/"));
        const subscription = await registration?.pushManager.getSubscription();
        if (subscription && await endpointHash(subscription.endpoint) === currentEndpointHash) {
          await subscription.unsubscribe();
        }
      } catch { /* The server-side subscription has already been removed. */ }
      await reloadDevices();
      setSuccess("To urządzenie nie będzie już otrzymywać powiadomień push.");
    } catch (error) {
      setGlobalError(error.message);
    } finally {
      setRemovingDevice("");
    }
  }

  async function removeDevice(id) {
    setRemovingDevice(id);
    setGlobalError("");
    try {
      const device = devices.find((item) => item.id === id);
      await api.deletePushSubscription(id);
      try {
        const registration = await navigator.serviceWorker?.getRegistration(appPath("/"));
        const subscription = await registration?.pushManager.getSubscription();
        if (subscription && device?.endpoint_hash === await endpointHash(subscription.endpoint)) {
          await subscription.unsubscribe();
        }
      } catch { /* The server-side subscription has already been removed. */ }
      await reloadDevices();
      setSuccess("Usunięto subskrypcję tego urządzenia.");
    } catch (error) {
      setGlobalError(error.message);
    } finally {
      setRemovingDevice("");
    }
  }

  async function markRead(id) {
    try {
      await api.markRead(id);
      await reload();
    } catch (error) {
      setError(error.message);
    }
  }

  async function markAllRead() {
    try {
      await api.markAllRead();
      await reload();
    } catch (error) {
      setError(error.message);
    }
  }

  return (
    <section>
      <div className="nav-links inner-tabs" role="tablist" aria-label="Powiadomienia i urządzenia">
        <button type="button" role="tab" aria-selected={innerTab === "notifications"} className={innerTab === "notifications" ? "nav-active" : "nav-link"} onClick={() => setInnerTab("notifications")}>Powiadomienia</button>
        <button type="button" role="tab" aria-selected={innerTab === "devices"} className={innerTab === "devices" ? "nav-active" : "nav-link"} onClick={() => setInnerTab("devices")}>Urządzenia push</button>
      </div>
      {error && <Message error={error} />}
      {innerTab === "notifications" ? <>
        <div className="section-heading">
          <div><h2>Powiadomienia</h2><p>Zmiany planu z ostatnich siedmiu dni.</p></div>
          <button type="button" className="secondary" onClick={markAllRead}>Oznacz wszystkie jako przeczytane</button>
        </div>
        <div className="card push-status">
          {devicesLoading ? <p>Sprawdzanie tego urządzenia…</p> : currentDevice ? <>
            <p><strong>To urządzenie subskrybuje powiadomienia push</strong></p>
            <p className="muted">Nazwa: {currentDevice.device_name || "Urządzenie bez nazwy"}</p>
            <button type="button" className="secondary" onClick={togglePush} disabled={Boolean(removingDevice)}>
              {removingDevice === currentDevice.id ? "Wyłączanie…" : "Przestań subskrybować powiadomienia push"}
            </button>
          </> : <>
            <p><strong>To urządzenie nie subskrybuje powiadomień push.</strong></p>
            <label htmlFor="push-device-name">Nazwa tego urządzenia</label>
            <input id="push-device-name" value={deviceName} maxLength={80} onChange={(event) => setDeviceName(event.target.value)} />
            <button type="button" onClick={togglePush} disabled={subscribing || Boolean(removingDevice)}>
              {subscribing ? "Włączanie…" : "Subskrybuj powiadomienia push"}
            </button>
          </>}
        </div>
        {loading ? <p>Ładowanie…</p> : items.length === 0 ? <p>Brak powiadomień.</p> : (
        <div className="list">
          {items.map((item) => (
            <article className={`card notice ${item.read_at ? "read" : "unread"}`} key={item.id}>
              <p>{item.message}</p>
              {item.sync_trigger && <p className="muted">Powód: {triggerLabel(item.sync_trigger)}</p>}
              <p className="muted">{dateTime(item.created_at)}</p>
              {!item.read_at && <button type="button" onClick={() => markRead(item.id)}>Oznacz jako przeczytane</button>}
            </article>
          ))}
        </div>
        )}
        {!loading && hasMore && <button type="button" onClick={() => reload(true)} disabled={loadingMore}>{loadingMore ? "Ładowanie…" : "Załaduj starsze powiadomienia"}</button>}
      </> : <PushDevicesPanel devices={devices} loading={devicesLoading} currentEndpointHash={currentEndpointHash} removing={removingDevice} onRemove={removeDevice} />}
    </section>
  );
}
