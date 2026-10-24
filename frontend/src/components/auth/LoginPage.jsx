import { appPath } from "../../api.js";

export default function LoginPage({ returnPath = "/" }) {
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
      <a className="button primary" href={appPath(`/api/auth/google?return_path=${encodeURIComponent(returnPath)}`)}>Zaloguj się przez Google</a>
      <p className="muted">
        Aplikacja utworzy osobny kalendarz na Twoim koncie Google. Nie zmienia
        Twojego głównego kalendarza.
      </p>
    </section>
  );
}
