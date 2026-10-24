import { useEffect, useState } from "react";
import { api } from "../../api.js";

export default function ContactPage({ setError }) {
  const [body, setBody] = useState("");
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.feedback()
      .then((feedback) => {
        setBody(feedback?.body || "");
        setSaved(Boolean(feedback));
      })
      .catch((error) => setError(error.message))
      .finally(() => setLoading(false));
  }, [setError]);

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
    try {
      await api.saveFeedback(body);
      setSaved(true);
      setMessage("Wiadomość została wysłana. Możesz ją później zmienić lub usunąć.");
    } catch (error) {
      setError(error.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setRemoving(true);
    setMessage("");
    setError("");
    try {
      await api.deleteFeedback();
      setBody("");
      setSaved(false);
      setMessage("Twoja wiadomość została usunięta.");
    } catch (error) {
      setError(error.message);
    } finally {
      setRemoving(false);
    }
  }

  return (
    <section>
      <h2>Kontakt z twórcą</h2>
      <p>Przekaż opinię, pochwałę, pytanie lub propozycję funkcji. Możesz ją później zmienić albo usunąć.</p>
      <p className="muted">Przechowujemy tylko Twoją aktualną wiadomość. Zapisanie zmian zastępuje poprzednią treść.</p>
      {message && <div className="message success" role="status">{message}</div>}
      {loading ? <p>Ładowanie…</p> : (
        <form onSubmit={submit} className="feedback-form">
          <label htmlFor="creator-feedback">Twoja wiadomość</label>
          <textarea
            id="creator-feedback"
            value={body}
            rows={10}
            onChange={(event) => {
              const characters = Array.from(event.target.value);
              setBody(characters.length <= 3000 ? event.target.value : characters.slice(0, 3000).join(""));
            }}
            aria-describedby="feedback-count"
          />
          <p id="feedback-count" className="muted">{Array.from(body).length}/3000 znaków</p>
          <div className="button-row">
            <button type="submit" disabled={saving || removing || body.length === 0}>
              {saving ? "Wysyłanie…" : saved ? "Zapisz zmiany i wyślij" : "Wyślij"}
            </button>
            {saved && <button type="button" className="secondary" onClick={remove} disabled={saving || removing}>
              {removing ? "Usuwanie…" : "Usuń wiadomość"}
            </button>}
          </div>
        </form>
      )}
    </section>
  );
}
