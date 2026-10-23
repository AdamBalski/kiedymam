import { useEffect, useState } from "react";
import { api, appPath } from "./api.js";

const DEFAULT_URL = "https://www.umb.edu.pl/photo/pliki/administracja/sekcja_ds_planowania_i_rozliczania_obciazen_dydaktycznych/fizjoterapia/zima/2_fizjoterapia_zima_26.27_20.10.xlsx";

function dateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pl-PL", { dateStyle: "medium", timeStyle: "short" });
}

function Message({ error, success }) {
  if (error) return <div className="message error" role="alert">{error}</div>;
  if (success) return <div className="message success" role="status">{success}</div>;
  return null;
}

function LoginPage() {
  return (
    <section className="login-panel">
      <h2>Plan zajęć dla Fizjoterapii UMB</h2>
      <p>
        Zaloguj się przez Google. Przy pierwszym logowaniu konto zostanie
        utworzone automatycznie.
      </p>
      <a className="button primary" href={appPath("/api/auth/google")}>Zaloguj się przez Google</a>
      <p className="muted">
        Aplikacja utworzy osobny kalendarz na Twoim koncie Google. Nie zmienia
        Twojego głównego kalendarza.
      </p>
    </section>
  );
}

function SetupForm({ onCreated, setError }) {
  const [sourceUrl, setSourceUrl] = useState(DEFAULT_URL);
  const [groupNumber, setGroupNumber] = useState("1");
  const [saving, setSaving] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const integration = await api.createIntegration({ source_url: sourceUrl, group_number: Number(groupNumber) });
      onCreated(integration);
    } catch (error) {
      setError(error.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card">
      <h2>Ustaw integrację</h2>
      <p>
        Wklej bezpośredni link do pliku XLSX z planem i podaj numer swojej
        grupy 6-osobowej.
      </p>
      <form onSubmit={submit}>
        <label htmlFor="source-url">Link do planu UMB</label>
        <input id="source-url" type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} required />
        <label htmlFor="group-number">Numer grupy 6-osobowej</label>
        <input id="group-number" type="number" min="1" max="36" value={groupNumber} onChange={(event) => setGroupNumber(event.target.value)} required />
        <p className="muted">Numer grupy jest używany do odczytania odpowiednich kolumn z planu.</p>
        <button type="submit" disabled={saving}>{saving ? "Tworzenie kalendarza…" : "Utwórz kalendarz"}</button>
      </form>
    </section>
  );
}

function SchedulePage({ integration, onChanged, setError, setSuccess }) {
  const [groupNumber, setGroupNumber] = useState(String(integration.group_number));
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    setGroupNumber(String(integration.group_number));
  }, [integration.id, integration.group_number]);

  async function updateGroup(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const result = await api.updateGroup(integration.id, { group_number: Number(groupNumber) });
      onChanged(result);
      setSuccess("Zmieniono grupę i zaktualizowano kalendarz.");
    } catch (error) {
      setError(error.message);
      const current = await api.integrations().catch(() => []);
      if (current[0]) onChanged(current[0]);
    } finally {
      setSaving(false);
    }
  }

  async function syncNow() {
    setSyncing(true);
    setError("");
    setSuccess("");
    try {
      const result = await api.syncNow(integration.id);
      if (result.status === "skipped") {
        setSuccess(`Synchronizacja była niedawno uruchomiona. Spróbuj po ${dateTime(result.retry_at * 1000)}.`);
      } else {
        setSuccess("Plan został sprawdzony i kalendarz jest aktualny.");
        onChanged(await api.integrations().then((rows) => rows[0]));
      }
    } catch (error) {
      setError(error.message);
    } finally {
      setSyncing(false);
    }
  }

  async function deleteIntegration() {
    if (!window.confirm("Usunąć integrację i jej osobny kalendarz Google?")) return;
    setDeleting(true);
    setError("");
    try {
      const result = await api.deleteIntegration(integration.id);
      if (!result.calendar_deleted) setSuccess("Integracja została usunięta. Google nie pozwoliło usunąć kalendarza; usuń go ręcznie z Google Calendar.");
      onChanged(null);
    } catch (error) {
      setError(error.message);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="card">
      <h2>Twój kalendarz</h2>
      <p><strong>{integration.calendar_name}</strong></p>
      <p><a href={`https://calendar.google.com/calendar/u/0/r?cid=${encodeURIComponent(integration.calendar_id)}`} target="_blank" rel="noreferrer">Otwórz kalendarz Google</a></p>
      <p>Źródło: <a href={integration.source_url} target="_blank" rel="noreferrer">arkusz UMB</a></p>
      <p>Ostatnia synchronizacja: {dateTime(integration.last_sync_at)}</p>

      <div className="button-row">
        <button type="button" onClick={syncNow} disabled={syncing}>{syncing ? "Sprawdzanie…" : "Synchronizuj teraz"}</button>
        <button type="button" className="secondary" onClick={deleteIntegration} disabled={deleting}>{deleting ? "Usuwanie…" : "Usuń integrację"}</button>
      </div>

      <hr />
      <h3>Zmień grupę</h3>
      <p className="muted">Link do arkusza pozostaje stały. Aby użyć innego planu, usuń integrację i utwórz ją ponownie.</p>
      <form onSubmit={updateGroup} className="inline-form">
        <label htmlFor="change-group">Numer grupy 6-osobowej</label>
        <input id="change-group" type="number" min="1" max="36" value={groupNumber} onChange={(event) => setGroupNumber(event.target.value)} required />
        <button type="submit" disabled={saving}>{saving ? "Zapisywanie…" : "Zapisz grupę"}</button>
      </form>
    </section>
  );
}

function NotificationsPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");

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
      <div className="section-heading">
        <div><h2>Powiadomienia</h2><p>Zmiany planu z ostatnich siedmiu dni.</p></div>
        <button type="button" className="secondary" onClick={markAllRead}>Oznacz wszystkie jako przeczytane</button>
      </div>
      {error && <Message error={error} />}
      {loading ? <p>Ładowanie…</p> : items.length === 0 ? <p>Brak powiadomień.</p> : (
        <div className="list">
          {items.map((item) => (
            <article className={`card notice ${item.read_at ? "read" : "unread"}`} key={item.id}>
              <p>{item.message}</p>
              <p className="muted">{dateTime(item.created_at)}</p>
              {!item.read_at && <button type="button" onClick={() => markRead(item.id)}>Oznacz jako przeczytane</button>}
            </article>
          ))}
        </div>
      )}
      {!loading && hasMore && <button type="button" onClick={() => reload(true)} disabled={loadingMore}>{loadingMore ? "Ładowanie…" : "Załaduj starsze powiadomienia"}</button>}
    </section>
  );
}

function ChangelogPage() {
  const [calendars, setCalendars] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    api.changelog().then(setCalendars).catch((error) => setError(error.message)).finally(() => setLoading(false));
  }, []);

  return (
    <section>
      <h2>Dziennik zmian UMB</h2>
      <p>Informacje o aktualizacjach odczytane z komentarza w arkuszu.</p>
      {error && <Message error={error} />}
      {loading ? <p>Ładowanie…</p> : calendars.length === 0 ? <p>Brak dziennika zmian dla wybranego planu.</p> : calendars.map((calendar) => (
        <div className="card" key={calendar.source_url}>
          <h3>{calendar.calendar_name}</h3>
          {calendar.entries.length === 0 ? <p>W arkuszu nie ma opublikowanego dziennika zmian.</p> : (
            <div className="list">
              {calendar.entries.map((entry, index) => (
                <article className="change-entry" key={`${entry.date}-${index}`}>
                  <h4>{entry.date}</h4>
                  <ul>{entry.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>
                </article>
              ))}
            </div>
          )}
        </div>
      ))}
    </section>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [integration, setIntegration] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("plan");
  const [showSetup, setShowSetup] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const account = await api.me();
        setUser(account);
        const integrations = await api.integrations();
        setIntegration(integrations[0] || null);
      } catch (error) {
        if (error.status !== 401) setError(error.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function logout() {
    try {
      await api.logout();
      setUser(null);
      setIntegration(null);
      setTab("plan");
    } catch (error) {
      setError(error.message);
    }
  }

  if (loading) return <main><h1>KiedyMam</h1><p>Ładowanie…</p></main>;

  return (
    <main>
      <header className="site-header">
        <h1><span className="brand-mark">K</span> KiedyMam</h1>
        <p className="muted">Czytelny plan zajęć dla studentów Uniwersytetu Medycznego w Białymstoku.</p>
      </header>
      {error && <Message error={error} />}
      {success && <Message success={success} />}

      {!user ? <LoginPage /> : (
        <>
          <nav className="nav" aria-label="Główna nawigacja">
            <div className="nav-links">
              {[ ["plan", "Plan"], ["notifications", "Powiadomienia"], ["changelog", "Dziennik zmian"] ].map(([key, label]) => (
                <button type="button" className={tab === key ? "nav-active" : "nav-link"} key={key} onClick={() => { setTab(key); setError(""); setSuccess(""); }}>{label}</button>
              ))}
            </div>
            <span className="account-label">{user.email}</span>
            <button type="button" className="nav-logout" onClick={logout}>Wyloguj</button>
          </nav>
          {tab === "plan" && (
            <section>
              <h2>Twój plan</h2>
              {integration ? (
                <SchedulePage integration={integration} onChanged={setIntegration} setError={setError} setSuccess={setSuccess} />
              ) : showSetup ? (
                <SetupForm onCreated={(created) => { setIntegration(created); setShowSetup(false); setSuccess("Utworzono kalendarz z Twoim planem."); }} setError={setError} />
              ) : (
                <div className="card">
                  <h3>Nie masz jeszcze skonfigurowanego planu</h3>
                  <p>Podaj link do arkusza UMB i numer grupy 6-osobowej. Utworzymy osobny kalendarz Google i uzupełnimy go zajęciami.</p>
                  <button type="button" onClick={() => setShowSetup(true)}>Ustaw integrację</button>
                </div>
              )}
            </section>
          )}
          {tab === "notifications" && <NotificationsPage />}
          {tab === "changelog" && <ChangelogPage />}
        </>
      )}
      <footer className="site-footer">
        <p>Plany pochodzą z oficjalnej strony UMB. Zmiany sprawdzane są co 30 minut.</p>
        <p><a href={appPath("/privacy")}>Polityka prywatności</a> · <a href={appPath("/terms")}>Warunki korzystania</a></p>
      </footer>
    </main>
  );
}
