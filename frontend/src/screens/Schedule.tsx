import { useEffect, useState } from "react";
import { api, type AppConfig, type ScheduleResponse } from "../api/client";
import { Panel } from "../components/Panel";
import { TimeSeriesChart } from "../components/TimeSeriesChart";
import { berlinHm, dayParts, num, shiftDay, signed } from "../format";

interface Props {
  day: string;
  onDay: (day: string) => void;
  config: AppConfig | null;
}

export function Schedule({ day, onDay, config }: Props) {
  const [data, setData] = useState<ScheduleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    let stale = false;
    setData(null);
    setError(null);
    api.schedule(day).then((d) => !stale && setData(d)).catch((e: Error) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [day]);

  const parts = dayParts(day);
  const rows = data?.rows ?? [];
  const step = (data?.resolution_min ?? 15) * 60_000;
  const s = data?.summary;
  const code = `${config?.battery.id ?? "BAT-01"} · ${config?.market.zone ?? "DE-LU"} · INT ${
    data?.resolution_min ?? "--"}M`;

  return (
    <div className="grid schedule">
      <Panel title="Delivery day" code="EUROPE/BERLIN" area="cal">
        <div className="cal">
          <div className="month">{parts.month.toUpperCase()} {parts.year}</div>
          <div className="day" aria-hidden="true">{parts.day}</div>
          <div className="weekday">{parts.weekday.toUpperCase()}</div>
          <div className="nav">
            <button type="button" className="btn" onClick={() => onDay(shiftDay(day, -1))}
              aria-label="Previous day">◀</button>
            <label className="sr-only" htmlFor="day-pick">Delivery day</label>
            <input id="day-pick" type="date" value={day}
              onChange={(e) => e.target.value && onDay(e.target.value)} />
            <button type="button" className="btn" onClick={() => onDay(shiftDay(day, 1))}
              aria-label="Next day">▶</button>
          </div>
        </div>
      </Panel>

      <Panel title="Plan summary" code={`${data?.intervals ?? "--"} INTERVALS`} area="sum">
        <dl className="kv">
          <dt>Optimizer</dt><dd>€ {num(s?.revenue_eur, 2)}</dd>
          <dt>Naive rule</dt><dd>€ {num(s?.naive_eur, 2)}</dd>
          <dt>Cycles</dt><dd>{num(s?.cycles, 2)}</dd>
          <dt>Price min</dt><dd>{num(s?.price_min, 2)} €/MWh</dd>
          <dt>Price max</dt><dd>{num(s?.price_max, 2)} €/MWh</dd>
          <dt>Solve time</dt><dd>{num(s?.solve_ms, 0)} ms</dd>
        </dl>
      </Panel>

      <Panel title="Price and dispatch" code={code} area="chart" grid>
        {error && <p className="banner" role="alert">{error}</p>}
        {!data && !error && <p className="note">LOADING…</p>}
        {data && !data.available && (
          <p className="banner" role="status">
            {data.reason} {data.quality.gaps.length} gap(s), {data.quality.duplicates.length}{" "}
            duplicate(s). Prices are never interpolated.
          </p>
        )}
        {data?.available && (
          <>
            <TimeSeriesChart label={`Price, dispatch and state of charge for ${day}`} zoomable scan
              x={rows.map((r) => Date.parse(r.ts_utc))} step={step} formatX={berlinHm}
              onHover={setHover}
              panes={[
                { id: "price", label: "Price", unit: "€/MWh", height: 150, kind: "step", digits: 2,
                  values: rows.map((r) => r.price_eur_mwh) },
                { id: "power", label: "Dispatch", unit: "kW", height: 110, kind: "bars", digits: 0,
                  values: rows.map((r) => r.power_kw),
                  domain: [-(config?.battery.max_discharge_kw ?? 500),
                    config?.battery.max_charge_kw ?? 500] },
                { id: "soc", label: "SoC", unit: "%", height: 80, kind: "line", digits: 1, scale: 100,
                  domain: [0, 100], values: rows.map((r) => r.soc) },
              ]} />
            <div className="legend">
              <span><i className="k-solid" />Charge</span>
              <span><i className="k-hatch" />Discharge</span>
              <span><i className="k-dots" />Idle</span>
            </div>
          </>
        )}
      </Panel>

      {data?.available && (
        <Panel title="Interval table" code={`${rows.length} ROWS`} area="table">
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Interval table">
            <table>
              <thead>
                <tr><th>Start</th><th>Price €/MWh</th><th>Setpoint kW</th><th>SoC %</th>
                  <th>Cash flow €</th><th>Naive kW</th></tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.ts_utc} className={i === hover ? "hl" : undefined}>
                    <td>{r.local}</td>
                    <td>{num(r.price_eur_mwh, 2)}</td>
                    <td>{signed(r.power_kw, 1)}</td>
                    <td>{num(r.soc * 100, 1)}</td>
                    <td>{signed(r.cash_flow_eur, 2)}</td>
                    <td>{signed(r.naive_power_kw, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}
