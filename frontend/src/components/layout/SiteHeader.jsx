import { appPath } from "../../api.js";

export default function SiteHeader() {
  return (
    <header className="site-header">
      <h1><img className="brand-logo" src={appPath("/kiedymam-icon.svg")} alt="KiedyMam" /></h1>
      <p className="muted">Plany zajęć dla studentów Uniwersytetu Medycznego w Białymstoku.</p>
    </header>
  );
}
