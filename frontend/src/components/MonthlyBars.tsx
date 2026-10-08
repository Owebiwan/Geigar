import { scaleBand, scaleLinear } from "d3";
import { useState } from "react";
import type { MonthRow } from "../api/client";
import { currentLocale, num } from "../format";
import { useT } from "../i18n";

const ALL_SERIES = [
  { key: "optimizer", label: "optimizer", cls: "solid", legend: "k-solid" },
  { key: "forecast_fundamentals", label: "forecast model", cls: "dimfill", legend: "k-dim" },
  { key: "naive", label: "naive", cls: "hatched", legend: "k-hatch" },
  { key: "persistence", label: "persistence", cls: "outlined", legend: "k-outline" },
] as const;
const W = 900;
const H = 300;
const M = { left: 56, right: 10, top: 14, bottom: 24 };
const monthName = (month: string) =>
  new Date(`${month}-15T12:00:00Z`).toLocaleString(currentLocale(), { month: "short", timeZone: "UTC" })
    .replace(".", "").toUpperCase();

/** Monthly revenue per strategy. Series differ by texture and brightness, not hue. */
export function MonthlyBars({ rows }: { rows: MonthRow[] }) {
  const t = useT();
  const [hover, setHover] = useState<string | null>(null);
  // The forecast series only appears once the model has been run for this scenario.
  const SERIES = ALL_SERIES.filter((s) => rows.some((r) => r.strategy === s.key));
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const value = (month: string, strategy: string) =>
    rows.find((r) => r.month === month && r.strategy === strategy)?.total_eur ?? 0;
  const all = months.flatMap((m) => SERIES.map((s) => value(m, s.key)));
  const y = scaleLinear().domain([Math.min(0, ...all), Math.max(1, ...all)]).nice(5)
    .range([H - M.bottom, M.top]);
  const band = scaleBand().domain(months).range([M.left, W - M.right]).paddingInner(0.28)
    .paddingOuter(0.1);
  const inner = scaleBand().domain(SERIES.map((s) => s.key)).range([0, band.bandwidth()])
    .paddingInner(0.12);

  return (
    <div className="chart">
      <div className="readout">
        {hover ? (
          <>
            <span>{t("Month")} <b>{hover}</b></span>
            {SERIES.map((s) => (
              <span key={s.key}>{t(s.label)} <b>€ {num(value(hover, s.key))}</b></span>
            ))}
          </>
        ) : <span>{t("Hover a month for values")}</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" tabIndex={0}
        aria-label={`${t("Monthly revenue")}: ${SERIES.map((s) => t(s.label)).join(", ")}`}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          const i = hover ? months.indexOf(hover) : -1;
          if (e.key === "ArrowRight") setHover(months[Math.min(months.length - 1, i + 1)]);
          if (e.key === "ArrowLeft") setHover(months[Math.max(0, i - 1)]);
        }}>
        {y.ticks(5).map((t) => (
          <g key={t}>
            <line className="gridline" x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={M.left - 6} y={y(t) + 3} textAnchor="end">{num(t)}</text>
          </g>
        ))}
        <text className="tick" x={M.left + 4} y={M.top + 2}>{t("EUR / MONTH")}</text>
        {months.map((m) => (
          <g key={m} onPointerEnter={() => setHover(m)}>
            <rect x={(band(m) ?? 0) - 6} y={M.top} width={band.bandwidth() + 12}
              height={H - M.top - M.bottom}
              fill={hover === m ? "rgba(255,255,255,0.06)" : "transparent"} />
            {SERIES.map((s) => {
              const v = value(m, s.key);
              return (
                <rect key={s.key} className={s.cls} x={(band(m) ?? 0) + (inner(s.key) ?? 0)}
                  y={Math.min(y(v), y(0))} width={inner.bandwidth()} height={Math.abs(y(v) - y(0))} />
              );
            })}
            <text className="tick" x={(band(m) ?? 0) + band.bandwidth() / 2} y={H - 7}
              textAnchor="middle">{monthName(m)}</text>
          </g>
        ))}
        <line className="axis" x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} />
      </svg>
      <div className="legend">
        {SERIES.map((s) => <span key={s.key}><i className={s.legend} />{t(s.label)}</span>)}
      </div>
    </div>
  );
}
