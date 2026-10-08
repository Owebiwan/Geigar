import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SystemResponse, type Telemetry } from "../api/client";
import { Panel } from "../components/Panel";
import { TickRing } from "../components/RingGauge";
import { berlinDate, berlinTime, num, signed } from "../format";
import { useT, type Translate } from "../i18n";

const stamp = (iso: string | null | undefined, t: Translate) =>
  iso ? `${berlinDate(iso)} ${berlinTime(iso)}` : t("NEVER");

/** Controller reason codes as short upper-case labels. */
const REASON_LABELS: Record<string, string> = {
  schedule: "SCHEDULE_REASON", manual: "MANUAL", stop: "STOP", fault: "FAULT",
};
const reasonText = (reason: string, t: Translate) => {
  const key = REASON_LABELS[reason];
  if (!key) return reason.toUpperCase();
  const text = t(key);
  return text === "SCHEDULE_REASON" ? "SCHEDULE" : text;
};

function HeartbeatRing({ beat, ok }: { beat: number | null | undefined; ok: boolean }) {
  const t = useT();
  return (
    <svg className="gauge" viewBox="0 0 200 200" style={{ maxWidth: 200 }} role="img"
      aria-label={t(ok ? "Modbus link up, heartbeat {beat}" : "Modbus link down, heartbeat {beat}",
        { beat: beat ?? t("none") })}>
      <TickRing />
      <circle cx="100" cy="100" r="62" fill="none" stroke={ok ? "var(--line)" : "var(--alert)"}
        strokeWidth="1.5" />
      {/* Re-mounting on every heartbeat change replays the pulse. */}
      {ok && <circle key={beat ?? 0} className="beat" cx="100" cy="100" r="62" fill="none"
        stroke="var(--line)" strokeWidth="2" />}
      <text x="100" y="104" textAnchor="middle" fontSize="20" fontWeight="600"
        fill={ok ? "var(--text)" : "var(--alert)"}>{t(ok ? "LINK UP" : "LINK DOWN")}</text>
      <text className="cap" x="100" y="122" textAnchor="middle">HB {beat ?? "-----"}</text>
    </svg>
  );
}

