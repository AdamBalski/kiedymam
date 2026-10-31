import { useEffect, useState } from "react";
import { api } from "../../api.js";

export default function SetupForm({ onCreated, setError }) {
  const [catalog, setCatalog] = useState([]);
  const [selectedDegree, setSelectedDegree] = useState("");
  const [selectedStudyYear, setSelectedStudyYear] = useState("");
  const [selectedStudyMode, setSelectedStudyMode] = useState("");
  const [selectedLinkKey, setSelectedLinkKey] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [groupNumber, setGroupNumber] = useState("");
  const [calendarName, setCalendarName] = useState("");
  const [workbook, setWorkbook] = useState(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    api.timetableLinks()
      .then((links) => {
        setCatalog(links);
        setSelectedLinkKey(links[0]?.link_key || "");
        setSelectedDegree(links[0]?.degree || "");
        setSelectedStudyYear(links[0] ? String(links[0].study_year) : "");
        setSelectedStudyMode(links[0]?.study_mode || "");
      })
      .catch(() => setCatalog([]))
      .finally(() => setCatalogLoading(false));
  }, []);

  const degrees = [...new Set(catalog.map((link) => link.degree))].sort((a, b) => a.localeCompare(b, "pl"));
  const studyYears = [...new Set(catalog.filter((link) => link.degree === selectedDegree).map((link) => link.study_year))].sort((a, b) => a - b);
  const studyModes = [...new Set(catalog
    .filter((link) => link.degree === selectedDegree && String(link.study_year) === selectedStudyYear)
    .map((link) => link.study_mode))].sort();

  async function inspectWorkbook() {
    if (!selectedLinkKey) return setError("Wybierz plan z listy.");
    setChecking(true);
    setWorkbook(null);
    setError("");
    try {
      const details = await api.workbookInfo(selectedLinkKey);
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
    if (!selectedLinkKey) {
      setError("Wybierz plan z listy.");
      return;
    }
    if (workbook?.timetable_link_key !== selectedLinkKey) {
      setError("Najpierw sprawdź plan, aby odczytać dostępne grupy.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const integration = await api.createIntegration({
        timetable_link_key: selectedLinkKey,
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
        Wybierz plan z listy. Kiedy UMB opublikuje jego nowszą wersję, kalendarz
        będzie korzystał z aktualnego linku. Po sprawdzeniu arkusza pokażemy grupy wykryte w planie.
      </p>
      <form onSubmit={submit}>
        <label htmlFor="catalog-degree">Kierunek</label>
        <select
          id="catalog-degree"
          value={selectedDegree}
          onChange={(event) => {
            const degree = event.target.value;
            const first = catalog.find((link) => link.degree === degree);
            setSelectedDegree(degree);
            setSelectedStudyYear(first ? String(first.study_year) : "");
            setSelectedStudyMode(first?.study_mode || "");
            setSelectedLinkKey(first?.link_key || "");
            setWorkbook(null);
            setGroupNumber("");
          }}
          required
          disabled={catalogLoading || degrees.length === 0}
        >
          <option value="" disabled>
            {catalogLoading ? "Ładowanie kierunków…" : "Wybierz kierunek"}
          </option>
          {degrees.map((degree) => (
            <option key={degree} value={degree}>{degree}</option>
          ))}
        </select>
        <label htmlFor="catalog-study-year">Rok studiów</label>
        <select
          id="catalog-study-year"
          value={selectedStudyYear}
          onChange={(event) => {
            const studyYear = event.target.value;
            const first = catalog.find((link) => link.degree === selectedDegree && String(link.study_year) === studyYear);
            setSelectedStudyYear(studyYear);
            setSelectedStudyMode(first?.study_mode || "");
            setSelectedLinkKey(first?.link_key || "");
            setWorkbook(null);
            setGroupNumber("");
          }}
          required
          disabled={catalogLoading || studyYears.length === 0}
        >
          {studyYears.map((studyYear) => (
            <option key={studyYear} value={String(studyYear)}>{studyYear}</option>
          ))}
        </select>
        <label htmlFor="catalog-study-mode">Tryb studiów</label>
        <select id="catalog-study-mode" value={selectedStudyMode} onChange={(event) => {
          const mode = event.target.value;
          const selected = catalog.find((link) => link.degree === selectedDegree && String(link.study_year) === selectedStudyYear && link.study_mode === mode);
          setSelectedStudyMode(mode);
          setSelectedLinkKey(selected?.link_key || "");
          setWorkbook(null);
          setGroupNumber("");
        }} required disabled={catalogLoading || studyModes.length === 0}>
          {studyModes.map((mode) => <option key={mode} value={mode}>{mode === "stacjonarne" ? "Stacjonarne" : "Niestacjonarne"}</option>)}
        </select>
        {!catalogLoading && catalog.length === 0 && (
          <p className="muted">Brak planów w katalogu. Spróbuj ponownie po jego najbliższym odświeżeniu.</p>
        )}
        <button type="button" className="secondary" onClick={inspectWorkbook} disabled={checking || !selectedLinkKey}>
          {checking ? "Odczytywanie arkusza…" : "Sprawdź plan i dostępne grupy"}
        </button>
        {workbook?.timetable_link_key === selectedLinkKey && <>
          {!workbook.available ? <p role="status" className="muted">{workbook.message || "Ten plan nie jest obecnie dostępny na stronie UMB."}</p> : <>
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
        </>}
        <button type="submit" disabled={saving || checking || !workbook?.available || workbook.timetable_link_key !== selectedLinkKey || (workbook?.grouping_mode !== "all" && !groupNumber)}>
          {saving ? "Tworzenie kalendarza…" : "Utwórz kalendarz"}
        </button>
      </form>
    </section>
  );
}
