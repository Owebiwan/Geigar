import { useEffect, useState } from "react";
import { api, type BacktestResponse } from "../api/client";
import { Segmented } from "../components/Controls";
import { MarketsPanel } from "../components/MarketsPanel";
import { MonthlyBars } from "../components/MonthlyBars";
import { NumericTile } from "../components/NumericTile";
import { Panel } from "../components/Panel";
import { num } from "../format";
import { useT } from "../i18n";

const YEAR = 2025;

export function Backtest({ onOpenDay }: { onOpenDay: (day: string) => void }) {
  const t = useT();
  const [duration, setDuration] = useState(2);
  const [data, setData] = useState<BacktestResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [forecasting, setForecasting] = useState(false);
  const [marketsBusy, setMarketsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    setData(null);
    setError(null);
    api.backtest(YEAR, duration).then((d) => !stale && setData(d))
      .catch((e: Error) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [duration]);

  const run = () => {
    setBusy(true);
    setError(null);
    api.runBacktest(YEAR, duration).then(setData).catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const runMarkets = () => {
    setMarketsBusy(true);
    setError(null);
    api.runMarkets(YEAR, duration).then(setData).catch((e: Error) => setError(e.message))
      .finally(() => setMarketsBusy(false));
  };

  const runForecast = () => {
    setForecasting(true);
    setError(null);
    api.runForecast(YEAR, duration).then(setData).catch((e: Error) => setError(e.message))
      .finally(() => setForecasting(false));
  };

  const h = data?.headline;
  const fc = data?.forecast;
  const model = fc?.strategies.find((r) => r.strategy === "forecast_fundamentals");
  const pricesOnly = fc?.strategies.find((r) => r.strategy === "forecast_prices");
  const monthly = [...(data?.monthly ?? []), ...(fc?.monthly ?? [])];
  const battery = data?.config?.battery;
  return (
    <div className="grid backtest">
      <Panel title={t("Backtest {year}", { year: YEAR })} area="count"
        code={`${battery ? `${num(battery.capacity_kwh)} KWH / ${num(battery.max_discharge_kw)} KW · ` : ""}CFG ${
          data?.config_hash ?? "------"}`}
        right={
          <Segmented label={t("Battery duration")} value={duration} onChange={setDuration}
            options={[{ value: 1, label: "1H" }, { value: 2, label: "2H" }, { value: 4, label: "4H" }]} />
        }>
        {error && <p className="banner" role="alert">{error}</p>}
        {data && !data.available && (
          <div className="controls">
            <p className="note" style={{ margin: 0 }}>{t("NO RUN STORED FOR THIS SCENARIO.")}</p>
            <button type="button" className="btn" onClick={run} disabled={busy}>
              {t(busy ? "Running full year…" : "Run backtest")}
            </button>
          </div>
        )}
        {data?.available && h && (
          <>
            <div className="tiles four">
              <NumericTile big solid label={t("Optimizer revenue")} unit="€" value={h.optimizer_eur}
                delta={t("€ {v} per MWh capacity", { v: num(h.eur_per_mwh_capacity) })} />
              <NumericTile big label={t("Uplift vs naive rule")} unit="%" digits={1} showSign
                value={h.uplift_vs_naive_pct} delta={t("naive € {v}", { v: num(h.naive_eur) })} />
              <NumericTile big label={t("Full cycles")} value={h.cycles}
                delta={t("{v} per day", { v: num(h.cycles / (data.days ?? 365), 2) })} />
              {model ? (
                <NumericTile big label={t("Forecast model keeps")} unit="%" digits={1}
                  value={model.share_of_optimizer_pct}
                  delta={`€ ${num(model.total_eur)} · ${t("persistence {v} %",
                    { v: num(h.persistence_share_pct, 1) })}`} />
              ) : (
                <NumericTile big label={t("Without foresight")} unit="%" digits={1}
                  value={h.persistence_share_pct}
                  delta={t("persistence € {v}", { v: num(h.persistence_eur) })} />
              )}
            </div>
            <p className="note">
              {t("{days} DAYS · SOURCE {source} · {violations} CONSTRAINT VIOLATIONS · {skipped} DAYS SKIPPED FOR DATA GAPS",
                { days: data.days ?? 0, source: data.source?.toUpperCase() ?? "",
                  violations: data.violations?.length ?? 0,
                  skipped: data.skipped_days?.length ?? 0 })}
            </p>
          </>
        )}
      </Panel>

      {data?.available && (
        <>
          <Panel title={t("Markets")} area="mkt"
            code={data.markets ? `V2 · FCR + AFRR + DAY-AHEAD · CFG ${data.markets.config_hash}`
              : "V2 · FCR + AFRR + DAY-AHEAD"}
            right={data.markets && data.markets.uplift_vs_day_ahead_pct != null
              ? t("+{v} % vs day-ahead only", { v: num(data.markets.uplift_vs_day_ahead_pct) })
              : undefined}>
            {data.markets ? <MarketsPanel m={data.markets} /> : (
              <div className="controls">
                <button type="button" className="btn" onClick={runMarkets}
                  disabled={marketsBusy || !data.markets_possible}>
                  {t(marketsBusy ? "Running multi-market backtest…" : "Run multi-market backtest")}
                </button>
                <span className="label">
                  {t(data.markets_possible
                    ? "Sells FCR and aFRR capacity alongside day-ahead trading."
                    : "Needs an ENTSO-E token: reserve prices are not published on SMARD.")}
                </span>
              </div>
            )}
          </Panel>
          <Panel title={t("Monthly revenue")} code="EUR" area="bars" grid
            right={<button type="button" className="btn" onClick={run} disabled={busy}>
              {t(busy ? "Running…" : "Re-run")}</button>}>
            <MonthlyBars rows={monthly} />
            <p className="note">
              {t("Upper bound: the optimizer plans against prices that are already cleared. A real battery bids before the auction. Persistence plans on yesterday's prices and shows what survives without foresight.")}
            </p>
            {model && (
              <p className="note">
                {t("FORECAST MODEL · price error {mae} €/MWh · plans on forecast prices, paid at real prices. With inputs certain to be known before the auction it keeps {share} %.",
                  { mae: num(model.mae, 1), share: num(pricesOnly?.share_of_optimizer_pct, 1) })}
              </p>
            )}
            {!model && (
              <div className="controls" style={{ marginTop: 10 }}>
                <button type="button" className="btn" onClick={runForecast} disabled={forecasting}>
                  {t(forecasting ? "Training forecast model…" : "Run forecast model")}
                </button>
                <span className="label">
                  {t("Trains a price model and replays the year on it. Several minutes the first time.")}
                </span>
              </div>
            )}
          </Panel>
          <Panel title={t("Special days")} code={t("OPEN IN SCHEDULE")} area="side">
            <ul className="list">
              {data.special_days?.map((d) => (
                <li key={d.label}>
                  <button type="button" onClick={() => onOpenDay(d.date)}>
                    <div className="label">{t(d.label)}</div>
                    <div className="title">{d.date} · {num(d.value, 2)} {d.unit}</div>
                    <div className="dim">
                      {t("OPT")} € {num(d.optimizer_eur, 0)} · {t("NAIVE")} €{" "}
                      {num(d.naive_eur, 0)} · {t("PRICE")}{" "}
                      {num(d.price_min, 0)} → {num(d.price_max, 0)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>
        </>
      )}
    </div>
  );
}
