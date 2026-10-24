import { dateTime } from "../shared/dateTime.js";

export default function PushSettingsPage({ devices, loading, currentEndpointHash, removing, onRemove }) {
  return <section>
    <h2>Urządzenia z powiadomieniami push</h2>
    <p>Każda pozycja to przeglądarka lub urządzenie subskrybujące powiadomienia.</p>
    {loading ? <p>Ładowanie…</p> : devices.length === 0 ? <p>Brak aktywnych subskrypcji.</p> : <div className="list">
      {devices.map((device) => <article className="card" key={device.id}>
        <p><strong>{device.device_name || "Urządzenie bez nazwy"}</strong>{device.endpoint_hash === currentEndpointHash && <span className="device-current"> To urządzenie</span>}</p>
        <p className="muted device-agent">{device.user_agent || "Przeglądarka"}</p>
        <p className="muted">Dodano: {dateTime(device.created_at)}</p>
        <button type="button" className="secondary" onClick={() => onRemove(device.id)} disabled={removing === device.id}>{removing === device.id ? "Usuwanie…" : "Usuń urządzenie"}</button>
      </article>)}
    </div>}
    <p className="muted">Na iPhonie i iPadzie web push działa po dodaniu KiedyMam do ekranu początkowego (iOS/iPadOS 16.4 lub nowszy).</p>
  </section>;
}
