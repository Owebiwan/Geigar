import { useEffect, useRef, useState } from "react";
import { api, type AppConfig, type Telemetry } from "../api/client";
import type { Sample } from "../api/useTelemetry";
import { PowerSlider, Segmented, StopButton } from "../components/Controls";
import { NumericTile } from "../components/NumericTile";
import { Panel } from "../components/Panel";
import { PowerDial } from "../components/PowerDial";
import { RingGauge } from "../components/RingGauge";
import { TimeSeriesChart } from "../components/TimeSeriesChart";
import { berlinTime, num, signed } from "../format";
import { useT } from "../i18n";

const REASONS: Record<string, string> = {
  schedule: "Following schedule", manual: "Manual setpoint", clamped: "Clamped to power limit",
  stop: "Stopped by operator", fault: "Device fault: output forced to 0 kW",
  comm_loss: "Link lost > 30 s: output forced to 0 kW",
  stale_schedule: "Schedule stale: output forced to 0 kW",
  no_schedule: "No schedule: output forced to 0 kW", soc_max: "SoC at upper limit",
  soc_min: "SoC at lower limit", startup: "Starting",
};

/** One decimal for small values, none once the number needs three digits, so the big
 *  numerals always fit their tile. */
const tight = (v: number | null | undefined) => (v != null && Math.abs(v) >= 100 ? 0 : 1);

const MIN_WAVE = 190;

/** Height for the telemetry chart so the panel fills the screen below the gauges. */
function useFillHeight(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(MIN_WAVE);
  useEffect(() => {
    const measure = () => {
      if (!ref.current) return;
      const top = ref.current.getBoundingClientRect().top + window.scrollY;
      // Room for the readout line, time axis, legend and panel padding below the plot.
      setHeight(Math.max(MIN_WAVE, Math.min(900, window.innerHeight - top - 120)));
    };
    measure();
    // The gauges above settle after fonts load, so measure once more.
    const later = setTimeout(measure, 600);
    window.addEventListener("resize", measure);
    return () => {
      clearTimeout(later);
      window.removeEventListener("resize", measure);
    };
  }, []);
  return [ref, height];
}

interface Props {
  live: Telemetry | null;
  history: Sample[];
  config: AppConfig | null;
}

