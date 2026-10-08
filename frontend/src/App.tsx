import { useEffect, useState } from "react";
import { api, type AppConfig } from "./api/client";
import { useTelemetry } from "./api/useTelemetry";
import { Segmented } from "./components/Controls";
import { TabBar, type Tab } from "./components/TabBar";
import { berlinDate, berlinTime, shiftDay } from "./format";
import { useI18n, type Lang } from "./i18n";
import { Backtest } from "./screens/Backtest";
import { Command } from "./screens/Command";
import { Schedule } from "./screens/Schedule";
import { System } from "./screens/System";

function usePhone(): boolean {
  const query = "(max-width: 639px)";
  const [phone, setPhone] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setPhone(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return phone;
}

export function App() {
  const { lang, setLang, t } = useI18n();
  const [tab, setTab] = useState<Tab>("COMMAND");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [day, setDay] = useState(() => shiftDay(berlinDate(Date.now()), 1));
  const { live, history, connected } = useTelemetry();
  const phone = usePhone();
  const screen: Tab = phone ? "COMMAND" : tab; // phones show COMMAND only (SC-6)

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
  }, []);

  const online = connected && live?.controller_ok !== false;
  const fault = (live?.fault_bits ?? 0) > 0 || (online && live?.link_ok === false);
  const openDay = (d: string) => {
    setDay(d);
    setTab("SCHEDULE");
  };

  return (
    <div className="app">
      {/* Shared SVG paint servers: hatch for discharge, gradient for the scanline. */}
      <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
        <defs>
          <pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="5" stroke="rgba(255,255,255,0.85)" strokeWidth="1.5" />
          </pattern>
          <linearGradient id="scan" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="#fff" stopOpacity="0" />
            <stop offset="1" stopColor="#fff" stopOpacity="0.05" />
          </linearGradient>
        </defs>
      </svg>

      <header className="topbar">
        <span className="brand">BDO // DISPATCH</span>
        <span className="codes">
          {config?.battery.id ?? "BAT-01"} · {config?.market.zone ?? "DE-LU"} · INT 15M
        </span>
        <span className="rule" aria-hidden="true" />
        <TabBar active={screen} onChange={setTab} />
        <span className="rule" aria-hidden="true" />
        <span className="codes" role="status">
          <span className={`dot ${fault ? "fault" : online ? "on pulse" : ""}`} aria-hidden="true" />
          {t(!connected ? "API OFFLINE" : !online ? "CONTROLLER OFFLINE" : fault ? "FAULT"
            : "ONLINE")}
          {live?.ts ? ` · ${berlinDate(live.ts)} ${berlinTime(live.ts)}` : ""}
        </span>
        <Segmented<Lang> label={t("Language")} value={lang} onChange={setLang}
          options={[{ value: "en", label: "EN" }, { value: "de", label: "DE" }]} />
      </header>

      <main id="screen" role="tabpanel" aria-labelledby={`tab-${screen}`}>
        {screen === "COMMAND" && <Command live={live} history={history} config={config} />}
        {screen === "SCHEDULE" && <Schedule day={day} onDay={setDay} config={config} />}
        {screen === "BACKTEST" && <Backtest onOpenDay={openDay} />}
        {screen === "SYSTEM" && <System live={live} />}
      </main>
    </div>
  );
}
