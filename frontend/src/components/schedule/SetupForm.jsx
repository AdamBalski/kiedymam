import { useEffect, useState } from "react";
import { api } from "../../api.js";

export default function SetupForm({ onCreated, setError }) {
  const [catalog, setCatalog] = useState([]);
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
            <label htmlFor="catalog-plan">Plan (nazwa linku na stronie UMB)</label>
            <select
              id="catalog-plan"
              value={selectedUrl}
              onChange={(event) => { setSelectedUrl(event.target.value); setWorkbook(null); setGroupNumber(""); }}
              required
              disabled={catalogLoading || linksOnPage.length === 0}
            >
              <option value="" disabled>
                {catalogLoading ? "Ładowanie planów…" : linksOnPage.length ? "Wybierz plan" : "Brak planów na tej stronie"}
              </option>
              {linksOnPage.map((link) => (
                <option key={link.url} value={link.url}>
                  {link.href_label}
                </option>
              ))}
            </select>
            {!catalogLoading && catalog.length === 0 && (
              <p className="muted">Lista planów jest pusta. Możesz użyć własnego linku albo administrator może skonfigurować strony UMB na serwerze.</p>
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