export function Command({ live, history, config }: Props) {
  const t = useT();
  const [error, setError] = useState<string | null>(null);
  const [waveRef, waveHeight] = useFillHeight();
  const powerHeight = Math.round((waveHeight - 14) * 0.62);
  const reason = t(REASONS[live?.reason ?? ""] ?? live?.reason ?? "---");
  const b = config?.battery;
  const max = b?.max_charge_kw ?? 500;
  const maxDis = b?.max_discharge_kw ?? 500;
  const online = !!live && live.controller_ok !== false;
  const fault = (live?.fault_bits ?? 0) > 0;
  const tripped = online && !!live?.tripped && live.mode !== "STOP";
  const act = (fn: () => Promise<unknown>) => {
    setError(null);
    fn().catch((e: Error) => setError(e.message));
  };
  const code = `${b?.id ?? "BAT-01"} · ${config?.market.zone ?? "DE-LU"}`;

  return (
    <div className="grid command">
      <Panel title={t("State of charge")} code={code} area="soc" grid
        status={fault ? "fault" : online && live?.link_ok ? "on" : "off"}>
        <RingGauge label="SOC" unit="%" min={0} max={100} digits={1} fault={fault}
          value={live?.soc == null ? null : live.soc * 100}
          marks={b ? [b.soc_min * 100, b.soc_max * 100] : undefined} />
        <div className="gauge-row">
          <span>{t("Plan")} <b>{num(live?.planned_soc == null ? null : live.planned_soc * 100, 1)} %</b></span>
          <span>{t("Window")} <b>{b ? `${num(b.soc_min * 100)}–${num(b.soc_max * 100)} %` : "---"}</b></span>
          <span>{t("Temp")} <b>{num(live?.temperature_c, 1)} °C</b></span>
        </div>
      </Panel>

      <Panel title={t("Power")} code={`±${num(max)} KW`} area="power" grid
        status={fault ? "fault" : online && live?.link_ok ? "on" : "off"}>
        <PowerDial value={live?.power_kw} setpoint={live?.setpoint_kw} max={Math.max(max, maxDis)}
          fault={fault} />
        <div className="gauge-row">
          <span>{t("Setpoint")} <b>{signed(live?.setpoint_kw, 0)} kW</b></span>
          <span>{t("Bus")} <b>{num(live?.voltage_v, 1)} V</b></span>
          <span>{t("Interval")} <b>{live?.interval == null ? "---" : String(live.interval).padStart(3, "0")}</b></span>
        </div>
      </Panel>

      <Panel title={t("Readouts")} code={live?.day ?? t("NO DAY")} area="tiles"
        right={live?.ts ? berlinTime(live.ts) : "--:--:--"}>
        <div className="tiles">
          <NumericTile label={t("Price")} unit="€/MWh" value={live?.price_eur_mwh} live
            digits={tight(live?.price_eur_mwh)} />
          <NumericTile label={t("Power")} unit="kW" value={live?.power_kw} showSign live />
          <NumericTile label={t("Revenue today")} unit="€" value={live?.revenue_eur} showSign live solid
            digits={tight(live?.revenue_eur)} />
        </div>
        <div className="gauge-row">
          <span>{t("Charged")} <b>{num(live?.charged_kwh, 0)} kWh</b></span>
          <span>{t("Discharged")} <b>{num(live?.discharged_kwh, 0)} kWh</b></span>
          <span>{t("Re-plans")} <b>{live?.reoptimisations ?? 0}</b></span>
        </div>
        <div className="gauge-row">
          <span>{t("Off schedule")} <b>{num(live?.imbalance_kwh, 1)} kWh</b></span>
          <span>{t("Imbalance settlement")} <b>{live?.imbalance_eur == null ? t("price not published")
            : `${signed(live.imbalance_eur, 2)} €`}</b></span>
        </div>
      </Panel>

      <Panel title={t("Control")} code={t("SAFETY LAYER ACTIVE")} area="control" status={tripped ? "fault" : undefined}>
        <div className="controls">
          <Segmented label={t("Mode")} disabled={!online || live?.mode === "STOP"}
            value={live?.mode === "MANUAL" ? "MANUAL" : "AUTO"}
            options={[{ value: "AUTO", label: t("AUTO") }, { value: "MANUAL", label: t("MANUAL") }]}
            onChange={(mode) => act(() => api.setMode(mode))} />
          <StopButton disabled={!online} stopped={live?.mode === "STOP"}
            onStop={() => act(() => api.setMode("STOP"))}
            onResume={() => act(() => api.setMode("AUTO"))} />
          <PowerSlider min={-maxDis} max={max} value={live?.manual_kw ?? 0}
            disabled={!online || live?.mode !== "MANUAL"}
            onCommit={(kw) => act(() => api.setManual(kw))} />
        </div>
        {!online && <p className="banner" role="alert">{t("Controller offline")}</p>}
        {online && (tripped || fault) && (
          <p className="banner" role="alert">
            {reason}
            {live?.faults?.length ? ` · ${live.faults.map((f) => t(f)).join(" · ")}` : ""}
          </p>
        )}
        {online && !tripped && !fault && (
          <p className="note">{t("STATUS")} · {reason.toUpperCase()}
            {live?.speed && live.speed > 1 ? ` · ${t("REPLAY")} ×${num(live.speed)}`
              : ` · ${t("LIVE")}`}</p>
        )}
        {error && <p className="banner" role="alert">{error}</p>}
      </Panel>

      <Panel title={t("Live telemetry")} code={`${history.length} ${t("SAMPLES")}`} area="wave" grid>
        <div ref={waveRef}>
        {history.length > 1 ? (
          <>
            <TimeSeriesChart label={t("Live power and state of charge")} step={0} scan
              x={history.map((s) => s.t)} formatX={berlinTime}
              panes={[
                { id: "p", label: t("Power"), unit: "kW", height: powerHeight, kind: "line", digits: 0,
                  domain: [-maxDis, max], values: history.map((s) => s.power_kw),
                  compare: { label: t("Setpoint"), values: history.map((s) => s.setpoint_kw) } },
                { id: "s", label: t("SoC"), unit: "%", height: waveHeight - 14 - powerHeight, kind: "line", digits: 1, scale: 100,
                  domain: [0, 100], values: history.map((s) => s.soc) },
              ]} />
            <div className="legend">
              <span><i className="k-line" />{t("Measured")}</span>
              <span><i className="k-dash" />{t("Setpoint")}</span>
            </div>
          </>
        ) : <p className="note">{t("WAITING FOR TELEMETRY…")}</p>}
        </div>
      </Panel>
    </div>
  );
}
