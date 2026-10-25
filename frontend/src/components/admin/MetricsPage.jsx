import { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";

const dateInput = (date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
};

function MetricChart({ series }) {
  const points = series.flatMap((item) => item.points);
  if (!points.length) return <p className="muted">Brak próbek w wybranym przedziale.</p>;
  const width = 900;
  const height = 300;
  const left = 88;
  const right = 18;
  const top = 20;
  const bottom = 48;
  const minTime = Math.min(...points.map((point) => point.timestamp));
  const maxTime = Math.max(...points.map((point) => point.timestamp), minTime + 1);
  const values = points.map((point) => Number(point.value));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const flatRange = Math.max(Math.abs(maxValue) * 0.1, 0.000001);
  const domainMin = minValue === maxValue ? maxValue - flatRange : minValue;
  const domainMax = maxValue;
  const colors = ["#005a9c", "#c3423f", "#237a57", "#7b4ba1", "#b66a00", "#007c91"];
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const x = (time) => left + ((time - minTime) / (maxTime - minTime)) * plotWidth;
  const y = (value) => top + ((domainMax - value) / (domainMax - domainMin)) * plotHeight;
  const formatValue = (value) => new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4 }).format(value);
  const rangeSeconds = maxTime - minTime;
  const timeFormat = new Intl.DateTimeFormat(undefined, rangeSeconds > 86400
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const yTicks = Array.from({ length: 5 }, (_, index) => {
    const fraction = index / 4;
    const value = domainMax - fraction * (domainMax - domainMin);
    return { value, y: top + fraction * plotHeight };
  });
  const xTicks = Array.from({ length: 5 }, (_, index) => {
    const fraction = index / 4;
    const timestamp = minTime + fraction * (maxTime - minTime);
    return { timestamp, x: left + fraction * plotWidth };
  });
  return (
    <div className="metric-chart-wrap">
      <svg className="metric-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Wykres metryki z osiami i wartościami">
        {yTicks.map((tick) => <g key={`y-${tick.value}`}>
          <line x1={left} y1={tick.y} x2={width - right} y2={tick.y} stroke="#d7dce1" />
          <text x={left - 10} y={tick.y} textAnchor="end" dominantBaseline="middle" className="metric-axis-label">
            {formatValue(tick.value)}
          </text>
        </g>)}
        <line x1={left} y1={top} x2={left} y2={height - bottom} stroke="#777" />
        <line x1={left} y1={height - bottom} x2={width - right} y2={height - bottom} stroke="#777" />
        {xTicks.map((tick, index) => <g key={`x-${tick.timestamp}`}>
          <line x1={tick.x} y1={height - bottom} x2={tick.x} y2={height - bottom + 5} stroke="#777" />
          <text
            x={tick.x}
            y={height - bottom + 20}
            textAnchor={index === 0 ? "start" : index === xTicks.length - 1 ? "end" : "middle"}
            className="metric-axis-label"
          >
            {timeFormat.format(new Date(tick.timestamp * 1000))}
          </text>
        </g>)}
        {series.map((item, index) => (
          <g key={JSON.stringify(item.dimensions)}>
            <polyline
              fill="none"
              stroke={colors[index % colors.length]}
              strokeWidth="2.5"
              points={item.points.map((point) => `${x(point.timestamp)},${y(Number(point.value))}`).join(" ")}
            />
            {item.points.map((point) => (
              <circle key={point.timestamp} cx={x(point.timestamp)} cy={y(Number(point.value))} r="3" fill={colors[index % colors.length]}>
                <title>{new Date(point.timestamp * 1000).toLocaleString()}: {point.value}</title>
              </circle>
            ))}
          </g>
        ))}
      </svg>
      <div className="metric-legend">
        {series.map((item, index) => (
          <span key={JSON.stringify(item.dimensions)}><i style={{ background: colors[index % colors.length] }} />
            {Object.entries(item.dimensions).map(([key, value]) => `${key}=${value}`).join(", ") || "Wszystkie serie"}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function MetricsPage({ setError, setSuccess }) {
  const [catalog, setCatalog] = useState([]);
  const [selectedName, setSelectedName] = useState("");
  const [filters, setFilters] = useState({});
  const [operations, setOperations] = useState([
    { type: "metric", operation: "sum", group_by: [], interval_seconds: null },
  ]);
  const [start, setStart] = useState(() => dateInput(new Date(Date.now() - 60 * 60 * 1000)));
  const [end, setEnd] = useState(() => dateInput(new Date()));
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [recipient, setRecipient] = useState("");
  const [message, setMessage] = useState("Testowe powiadomienie KiedyMam.");
  const [sending, setSending] = useState(false);
  const [pushResult, setPushResult] = useState(null);

  useEffect(() => {
    api.metricCatalog().then((data) => {
      const firstMetric = data.metrics[0];
      setCatalog(data.metrics);
      setSelectedName(firstMetric?.name || "");
      setOperations([{ type: "metric", operation: "sum",
        group_by: firstMetric?.dimensions.map((dimension) => dimension.name) || [], interval_seconds: null }]);
    }).catch((error) => setError(error.message));
  }, [setError]);

  const selected = useMemo(() => catalog.find((item) => item.name === selectedName), [catalog, selectedName]);

  function groupableDimensions(operationIndex, sourceOperations = operations) {
    const available = new Set((selected?.dimensions || []).map((dimension) => dimension.name));
    for (const operation of sourceOperations.slice(0, operationIndex)) {
      if (operation.type === "metric") {
        const retained = new Set(operation.group_by);
        for (const name of available) {
          if (!retained.has(name)) available.delete(name);
        }
      }
    }
    return (selected?.dimensions || []).filter((dimension) => available.has(dimension.name));
  }

  const minimumInterval = useMemo(() => {
    const range = (new Date(end).getTime() - new Date(start).getTime()) / 1000;
    return range <= 3600 ? 5 : range <= 7 * 86400 ? 60 : range <= 30 * 86400 ? 3600 : 86400;
  }, [start, end]);

  const intervalOptions = [5, 10, 15, 30, 60, 300, 900, 1800, 3600, 21600, 43200, 86400, 604800, 2592000]
    .filter((seconds) => seconds >= minimumInterval && seconds % minimumInterval === 0);

  function chooseMetric(name) {
    const metric = catalog.find((item) => item.name === name);
    setSelectedName(name);
    setFilters({});
    setOperations([{ type: "metric", operation: "sum",
      group_by: metric?.dimensions.map((dimension) => dimension.name) || [], interval_seconds: null }]);
    setResult(null);
  }

  function updateOperation(index, updates) {
    setOperations((current) => {
      const updated = current.map((operation, itemIndex) => itemIndex === index
        ? { ...operation, ...updates }
        : operation);
      const available = new Set((selected?.dimensions || []).map((dimension) => dimension.name));
      return updated.map((operation) => {
        if (operation.type !== "metric") return operation;
        const group_by = operation.group_by.filter((name) => available.has(name));
        available.clear();
        group_by.forEach((name) => available.add(name));
        return group_by.length === operation.group_by.length ? operation : { ...operation, group_by };
      });
    });
  }

  function updateArithmeticFilter(index, name, value) {
    setOperations((current) => current.map((operation, itemIndex) => {
      if (itemIndex !== index) return operation;
      const filters = { ...operation.query.filters };
      if (value === "") delete filters[name];
      else filters[name] = value;
      return { ...operation, query: { ...operation.query, filters } };
    }));
  }

  function addMetricOperation() {
    const group_by = groupableDimensions(operations.length).map((dimension) => dimension.name);
    setOperations((current) => current.length >= 16 ? current : [
      ...current,
      { type: "metric", operation: "sum", group_by, interval_seconds: null },
    ]);
  }

  function addPredicate() {
    setOperations((current) => current.length >= 16 ? current : [
      ...current,
      { type: "predicate", operator: ">=", value: "1" },
    ]);
  }

  function addArithmetic() {
    const operand = catalog.find((item) => item.name !== selectedName) || selected;
    if (!operand) return;
    setOperations((current) => current.length >= 16 ? current : [
      ...current,
      {
        type: "arithmetic",
        operator: "divide",
        query: { metric: operand.name, filters: {}, operation: "sum", group_by: [], interval_seconds: null },
      },
    ]);
  }

  function updateFilter(name, value) {
    setFilters((current) => ({ ...current, [name]: value }));
  }

  async function runQuery(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      setResult(await api.queryMetrics({
        metric: selectedName,
        start: new Date(start).getTime() / 1000,
        end: new Date(end).getTime() / 1000,
        filters,
        operations: operations.map((operation) => {
          if (operation.type === "metric") {
            return { type: "metric", operation: operation.operation, group_by: operation.group_by,
              interval_seconds: operation.interval_seconds ?? minimumInterval };
          }
          if (operation.type === "predicate") {
            return { type: "predicate", operator: operation.operator, value: Number(operation.value) };
          }
          return {
            type: "arithmetic",
            operator: operation.operator,
            query: {
              metric: operation.query.metric,
              start: new Date(start).getTime() / 1000,
              end: new Date(end).getTime() / 1000,
              filters: operation.query.filters,
              operations: [{
                type: "metric",
                operation: operation.query.operation,
                group_by: operation.query.group_by,
                interval_seconds: operation.query.interval_seconds ?? minimumInterval,
              }],
            },
          };
        }),
      }));
    } catch (error) {
      setError(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function sendTest(event) {
    event.preventDefault();
    setSending(true);
    setError("");
    setPushResult(null);
    try {
      const response = await api.sendTestPush({ email: recipient, message });
      setPushResult(response);
      setSuccess("Wysłano próbę powiadomienia push.");
    } catch (error) {
      setError(error.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="admin-metrics">
      <h2>Eksplorator metryk</h2>
      <p className="muted">Wybierz metrykę, a następnie filtruj i grupuj jej atrybuty. Dane mają różną rozdzielczość zależnie od wieku próbki.</p>
      <form className="card metric-query" onSubmit={runQuery}>
        <label htmlFor="metric-name">Metryka</label>
        <select id="metric-name" value={selectedName} onChange={(event) => chooseMetric(event.target.value)} required>
          {catalog.map((metric) => <option key={metric.name} value={metric.name}>{metric.name} · {metric.type}</option>)}
        </select>
        {selected && <p className="muted">Typ: {selected.type}</p>}

        <div className="metric-dimensions">
          {(selected?.dimensions || []).map((dimension) => <div className="metric-dimension" key={dimension.name}>
            <label htmlFor={`filter-${dimension.name}`}>Filtr: {dimension.name}</label>
            {dimension.values.length > 0 && dimension.values.length <= 100 ? <select
              id={`filter-${dimension.name}`} value={filters[dimension.name] ?? ""}
              onChange={(event) => updateFilter(dimension.name, event.target.value)}>
              <option value="">Wszystkie</option>
              {dimension.values.map((value) => <option key={`${typeof value}-${value}`} value={value}>{String(value)}</option>)}
            </select> : <input id={`filter-${dimension.name}`} value={filters[dimension.name] ?? ""}
              onChange={(event) => updateFilter(dimension.name, event.target.value)} placeholder="Wartość atrybutu" />}
          </div>)}
        </div>

        <div className="metric-time-range">
          <div><label htmlFor="metric-start">Od</label><input id="metric-start" type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} required /></div>
          <div><label htmlFor="metric-end">Do</label><input id="metric-end" type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} required /></div>
        </div>
        <div className="metric-operations">
          <h3>Operacje</h3>
          <p className="muted">Operacje są wykonywane po kolei. Predykat zamienia każdą wartość na sygnał 1.0 (prawda) albo 0.0 (fałsz).</p>
          {operations.map((operation, index) => <fieldset className="metric-operation" key={index}>
            <legend>Operacja {index + 1}</legend>
            <button type="button" className="secondary-button" disabled={index === 0}
              onClick={() => setOperations((current) => current.filter((_, itemIndex) => itemIndex !== index))}>
              Usuń
            </button>
            {operation.type === "metric" ? <>
              <label htmlFor={`metric-operation-${index}`}>Agregacja</label>
              <select id={`metric-operation-${index}`} value={operation.operation}
                onChange={(event) => updateOperation(index, { operation: event.target.value })}>
                <option value="sum">Suma</option>
                <option value="count">Liczba próbek</option>
                <option value="mean">Średnia</option>
                <option value="min">Minimum</option>
                <option value="max">Maksimum</option>
                {selected?.type === "histogram" && Array.from({ length: 101 }, (_, percentile) => <option key={`p${percentile}`} value={`p${percentile}`}>
                  Percentyl p{percentile}
                </option>)}
              </select>
              {operation.operation.startsWith("p") && selected?.type === "histogram" && <p className="muted">
                {operation.operation} szacuje wartość, poniżej której mieści się {operation.operation.slice(1)}% obserwacji.
              </p>}
              <label htmlFor={`metric-interval-${index}`}>Przedział wyniku</label>
              <select id={`metric-interval-${index}`} value={operation.interval_seconds ?? "auto"}
                onChange={(event) => updateOperation(index, { interval_seconds: event.target.value === "auto" ? null : Number(event.target.value) })}>
                <option value="auto">Automatycznie ({minimumInterval}s lub więcej)</option>
                {intervalOptions.map((seconds) => <option key={seconds} value={seconds}>{seconds < 60 ? `${seconds} s` : seconds < 3600 ? `${seconds / 60} min` : seconds < 86400 ? `${seconds / 3600} godz.` : `${seconds / 86400} dni`}</option>)}
              </select>
              <div className="metric-operation-groups">
                <span>Grupuj według</span>
                {groupableDimensions(index).map((dimension) => <label className="checkbox-option" key={dimension.name}>
                  <input type="checkbox" checked={operation.group_by.includes(dimension.name)}
                    onChange={(event) => updateOperation(index, { group_by: event.target.checked
                      ? [...operation.group_by, dimension.name]
                      : operation.group_by.filter((name) => name !== dimension.name) })} />
                  {dimension.name}
                </label>)}
              </div>
              <p className="muted">
                {operation.group_by.length === 0
                  ? "Brak zaznaczonych wymiarów łączy wszystkie serie w jedną. Kolejne operacje nie będą miały wymiarów do grupowania."
                  : "Wynik zachowa tylko zaznaczone wymiary; kolejne operacje będą mogły grupować tylko według nich."}
              </p>
            </> : operation.type === "predicate" ? <>
              <label htmlFor={`predicate-operator-${index}`}>Warunek</label>
              <select id={`predicate-operator-${index}`} value={operation.operator}
                onChange={(event) => updateOperation(index, { operator: event.target.value })}>
                {[">", ">=", "<", "<=", "=", "!="].map((symbol) => <option key={symbol} value={symbol}>{symbol}</option>)}
              </select>
              <label htmlFor={`predicate-value-${index}`}>Wartość</label>
              <input id={`predicate-value-${index}`} type="number" step="any" value={operation.value}
                onChange={(event) => updateOperation(index, { value: event.target.value })} required />
            </> : <>
              <label htmlFor={`arithmetic-operator-${index}`}>Działanie na zapytaniach</label>
              <select id={`arithmetic-operator-${index}`} value={operation.operator}
                onChange={(event) => updateOperation(index, { operator: event.target.value })}>
                <option value="add">Dodaj</option>
                <option value="subtract">Odejmij</option>
                <option value="multiply">Pomnóż</option>
                <option value="divide">Podziel</option>
              </select>
              <label htmlFor={`arithmetic-metric-${index}`}>Drugie zapytanie: metryka</label>
              <select id={`arithmetic-metric-${index}`} value={operation.query.metric}
                onChange={(event) => updateOperation(index, { query: {
                  ...operation.query, metric: event.target.value, group_by: [], filters: {},
                } })}>
                {catalog.map((metric) => <option key={metric.name} value={metric.name}>{metric.name} · {metric.type}</option>)}
              </select>
              <label htmlFor={`arithmetic-aggregation-${index}`}>Agregacja drugiego zapytania</label>
              <select id={`arithmetic-aggregation-${index}`} value={operation.query.operation}
                onChange={(event) => updateOperation(index, { query: { ...operation.query, operation: event.target.value } })}>
                <option value="sum">Suma</option>
                <option value="count">Liczba próbek</option>
                <option value="mean">Średnia</option>
                <option value="min">Minimum</option>
                <option value="max">Maksimum</option>
                {catalog.find((item) => item.name === operation.query.metric)?.type === "histogram"
                  && Array.from({ length: 101 }, (_, percentile) => <option key={`arithmetic-p${percentile}`} value={`p${percentile}`}>Percentyl p{percentile}</option>)}
              </select>
              <label htmlFor={`arithmetic-interval-${index}`}>Przedział drugiego zapytania</label>
              <select id={`arithmetic-interval-${index}`} value={operation.query.interval_seconds ?? "auto"}
                onChange={(event) => updateOperation(index, { query: { ...operation.query,
                  interval_seconds: event.target.value === "auto" ? null : Number(event.target.value) } })}>
                <option value="auto">Automatycznie</option>
                {intervalOptions.map((seconds) => <option key={seconds} value={seconds}>{seconds < 60 ? `${seconds} s` : seconds < 3600 ? `${seconds / 60} min` : seconds < 86400 ? `${seconds / 3600} godz.` : `${seconds / 86400} dni`}</option>)}
              </select>
              <fieldset className="metric-operation-groups">
                <legend>Grupuj drugie zapytanie według</legend>
                {(catalog.find((item) => item.name === operation.query.metric)?.dimensions || []).map((dimension) => <label className="checkbox-option" key={dimension.name}>
                  <input type="checkbox" checked={operation.query.group_by.includes(dimension.name)}
                    onChange={(event) => updateOperation(index, { query: { ...operation.query,
                      group_by: event.target.checked ? [...operation.query.group_by, dimension.name]
                        : operation.query.group_by.filter((name) => name !== dimension.name) } })} />
                  {dimension.name}
                </label>)}
              </fieldset>
              <fieldset className="metric-operation-groups">
                <legend>Filtry drugiego zapytania</legend>
                {(catalog.find((item) => item.name === operation.query.metric)?.dimensions || []).map((dimension) => <label key={dimension.name}>
                  {dimension.name}
                  {dimension.values.length > 0 && dimension.values.length <= 100 ? <select
                    value={operation.query.filters[dimension.name] ?? ""}
                    onChange={(event) => updateArithmeticFilter(index, dimension.name, event.target.value)}>
                    <option value="">Wszystkie</option>
                    {dimension.values.map((value) => <option key={`${typeof value}-${value}`} value={value}>{String(value)}</option>)}
                  </select> : <input value={operation.query.filters[dimension.name] ?? ""}
                    onChange={(event) => updateArithmeticFilter(index, dimension.name, event.target.value)} />}
                </label>)}
              </fieldset>
              <p className="muted">Wyniki łączą się po dokładnym czasie i wymiarach. Ustaw zgodne grupowanie i przedział w obu zapytaniach.</p>
            </>}
          </fieldset>)}
          <button type="button" className="secondary-button" onClick={addMetricOperation} disabled={operations.length >= 16}>
            Dodaj agregację
          </button>
          <button type="button" className="secondary-button" onClick={addPredicate} disabled={operations.length >= 16}>
            Dodaj predykat
          </button>
          <button type="button" className="secondary-button" onClick={addArithmetic} disabled={operations.length >= 16 || catalog.length === 0}>
            Dodaj działanie na zapytaniu
          </button>
        </div>
        <button type="submit" disabled={loading || !selected}>{loading ? "Pobieranie…" : "Pokaż metrykę"}</button>
      </form>

      {result && <div className="card">
        <h3>{result.metric}</h3>
        <p className="muted">Wersja usługi: {result.service.version}</p>
        <p className="muted">Krok: {result.step_seconds}s · wynik operacji na żądanie</p>
        <MetricChart series={result.series} />
        <div className="table-scroll"><table className="metric-table"><thead><tr><th>Czas UTC</th><th>Wymiary</th><th>Wartość</th></tr></thead>
            <tbody>{result.series.flatMap((series) => series.points.map((point) => <tr key={`${JSON.stringify(series.dimensions)}-${point.timestamp}`}>
              <td>{new Date(point.timestamp * 1000).toISOString()}</td><td>{Object.entries(series.dimensions).map(([key, value]) => `${key}=${value}`).join(", ") || "—"}</td><td>{Number(point.value).toPrecision(6)}</td>
            </tr>))}</tbody></table></div>
      </div>}

      <div className="card">
        <h3>Testowe powiadomienie push</h3>
        <p>Wybierz konto po adresie email. Test zostanie wysłany tylko na aktywne urządzenia tego użytkownika.</p>
        <form onSubmit={sendTest} className="admin-push-form">
          <label htmlFor="test-push-email">Email odbiorcy</label>
          <input id="test-push-email" type="email" autoComplete="off" maxLength="320" value={recipient} onChange={(event) => setRecipient(event.target.value)} required />
          <label htmlFor="test-push-message">Treść testowego powiadomienia</label>
          <textarea id="test-push-message" minLength="1" maxLength="256" value={message} onChange={(event) => setMessage(event.target.value)} required />
          <button type="submit" disabled={sending}>{sending ? "Wysyłanie…" : "Wyślij test"}</button>
        </form>
        {pushResult && <p role="status">Próby: {pushResult.attempted}, przyjęte: {pushResult.accepted}, wygasłe urządzenia: {pushResult.expired}, błędy: {pushResult.failed}.</p>}
      </div>
    </section>
  );
}
