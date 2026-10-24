import { useEffect, useState } from "react";
import { api } from "./api.js";
import ChangelogPage from "./components/changelog/ChangelogPage.jsx";
import ContactPage from "./components/feedback/ContactPage.jsx";
import LoginPage from "./components/auth/LoginPage.jsx";
import MainNavigation from "./components/layout/MainNavigation.jsx";
import SiteFooter from "./components/layout/SiteFooter.jsx";
import SiteHeader from "./components/layout/SiteHeader.jsx";
import { Message } from "./components/shared/Message.jsx";
import NotificationsPage from "./components/notifications/NotificationsPage.jsx";
import SchedulePage from "./components/schedule/SchedulePage.jsx";
import SetupForm from "./components/schedule/SetupForm.jsx";

export default function App() {
  const [user, setUser] = useState(null);
  const [integration, setIntegration] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState(() => new URLSearchParams(window.location.search).get("tab") || "plan");
  const [showSetup, setShowSetup] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

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
    async function loadAccount() {
      try {
        const account = await api.me();
        setUser(account);
        const integrations = await api.integrations();
        setIntegration(integrations[0] || null);
      } catch (loadError) {
        if (loadError.status !== 401) setError(loadError.message);
      } finally {
        setLoading(false);
      }
    }
    loadAccount();
  }, []);

  async function logout() {
    try {
      await api.logout();
      setUser(null);
      setIntegration(null);
      setTab("plan");
    } catch (logoutError) {
      setError(logoutError.message);
    }
  }

  function selectTab(nextTab) {
    setTab(nextTab);
    setError("");
    setSuccess("");
  }

  if (loading) {
    return <main><SiteHeader /><p>Ładowanie…</p></main>;
  }

  return (
    <main>
      <SiteHeader />
      {error && <Message error={error} />}
      {success && <Message success={success} />}

      {user ? (
        <>
          <MainNavigation
            tab={tab}
            setTab={selectTab}
            email={user.email}
            onLogout={logout}
            clearMessages={() => { setError(""); setSuccess(""); }}
          />
          {tab === "plan" && (
            <section>
              <h2>Twój plan</h2>
              {integration ? (
                <SchedulePage integration={integration} onChanged={setIntegration} setError={setError} setSuccess={setSuccess} />
              ) : showSetup ? (
                <SetupForm
                  onCreated={(created) => {
                    setIntegration(created);
                    setShowSetup(false);
                    setSuccess("Utworzono kalendarz z Twoim planem.");
                  }}
                  setError={setError}
                />
              ) : (
                <div className="card">
                  <h3>Nie masz jeszcze skonfigurowanego planu</h3>
                  <p>Wybierz plan UMB i swoją grupę. Utworzymy osobny kalendarz Google i uzupełnimy go zajęciami.</p>
                  <button type="button" onClick={() => setShowSetup(true)}>Ustaw integrację</button>
                </div>
              )}
            </section>
          )}
          {tab === "notifications" && <NotificationsPage setError={setError} setSuccess={setSuccess} />}
          {tab === "changelog" && <ChangelogPage />}
          {tab === "contact" && <ContactPage setError={setError} />}
        </>
      ) : tab === "contact" ? (
        <>
          <h2>Kontakt z twórcą</h2>
          <p>Zaloguj się przez Google, aby wysłać wiadomość i móc ją później edytować lub usunąć.</p>
          <LoginPage returnPath="/?tab=contact" />
        </>
      ) : <LoginPage />}

      <SiteFooter onContact={() => selectTab("contact")} />
    </main>
  );
}
