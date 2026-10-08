import type { MarketsResult } from "../api/client";
import { num } from "../format";
import { useT } from "../i18n";

const W = 900;
const ROW = 26;
const LEFT = 150;

/** Revenue by source, multi-market against day-ahead only. Segments differ by texture. */
export function MarketsPanel({ m }: { m: MarketsResult }) {
  const t = useT();
  const segments = [
    { key: "Day-ahead energy", cls: "solid", legend: "k-solid",
      value: Math.max(0, m.energy_eur - m.degradation_eur) },
    { key: "FCR", cls: "dimfill", legend: "k-dim", value: Math.max(0, m.fcr_eur - m.reserve_wear_eur) },
    { key: "aFRR up", cls: "hatched", legend: "k-hatch", value: m.afrr_up_eur },
    { key: "aFRR down", cls: "outlined", legend: "k-outline", value: m.afrr_dn_eur },
  ];
  const max = Math.max(m.total_eur, m.day_ahead_only_eur, 1);
  const scale = (v: number) => (v / max) * (W - LEFT - 90);
  let x = LEFT;
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${ROW * 2 + 22}`} role="img"
        aria-label={t("Revenue by market: multi-market {a} euro, day-ahead only {b} euro",
          { a: num(m.total_eur), b: num(m.day_ahead_only_eur) })}>
        <text className="tick" x={0} y={ROW - 8}>{t("MULTI-MARKET")}</text>
        {segments.map((s) => {
          const w = scale(s.value);
          const rect = (
            <rect key={s.key} className={s.cls} x={x} y={2} width={Math.max(0, w - 2)}
              height={ROW - 6}><title>{t(s.key)}: € {num(s.value)}</title></rect>
          );
          x += w;
          return rect;
        })}
        <text className="tick" x={x + 6} y={ROW - 8} style={{ fill: "var(--text)" }}>
          € {num(m.total_eur)}
        </text>
        <text className="tick" x={0} y={ROW * 2 - 2}>{t("DAY-AHEAD ONLY")}</text>
        <rect className="solid" x={LEFT} y={ROW + 8} width={scale(m.day_ahead_only_eur)}
          height={ROW - 6} />
        <text className="tick" x={LEFT + scale(m.day_ahead_only_eur) + 6} y={ROW * 2 - 2}
          style={{ fill: "var(--text)" }}>€ {num(m.day_ahead_only_eur)}</text>
      </svg>
      <div className="legend">
        {segments.map((s) => (
          <span key={s.key}><i className={s.legend} />{t(s.key)} € {num(s.value)}</span>
        ))}
      </div>
      <dl className="kv markets-kv">
        <dt>{t("Per MW of power, per year")}</dt><dd>€ {num(m.eur_per_mw_year)}</dd>
        <dt>{t("Average band sold")}</dt>
        <dd>FCR {num(m.fcr_kw)} kW · aFRR {t("up")} {num(m.afrr_up_kw)} kW · {t("down")}{" "}
          {num(m.afrr_dn_kw)} kW</dd>
        <dt>{t("Energy held per kW sold")}</dt>
        <dd>FCR {num(m.assumptions.fcr_energy_hours, 2)} h · aFRR{" "}
          {num(m.assumptions.afrr_energy_hours, 1)} h</dd>
        <dt>{t("Full cycles · violations")}</dt><dd>{num(m.cycles)} · {m.violations}</dd>
        {m.imbalance && (
          <>
            <dt>{t("Imbalance vs day-ahead price")}</dt>
            <dd>{t("mean gap {a} €/MWh · 95 % below {b}", {
              a: num(m.imbalance.mean_abs_gap_eur_mwh, 1),
              b: num(m.imbalance.p95_abs_gap_eur_mwh, 0) })}</dd>
          </>
        )}
      </dl>
      <p className="note">
        {t("Capacity payments only, at cleared prices and with perfect foresight of three auctions: an upper bound. Activation energy is not modelled, and a 500 kW unit would need pooling to reach the 1 MW minimum bid.")}
      </p>
    </div>
  );
}
