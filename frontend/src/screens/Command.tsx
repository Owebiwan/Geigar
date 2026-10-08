import { useState } from "react";
import { api, type AppConfig, type Telemetry } from "../api/client";
import type { Sample } from "../api/useTelemetry";
import { PowerSlider, Segmented, StopButton } from "../components/Controls";
import { NumericTile } from "../components/NumericTile";
import { Panel } from "../components/Panel";
import { PowerDial } from "../components/PowerDial";
import { RingGauge } from "../components/RingGauge";
import { TimeSeriesChart } from "../components/TimeSeriesChart";
import { berlinTime, num, signed } from "../format";

const REASONS: Record<string, string> = {
  schedule: "Following schedule", manual: "Manual setpoint", clamped: "Clamped to power limit",
  stop: "Stopped by operator", fault: "Device fault: output forced to 0 kW",
  comm_loss: "Link lost > 30 s: output forced to 0 kW",
  stale_schedule: "Schedule stale: output forced to 0 kW",
  no_schedule: "No schedule: output forced to 0 kW", soc_max: "SoC at upper limit",
  soc_min: "SoC at lower limit", startup: "Starting",
};

interface Props {
  live: Telemetry | null;
  history: Sample[];
  config: AppConfig | null;
}

export function Command({ live, history, config }: Props) {
  const [error, setError] = useState<string | null>(null);
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
      <Panel title="State of charge" code={code} area="soc" grid
        status={fault ? "fault" : online && live?.link_ok ? "on" : "off"}>
        <RingGauge label="SOC" unit="%" min={0} max={100} digits={1} fault={fault}
          value={live?.soc == null ? null : live.soc * 100}
          marks={b ? [b.soc_min * 100, b.soc_max * 100] : undefined} />
        <div className="gauge-row">
          <span>Plan <b>{num(live?.planned_soc == null ? null : live.planned_soc * 100, 1)} %</b></span>
          <span>Window <b>{b ? `${num(b.soc_min * 100)}–${num(b.soc_max * 100)} %` : "---"}</b></span>
          <span>Temp <b>{num(live?.temperature_c, 1)} °C</b></span>
        </div>
      </Panel>

      <Panel title="Power" code={`±${num(max)} KW`} area="power" grid
        status={fault ? "fault" : online && live?.link_ok ? "on" : "off"}>
        <PowerDial value={live?.power_kw} setpoint={live?.setpoint_kw} max={Math.max(max, maxDis)}
          fault={fault} />
        <div className="gauge-row">
          <span>Setpoint <b>{signed(live?.setpoint_kw, 0)} kW</b></span>
          <span>Bus <b>{num(live?.voltage_v, 1)} V</b></span>
          <span>Interval <b>{live?.interval == null ? "---" : String(live.interval).padStart(3, "0")}</b></span>
        </div>
      </Panel>

      <Panel title="Readouts" code={live?.day ?? "NO DAY"} area="tiles"
        right={live?.ts ? berlinTime(live.ts) : "--:--:--"}>
        <div className="tiles">
          <NumericTile label="Price" unit="€/MWh" value={live?.price_eur_mwh} digits={1} live />
          <NumericTile label="Power" unit="kW" value={live?.power_kw} showSign live />
          <NumericTile label="Revenue today" unit="€" value={live?.revenue_eur} digits={1} showSign
            live solid />
        </div>
        <div className="gauge-row">
          <span>Charged <b>{num(live?.charged_kwh, 0)} kWh</b></span>
          <span>Discharged <b>{num(live?.discharged_kwh, 0)} kWh</b></span>
          <span>Re-plans <b>{live?.reoptimisations ?? 0}</b></span>
        </div>
      </Panel>

      <Panel title="Control" code="SAFETY LAYER ACTIVE" area="control" status={tripped ? "fault" : undefined}>
        <div className="controls">
          <Segmented label="Mode" disabled={!online || live?.mode === "STOP"}
            value={live?.mode === "MANUAL" ? "MANUAL" : "AUTO"}
            options={[{ value: "AUTO", label: "AUTO" }, { value: "MANUAL", label: "MANUAL" }]}
            onChange={(mode) => act(() => api.setMode(mode))} />
          <StopButton disabled={!online} stopped={live?.mode === "STOP"}
            onStop={() => act(() => api.setMode("STOP"))}
            onResume={() => act(() => api.setMode("AUTO"))} />
          <PowerSlider min={-maxDis} max={max} value={live?.manual_kw ?? 0}
            disabled={!online || live?.mode !== "MANUAL"}
            onCommit={(kw) => act(() => api.setManual(kw))} />
        </div>
        {!online && <p className="banner" role="alert">Controller offline</p>}
        {online && (tripped || fault) && (
          <p className="banner" role="alert">
            {REASONS[live?.reason ?? ""] ?? live?.reason}
            {live?.faults?.length ? ` · ${live.faults.join(" · ")}` : ""}
          </p>
        )}
        {online && !tripped && !fault && (
          <p className="note">STATUS · {(REASONS[live?.reason ?? ""] ?? "---").toUpperCase()}
            {live?.speed && live.speed > 1 ? ` · REPLAY ×${num(live.speed)}` : " · LIVE"}</p>
        )}
        {error && <p className="banner" role="alert">{error}</p>}
      </Panel>

      <Panel title="Live telemetry" code={`${history.length} SAMPLES`} area="wave" grid>
        {history.length > 1 ? (
          <>
            <TimeSeriesChart label="Live power and state of charge" step={0} scan
              x={history.map((s) => s.t)} formatX={berlinTime}
              panes={[
                { id: "p", label: "Power", unit: "kW", height: 120, kind: "line", digits: 0,
                  domain: [-maxDis, max], values: history.map((s) => s.power_kw),
                  compare: { label: "Setpoint", values: history.map((s) => s.setpoint_kw) } },
                { id: "s", label: "SoC", unit: "%", height: 70, kind: "line", digits: 1, scale: 100,
                  domain: [0, 100], values: history.map((s) => s.soc) },
              ]} />
            <div className="legend">
              <span><i className="k-line" />Measured</span>
              <span><i className="k-dash" />Setpoint</span>
            </div>
          </>
        ) : <p className="note">WAITING FOR TELEMETRY…</p>}
      </Panel>
    </div>
  );
}
