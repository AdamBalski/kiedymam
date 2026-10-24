import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { Message } from "../shared/Message.jsx";

export default function ChangelogPage() {
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
