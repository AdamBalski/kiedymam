export default function MainNavigation({ tab, setTab, email, onLogout, clearMessages }) {
  const pages = [
    ["plan", "Plan"],
    ["notifications", "Powiadomienia"],
    ["changelog", "Dziennik zmian"],
    ["contact", "Kontakt z twórcą"],
  ];

  function selectPage(nextTab) {
    setTab(nextTab);
    clearMessages();
  }

  return (
    <nav className="nav" aria-label="Główna nawigacja">
      <div className="nav-links">
        {pages.map(([key, label]) => (
          <button
            type="button"
            className={tab === key ? "nav-active" : "nav-link"}
            key={key}
            onClick={() => selectPage(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <span className="account-label">{email}</span>
      <button type="button" className="nav-logout" onClick={onLogout}>Wyloguj</button>
    </nav>
  );
}
