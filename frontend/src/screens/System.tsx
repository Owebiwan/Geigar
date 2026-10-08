import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SystemResponse, type Telemetry } from "../api/client";
import { Panel } from "../components/Panel";
import { TickRing } from "../components/RingGauge";
import { berlinDate, berlinTime, num, signed } from "../format";

const stamp = (iso: string | null | undefined) =>
  iso ? `${berlinDate(iso)} ${berlinTime(iso)}` : "NEVER";

function HeartbeatRing({ beat, ok }: { beat: number | null | undefined; ok: boolean }) {
  return (
    <svg className="gauge" viewBox="0 0 200 200" style={{ maxWidth: 200 }} role="img"
      aria-label={`Modbus link ${ok ? "up" : "down"}, heartbeat ${beat ?? "none"}`}>
      <TickRing />
      <circle cx="100" cy="100" r="62" fill="none" stroke={ok ? "var(--line)" : "var(--alert)"}
        strokeWidth="1.5" />
      {/* Re-mounting on every heartbeat change replays the pulse. */}
      {ok && <circle key={beat ?? 0} className="beat" cx="100" cy="100" r="62" fill="none"
        stroke="var(--line)" strokeWidth="2" />}
      <text x="100" y="104" textAnchor="middle" fontSize="20" fontWeight="600"
        fill={ok ? "var(--text)" : "var(--alert)"}>{ok ? "LINK UP" : "LINK DOWN"}</text>
      <text className="cap" x="100" y="122" textAnchor="middle">HB {beat ?? "-----"}</text>
    </svg>
  );
}

export function System({ live }: { live: Telemetry | null }) {
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
      <Panel title="Modbus link" code={c?.modbus ?? "TCP"} area="link"
        status={!linkOk || faults.length ? "fault" : "on"}>
        <HeartbeatRing beat={c?.heartbeat} ok={linkOk} />
        <dl className="kv">
          <dt>Controller</dt><dd className={online ? "" : "alert"}>{online ? "ONLINE" : "OFFLINE"}</dd>
          <dt>Last reply</dt><dd>{num(c?.link_age_s, 1)} s ago</dd>
          <dt>Mode</dt><dd>{c?.mode ?? "---"}</dd>
          <dt>Decision</dt><dd>{c?.reason?.toUpperCase() ?? "---"}</dd>
          <dt>Faults</dt>
          <dd className={faults.length ? "alert" : ""}>{faults.length ? faults.join(" · ") : "NONE"}</dd>
        </dl>
        <div className="controls" style={{ marginTop: 12 }}>
          <button type="button" className="btn" disabled={busy || !online}
            onClick={() => act(() => api.simFault(!faults.includes("DEVICE FAULT")))}>
            {faults.includes("DEVICE FAULT") ? "Clear simulated fault" : "Inject simulated fault"}
          </button>
        </div>
      </Panel>

      <Panel title="Register readout" code={`UNIT 1 · ${data?.registers.length ?? "--"} REG`} area="reg">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Type</th><th>Addr</th><th>Raw</th><th>Value</th><th>Unit</th></tr></thead>
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

      <Panel title="Data feed" code={feed?.source.toUpperCase() ?? "---"} area="feed"
        status={feed ? (feed.quality.ok ? "on" : "fault") : "off"}
        right={<button type="button" className="btn" onClick={refresh}>Refresh</button>}>
        <dl className="kv">
          <dt>Source</dt><dd>{feed?.source.toUpperCase() ?? "---"}</dd>
          <dt>ENTSO-E token</dt><dd>{feed?.entsoe_token ? "SET" : "NOT SET · USING SMARD"}</dd>
          <dt>Last fetch</dt><dd>{stamp(feed?.last_fetch)}</dd>
          <dt>Daily job</dt><dd>{feed?.daily_job.detail.toUpperCase() ?? "---"}</dd>
          <dt>Job last run</dt><dd>{stamp(feed?.daily_job.last_run)}</dd>
          <dt>Last {feed?.window_days ?? "--"} days</dt>
          <dd>{num(feed?.quality.rows)} ROWS</dd>
          <dt>Gaps</dt>
          <dd className={feed?.quality.gaps.length ? "alert" : ""}>{feed?.quality.gaps.length ?? "---"}</dd>
          <dt>Duplicates</dt>
          <dd className={feed?.quality.duplicates.length ? "alert" : ""}>
            {feed?.quality.duplicates.length ?? "---"}</dd>
        </dl>
        {feed?.error && <p className="banner" role="alert">Fetch failed: {feed.error}</p>}
        {feed?.quality.gaps.slice(0, 4).map((g) => (
          <p key={g.start} className="note">GAP {stamp(g.start)} → {stamp(g.end)}</p>
        ))}
        <div className="controls" style={{ marginTop: 12 }}>
          <button type="button" className="btn" disabled={busy} onClick={() => act(api.fetchNow)}>
            Fetch next day now
          </button>
        </div>
        {error && <p className="banner" role="alert">{error}</p>}
      </Panel>

      <Panel title="Fault and dispatch log" code={`${safety.length} SAFETY EVENTS`} area="log"
        status={safety.length ? "fault" : undefined}>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Fault and dispatch log">
          <table>
            <thead><tr><th>Time (sim)</th><th>Kind</th><th>Reason</th><th>Setpoint kW</th>
              <th>Measured kW</th><th>SoC %</th></tr></thead>
            <tbody>
              {data?.events.slice(0, 60).map((e, i) => (
                <tr key={`${e.ts}-${i}`} className={e.kind === "safety" ? "fault" : undefined}>
                  <td>{stamp(e.ts)}</td>
                  <td>{e.kind.toUpperCase()}</td>
                  <td>{e.reason.toUpperCase()}</td>
                  <td>{signed(e.setpoint_kw, 1)}</td>
                  <td>{signed(e.measured_kw, 1)}</td>
                  <td>{num(e.soc == null ? null : e.soc * 100, 1)}</td>
                </tr>
              ))}
              {!data?.events.length && <tr><td colSpan={6}>NO EVENTS</td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
