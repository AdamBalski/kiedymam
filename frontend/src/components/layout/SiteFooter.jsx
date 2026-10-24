import { appPath } from "../../api.js";

export default function SiteFooter({ onContact }) {
  return (
    <footer className="site-footer">
      <p>Plany pochodzą z oficjalnej strony UMB. Zmiany sprawdzane są co 30 minut.</p>
      <p>
        <button type="button" className="footer-link" onClick={onContact}>Kontakt z twórcą</button>
        {" · "}<a href={appPath("/privacy")}>Polityka prywatności</a>
        {" · "}<a href={appPath("/terms")}>Warunki korzystania</a>
      </p>
    </footer>
  );
}
