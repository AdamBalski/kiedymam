import { useEffect, useState } from "react";
import { api, appPath } from "./api.js";

function dateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pl-PL", { dateStyle: "medium", timeStyle: "short" });
}

function Message({ error, success }) {
  if (error) return <div className="message error" role="alert">{error}</div>;
  if (success) return <div className="message success" role="status">{success}</div>;
  return null;
}

function applicationServerKey(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(value.replace(/-/g, "+").replace(/_/g, "/") + padding);
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

async function endpointHash(endpoint) {
  const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function triggerLabel(value) {
  return ({ scheduled: "Kontrola automatyczna", manual: "Synchronizacja ręczna",
    source_changed: "Aktualizacja planu UMB", integration_created: "Utworzenie integracji",
    integration_changed: "Zmiana ustawień", integration_deleted: "Usunięcie integracji" })[value] || value;
}

function LoginPage() {
  return (
    <section className="login-panel">
      <h2>Plan zajęć UMB</h2>
      <p>
        Zaloguj się przez Google. Przy pierwszym logowaniu konto zostanie
        utworzone automatycznie.
      </p>
      <p className="muted">
        Do utworzenia osobnego kalendarza wymagana jest zgoda Google na tworzenie
        kalendarzy. Jeśli jej nie udzielisz, logowanie nie zostanie zakończone.
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
  const [catalog, setCatalog] = useState([]);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [selectedPageUrl, setSelectedPageUrl] = useState("");
  const [selectedUrl, setSelectedUrl] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [useCustomLink, setUseCustomLink] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [groupNumber, setGroupNumber] = useState("");
  const [calendarName, setCalendarName] = useState("");
  const [workbook, setWorkbook] = useState(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const sourceUrl = (useCustomLink ? customUrl : selectedUrl).trim();

  useEffect(() => {
    api.timetableLinks()
      .then((links) => {
        setCatalog(links);
        setSelectedPageUrl(links[0]?.page_url || "");
        setSelectedUrl(links[0]?.url || "");
      })
      .catch(() => setCatalog([]))
      .finally(() => setCatalogLoading(false));
  }, []);

  const timetablePages = [...new Map(
    catalog.map((link) => [link.page_url, { page_url: link.page_url, programme: link.programme }]),
  ).values()];
  const linksOnPage = catalog.filter((link) => link.page_url === selectedPageUrl);
  const visibleLinks = linksOnPage.filter((link) =>
    `${link.href_label} ${link.url}`.toLocaleLowerCase().includes(catalogSearch.toLocaleLowerCase()),
  );

  async function inspectWorkbook() {
    if (!sourceUrl) return setError(useCustomLink ? "Wpisz bezpośredni link do pliku XLSX." : "Wybierz plan z listy.");
    setChecking(true);
    setWorkbook(null);
    setError("");
    try {
      const details = await api.workbookInfo(sourceUrl);
      setWorkbook(details);
      setGroupNumber(details.grouping_mode === "all" ? "0" : String(details.available_groups[0] ?? ""));
    } catch (error) {
      setError(error.message);
    } finally {
      setChecking(false);
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (!sourceUrl) {
      setError(useCustomLink ? "Wpisz bezpośredni link do pliku XLSX." : "Wybierz plan z listy.");
      return;
    }
    if (workbook?.source_url !== sourceUrl) {
      setError("Najpierw sprawdź plan, aby odczytać dostępne grupy.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const integration = await api.createIntegration({
        source_url: sourceUrl,
        group_number: Number(groupNumber),
        ...(calendarName.trim() ? { calendar_name: calendarName.trim() } : {}),
      });
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
        Wybierz plan z listy albo użyj własnego bezpośredniego linku do pliku XLSX.
        Po sprawdzeniu arkusza pokażemy grupy wykryte w tym planie.
      </p>
      <form onSubmit={submit}>
        <label className="checkbox-option" htmlFor="use-custom-link">
          <input
            id="use-custom-link"
            type="checkbox"
            checked={useCustomLink}
            onChange={(event) => { setUseCustomLink(event.target.checked); setWorkbook(null); setGroupNumber(""); }}
          />
          Użyj własnego linku zamiast listy planów
        </label>
        {useCustomLink ? (
          <>
            <label htmlFor="custom-source-url">Bezpośredni link do pliku XLSX</label>
            <input
              id="custom-source-url"
              type="url"
              value={customUrl}
              onChange={(event) => { setCustomUrl(event.target.value); setWorkbook(null); setGroupNumber(""); }}
              placeholder="https://www.umb.edu.pl/.../plan.xlsx"
              required
            />
          </>
        ) : (
          <>
            <label htmlFor="catalog-page">Kierunek i strona z planami</label>
            <select
              id="catalog-page"
              value={selectedPageUrl}
              onChange={(event) => {
                const pageUrl = event.target.value;
                setSelectedPageUrl(pageUrl);
                setCatalogSearch("");
                setSelectedUrl(catalog.find((link) => link.page_url === pageUrl)?.url || "");
                setWorkbook(null);
                setGroupNumber("");
              }}
              required
              disabled={catalogLoading || timetablePages.length === 0}
            >
              <option value="" disabled>
                {catalogLoading ? "Ładowanie stron…" : "Wybierz kierunek"}
              </option>
              {timetablePages.map((page) => (
                <option key={page.page_url} value={page.page_url}>{page.programme}</option>
              ))}
            </select>
            <label htmlFor="catalog-search">Szukaj planu na tej stronie</label>
            <input
              id="catalog-search"
              type="search"
              value={catalogSearch}
              onChange={(event) => {
                setCatalogSearch(event.target.value);
                setSelectedUrl("");
                setWorkbook(null);
                setGroupNumber("");
              }}
              placeholder="Rok lub nazwa linku na stronie"
            />
            <label htmlFor="catalog-plan">Plan (nazwa linku na stronie UMB)</label>
            <select
              id="catalog-plan"
              value={selectedUrl}
              onChange={(event) => { setSelectedUrl(event.target.value); setWorkbook(null); setGroupNumber(""); }}
              required
              disabled={catalogLoading || visibleLinks.length === 0}
            >
              <option value="" disabled>
                {catalogLoading ? "Ładowanie planów…" : visibleLinks.length ? "Wybierz plan" : "Brak pasujących planów"}
              </option>
              {visibleLinks.map((link) => (
                <option key={link.url} value={link.url}>
                  {link.href_label}
                </option>
              ))}
            </select>
            {!catalogLoading && catalog.length === 0 && (
              <p className="muted">Lista planów jest pusta. Możesz użyć własnego linku albo administrator może skonfigurować strony UMB na serwerze.</p>
            )}
            {catalog.length > 0 && visibleLinks.length === 0 && (
              <p className="muted">Brak wyników wyszukiwania. Zmień frazę albo zaznacz opcję własnego linku.</p>
            )}
          </>
        )}
        <button type="button" className="secondary" onClick={inspectWorkbook} disabled={checking || !sourceUrl}>
          {checking ? "Odczytywanie arkusza…" : "Sprawdź plan i dostępne grupy"}
        </button>
        {workbook?.source_url === sourceUrl && <>
          {workbook.grouping_mode === "groups" ? <>
            <label htmlFor="group-number">Twoja grupa</label>
            <select id="group-number" value={groupNumber} onChange={(event) => setGroupNumber(event.target.value)} required>
              {workbook.available_groups.map((group) => <option key={group} value={group}>Grupa {group}</option>)}
            </select>
            <p className="muted">Grupy wykryte w arkuszu. Zajęcia wspólne dla kilku grup zostaną dodane automatycznie.</p>
          </> : workbook.grouping_mode === "all" ? <p className="muted">Ten plan nie jest podzielony na grupy. Kalendarz będzie zawierał wszystkie zajęcia.</p> : <>
            <label htmlFor="group-number">Numer grupy</label>
            <input id="group-number" type="number" min="1" max="999" value={groupNumber} onChange={(event) => setGroupNumber(event.target.value)} required />
            <p className="muted">Lista grup nie została rozpoznana. Wpisz numer grupy wskazany w planie.</p>
          </>}
          <label htmlFor="calendar-name">Nazwa kalendarza (opcjonalnie)</label>
          <input id="calendar-name" type="text" maxLength="100" value={calendarName} onChange={(event) => setCalendarName(event.target.value)} placeholder="np. Plan" />
        </>}
        <button type="submit" disabled={saving || checking || !workbook || workbook.source_url !== sourceUrl || (workbook?.grouping_mode !== "all" && !groupNumber)}>
          {saving ? "Tworzenie kalendarza…" : "Utwórz kalendarz"}
        </button>
      </form>
    </section>
  );
}

function SchedulePage({ integration, onChanged, setError, setSuccess }) {
  const [groupNumber, setGroupNumber] = useState(String(integration.group_number));
  const [calendarName, setCalendarName] = useState(integration.calendar_name || "");
  const [workbook, setWorkbook] = useState(null);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    setGroupNumber(String(integration.group_number));
    setCalendarName(integration.calendar_name || "");
    api.workbookInfo(integration.source_url).then(setWorkbook).catch((error) => setError(error.message));
  }, [integration.id, integration.group_number]);

  async function updateGroup(event) {
    event.preventDefault();
    const payload = {};
    if (workbook) {
      const nextGroup = workbook.grouping_mode === "all" ? 0 : Number(groupNumber);
      if (nextGroup !== Number(integration.group_number)) payload.group_number = nextGroup;
    }
    if (calendarName.trim() !== integration.calendar_name) payload.calendar_name = calendarName.trim();
    if (Object.keys(payload).length === 0) {
      setSuccess("Ustawienia kalendarza nie uległy zmianie.");
      return;
    }
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const result = await api.updateGroup(integration.id, payload);
      onChanged(result);
      setSuccess("Zapisano ustawienia i zaktualizowano kalendarz.");
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
      <h3>Ustawienia kalendarza</h3>
      <p className="muted">Link do arkusza pozostaje stały. Aby użyć innego planu, usuń integrację i utwórz ją ponownie.</p>
      <form onSubmit={updateGroup} className="inline-form">
        {workbook?.grouping_mode === "groups" ? <>
          <label htmlFor="change-group">Twoja grupa</label>
          <select id="change-group" value={groupNumber} onChange={(event) => setGroupNumber(event.target.value)} required>
            {workbook.available_groups.map((group) => <option key={group} value={group}>Grupa {group}</option>)}
          </select>
        </> : workbook?.grouping_mode === "all" ? <p className="muted">Ten plan nie ma podziału na grupy.</p> : <>
          <label htmlFor="change-group">Numer grupy</label>
          <input id="change-group" type="number" min="1" max="999" value={groupNumber} onChange={(event) => setGroupNumber(event.target.value)} required />
        </>}
        <label htmlFor="change-calendar-name">Nazwa kalendarza</label>
        <input id="change-calendar-name" type="text" maxLength="100" value={calendarName} onChange={(event) => setCalendarName(event.target.value)} required />
        <button type="submit" disabled={saving}>{saving ? "Zapisywanie…" : "Zapisz ustawienia"}</button>
      </form>
    </section>
  );
}

function NotificationsPage({ onSubscribe, subscribing }) {
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
        <div className="button-row">
          <button type="button" onClick={onSubscribe} disabled={subscribing}>{subscribing ? "Włączanie…" : "Subskrybuj powiadomienia push"}</button>
          <button type="button" className="secondary" onClick={markAllRead}>Oznacz wszystkie jako przeczytane</button>
        </div>
      </div>
      {error && <Message error={error} />}
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
    </section>
  );
}

function PushSettingsPage({ setError, setSuccess }) {
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [removing, setRemoving] = useState("");
  async function reload() {
    try { setDevices(await api.pushSubscriptions()); }
    catch (error) { setError(error.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { reload(); }, []);
  async function remove(id) {
    setRemoving(id);
    setError("");
    try {
      const device = devices.find((item) => item.id === id);
      try {
        const registration = await navigator.serviceWorker?.getRegistration(appPath("/"));
        const subscription = await registration?.pushManager.getSubscription();
        if (subscription && device?.endpoint_hash === await endpointHash(subscription.endpoint)) {
          await subscription.unsubscribe();
        }
      } catch { /* Server-side removal remains available if browser cleanup fails. */ }
      await api.deletePushSubscription(id);
      await reload();
      setSuccess("Usunięto subskrypcję tego urządzenia.");
    } catch (error) { setError(error.message); }
    finally { setRemoving(""); }
  }
  return <section>
    <h2>Urządzenia z powiadomieniami push</h2>
    <p>Każda pozycja to przeglądarka lub urządzenie subskrybujące powiadomienia.</p>
    {loading ? <p>Ładowanie…</p> : devices.length === 0 ? <p>Brak aktywnych subskrypcji.</p> : <div className="list">
      {devices.map((device) => <article className="card" key={device.id}>
        <p><strong>{device.user_agent || "Przeglądarka"}</strong></p>
        <p className="muted">Dodano: {dateTime(device.created_at)}</p>
        <button type="button" className="secondary" onClick={() => remove(device.id)} disabled={removing === device.id}>{removing === device.id ? "Usuwanie…" : "Usuń urządzenie"}</button>
      </article>)}
    </div>}
    <p className="muted">Na iPhonie i iPadzie web push działa po dodaniu KiedyMam do ekranu początkowego (iOS/iPadOS 16.4 lub nowszy).</p>
  </section>;
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
  const [tab, setTab] = useState(() => new URLSearchParams(window.location.search).get("tab") || "plan");
  const [showSetup, setShowSetup] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [subscribing, setSubscribing] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("auth_error") === "calendar_permission_required") {
      setError("Nie przyznano wymaganej zgody na tworzenie kalendarza Google. Spróbuj ponownie i pozostaw tę zgodę zaznaczoną.");
      params.delete("auth_error");
      const query = params.toString();
      window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    }
  }, []);

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

  async function subscribeToPush() {
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
      if (!subscription) subscription = await pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: applicationServerKey(config.public_key) });
      await api.savePushSubscription(subscription.toJSON());
      setSuccess("To urządzenie będzie otrzymywać powiadomienia push.");
      setTab("push");
    } catch (error) { setError(error.message); }
    finally { setSubscribing(false); }
  }

  if (loading) return <main><h1>KiedyMam</h1><p>Ładowanie…</p></main>;

  return (
    <main>
      <header className="site-header">
        <h1><img className="brand-logo" src={appPath("/kiedymam-icon.svg")} alt="KiedyMam" /></h1>
        <p className="muted">Plany zajęć dla studentów Uniwersytetu Medycznego w Białymstoku.</p>
      </header>
      {error && <Message error={error} />}
      {success && <Message success={success} />}

      {!user ? <LoginPage /> : (
        <>
          <nav className="nav" aria-label="Główna nawigacja">
            <div className="nav-links">
              {[ ["plan", "Plan"], ["notifications", "Powiadomienia"], ["push", "Urządzenia push"], ["changelog", "Dziennik zmian"] ].map(([key, label]) => (
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
                  <p>Wybierz plan UMB i swoją grupę. Utworzymy osobny kalendarz Google i uzupełnimy go zajęciami.</p>
                  <button type="button" onClick={() => setShowSetup(true)}>Ustaw integrację</button>
                </div>
              )}
            </section>
          )}
          {tab === "notifications" && <NotificationsPage onSubscribe={subscribeToPush} subscribing={subscribing} />}
          {tab === "push" && <PushSettingsPage setError={setError} setSuccess={setSuccess} />}
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
