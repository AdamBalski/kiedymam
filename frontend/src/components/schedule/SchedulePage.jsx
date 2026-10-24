import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { dateTime } from "../shared/dateTime.js";

export default function SchedulePage({ integration, onChanged, setError, setSuccess }) {
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