export function System({ live }: { live: Telemetry | null }) {
  const t = useT();
  const [data, setData] = useState<SystemResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);

  const refresh = useCallback(() => {
    api.system().then((d) => mounted.current && setData(d))
      .catch((e: Error) => mounted.current && setError(e.message));
  }, []);
  useEffect(() => {
    mounted.current = true;
    refresh();
    return () => { mounted.current = false; };
  }, [refresh]);

  const act = (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    fn().catch((e: Error) => setError(e.message)).finally(() => {
      setBusy(false);
      refresh();
      setTimeout(refresh, 1500); // the controller logs the resulting event a cycle later
    });
  };

  const c = live?.ts ? live : data?.controller;
  const online = !!c && c.controller_ok !== false;
  const linkOk = online && !!c?.link_ok;
  const faults = c?.faults ?? [];
  const feed = data?.feed;
  const safety = data?.events.filter((e) => e.kind === "safety") ?? [];

  return (
    <div className="grid system">
      <Panel title={t("Modbus link")} code={c?.modbus ?? "TCP"} area="link"
        status={!linkOk || faults.length ? "fault" : "on"}>
        <HeartbeatRing beat={c?.heartbeat} ok={linkOk} />
        <dl className="kv">
          <dt>{t("Controller")}</dt>
          <dd className={online ? "" : "alert"}>{t(online ? "ONLINE" : "OFFLINE")}</dd>
          <dt>{t("Last reply")}</dt><dd>{num(c?.link_age_s, 1)} {t("s ago")}</dd>
          <dt>{t("Mode")}</dt><dd>{c?.mode ? t(c.mode) : "---"}</dd>
          <dt>{t("Decision")}</dt><dd>{c?.reason ? reasonText(c.reason, t) : "---"}</dd>
          <dt>{t("Faults")}</dt>
          <dd className={faults.length ? "alert" : ""}>
            {faults.length ? faults.map((f) => t(f)).join(" · ") : t("NONE")}</dd>
        </dl>
        <div className="controls" style={{ marginTop: 12 }}>
          <button type="button" className="btn" disabled={busy || !online}
            onClick={() => act(() => api.simFault(!faults.includes("DEVICE FAULT")))}>
            {t(faults.includes("DEVICE FAULT") ? "Clear simulated fault" : "Inject simulated fault")}
          </button>
        </div>
      </Panel>

      <Panel title={t("Register readout")}
        code={`${t("UNIT")} 1 · ${data?.registers.length ?? "--"} REG`} area="reg">
        <div className="table-wrap">
          <table>
            <thead><tr><th>{t("Name")}</th><th>{t("Type")}</th><th>{t("Addr")}</th><th>{t("Raw")}</th>
              <th>{t("Value")}</th><th>{t("Unit")}</th></tr></thead>
            <tbody>
              {data?.registers.map((r) => {
                const raw = r.name === "WSET"
                  ? (c?.setpoint_kw == null ? undefined : Math.round(c.setpoint_kw / r.scale))
                  : c?.registers?.[r.name];
                const signedRaw = raw != null && raw >= 0x8000 && (r.name === "W" || r.name === "TMP")
                  ? raw - 0x10000 : raw;
                return (
                  <tr key={`${r.kind}-${r.address}`}
                    className={r.name === "FAULT" && (raw ?? 0) > 0 ? "fault" : undefined}>
                    <td title={r.description}>{r.name}</td>
                    <td>{r.kind === "input" ? "IR" : "HR"}</td>
                    <td>{String(r.address).padStart(3, "0")}</td>
                    <td>{raw ?? "---"}</td>
                    <td>{signedRaw == null ? "---" : num(signedRaw * r.scale, r.scale < 1 ? 1 : 0)}</td>
                    <td>{r.unit}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title={t("Data feed")} code={feed?.source.toUpperCase() ?? "---"} area="feed"
        status={feed ? (feed.quality.ok ? "on" : "fault") : "off"}
        right={<button type="button" className="btn" onClick={refresh}>{t("Refresh")}</button>}>
        <dl className="kv">
          <dt>{t("Source")}</dt><dd>{feed?.source.toUpperCase() ?? "---"}</dd>
          <dt>{t("ENTSO-E token")}</dt>
          <dd>{t(feed?.entsoe_token ? "SET" : "NOT SET · USING SMARD")}</dd>
          <dt>{t("Last fetch")}</dt><dd>{stamp(feed?.last_fetch, t)}</dd>
          <dt>{t("Daily job")}</dt>
          <dd>{feed ? t(feed.daily_job.detail).toUpperCase() : "---"}</dd>
          <dt>{t("Job last run")}</dt><dd>{stamp(feed?.daily_job.last_run, t)}</dd>
          <dt>{t("Last {n} days", { n: feed?.window_days ?? "--" })}</dt>
          <dd>{num(feed?.quality.rows)} {t("ROWS")}</dd>
          <dt>{t("Gaps")}</dt>
          <dd className={feed?.quality.gaps.length ? "alert" : ""}>{feed?.quality.gaps.length ?? "---"}</dd>
          <dt>{t("Duplicates")}</dt>
          <dd className={feed?.quality.duplicates.length ? "alert" : ""}>
            {feed?.quality.duplicates.length ?? "---"}</dd>
        </dl>
        {feed?.error && <p className="banner" role="alert">{t("Fetch failed: {e}", { e: feed.error })}</p>}
        {feed?.quality.gaps.slice(0, 4).map((g) => (
          <p key={g.start} className="note">{t("GAP")} {stamp(g.start, t)} → {stamp(g.end, t)}</p>
        ))}
        <div className="controls" style={{ marginTop: 12 }}>
          <button type="button" className="btn" disabled={busy} onClick={() => act(api.fetchNow)}>
            {t("Fetch next day now")}
          </button>
        </div>
        {error && <p className="banner" role="alert">{error}</p>}
      </Panel>

      <Panel title={t("Fault and dispatch log")} code={`${safety.length} ${t("SAFETY EVENTS")}`} area="log"
        status={safety.length ? "fault" : undefined}>
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t("Fault and dispatch log")}>
          <table>
            <thead><tr><th>{t("Time (sim)")}</th><th>{t("Kind")}</th><th>{t("Reason")}</th>
              <th>{t("Setpoint kW")}</th><th>{t("Measured kW")}</th><th>{t("SoC %")}</th></tr></thead>
            <tbody>
              {data?.events.slice(0, 60).map((e, i) => (
                <tr key={`${e.ts}-${i}`} className={e.kind === "safety" ? "fault" : undefined}>
                  <td>{stamp(e.ts, t)}</td>
                  <td>{t(e.kind.toUpperCase())}</td>
                  <td>{reasonText(e.reason, t)}</td>
                  <td>{signed(e.setpoint_kw, 1)}</td>
                  <td>{signed(e.measured_kw, 1)}</td>
                  <td>{num(e.soc == null ? null : e.soc * 100, 1)}</td>
                </tr>
              ))}
              {!data?.events.length && <tr><td colSpan={6}>{t("NO EVENTS")}</td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
