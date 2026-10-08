export interface Telemetry {
  controller_ok?: boolean;
  ts?: string;
  mode?: "AUTO" | "MANUAL" | "STOP";
  link_ok: boolean;
  link_age_s?: number;
  setpoint_kw?: number;
  reason?: string;
  tripped?: boolean;
  manual_kw?: number;
  interval?: number | null;
  price_eur_mwh?: number | null;
  planned_soc?: number | null;
  revenue_eur?: number;
  charged_kwh?: number;
  discharged_kwh?: number;
  imbalance_kwh?: number;
  imbalance_eur?: number | null;
  soc?: number | null;
  power_kw?: number | null;
  voltage_v?: number | null;
  temperature_c?: number | null;
  fault_bits?: number;
  faults?: string[];
  heartbeat?: number | null;
  registers?: Record<string, number>;
  speed?: number;
  replay_day?: string | null;
  day?: string | null;
  reoptimisations?: number;
  battery_id?: string;
  modbus?: string;
  error?: string;
}

export interface AppConfig {
  battery: {
    id: string; capacity_kwh: number; max_charge_kw: number; max_discharge_kw: number;
    eta_charge: number; eta_discharge: number; soc_min: number; soc_max: number;
  };
  market: { zone: string };
  config_hash: string;
  source: string;
}

export interface ScheduleRow {
  ts_utc: string; local: string; price_eur_mwh: number; power_kw: number; soc_start: number;
  soc: number; grid_kw: number; load_kw: number; solar_kw: number; cash_flow_eur: number;
  naive_power_kw: number;
}

export interface Quality {
  rows: number; ok: boolean; duplicates: string[]; gaps: { start: string; end: string }[];
}

export interface ScheduleResponse {
  date: string; available: boolean; reason?: string; resolution_min?: number; intervals?: number;
  provisional?: boolean; forecast_model?: string | null;
  rows?: ScheduleRow[];
  summary?: {
    revenue_eur: number; naive_eur: number; cycles: number; price_min: number; price_max: number;
    solve_ms: number;
  };
  quality: Quality;
}

export interface MonthRow {
  month: string; strategy: string; total_eur: number; revenue_eur: number; cycles: number;
  peak_savings_eur: number;
}

export interface SpecialDay {
  label: string; date: string; value: number; unit: string; optimizer_eur: number;
  naive_eur: number; price_min: number; price_max: number;
}

export interface ForecastRow {
  strategy: string; total_eur: number; share_of_optimizer_pct: number; cycles: number;
  mae: number | null; shape_corr: number | null;
}

export interface ForecastResult {
  year: number; days: number; last_day: string;
  strategies: ForecastRow[];
  monthly: MonthRow[];
  quarterly_share_pct: Record<string, Record<string, number>>;
}

export interface MarketsResult {
  total_eur: number; day_ahead_only_eur: number; uplift_vs_day_ahead_pct: number | null;
  eur_per_mw_year: number; energy_eur: number; degradation_eur: number; fcr_eur: number;
  afrr_up_eur: number; afrr_dn_eur: number; reserve_wear_eur: number; fcr_kw: number;
  afrr_up_kw: number; afrr_dn_kw: number; cycles: number; violations: number;
  config_hash: string;
  imbalance: { mean_abs_gap_eur_mwh: number; p95_abs_gap_eur_mwh: number } | null;
  assumptions: { fcr_energy_hours: number; afrr_energy_hours: number };
}

export interface BacktestResponse {
  available: boolean; year: number; config_hash: string; source?: string; days?: number;
  headline?: {
    optimizer_eur: number; naive_eur: number; persistence_eur: number;
    uplift_vs_naive_pct: number | null; uplift_vs_naive_eur: number;
    persistence_share_pct: number | null;
    eur_per_mwh_capacity: number; cycles: number; peak_savings_eur: number;
  };
  monthly?: MonthRow[];
  special_days?: SpecialDay[];
  skipped_days?: string[];
  violations?: string[];
  config?: { battery: AppConfig["battery"] };
  forecast?: ForecastResult | null;
  markets?: MarketsResult | null;
  markets_possible?: boolean;
}

export interface ControllerEvent {
  ts: string; kind: "dispatch" | "safety"; reason: string; setpoint_kw: number;
  measured_kw: number | null; soc: number | null;
}

export interface SystemResponse {
  controller: Telemetry;
  events: ControllerEvent[];
  feed: {
    source: string; entsoe_token: boolean; last_fetch: string | null;
    daily_job: { last_run: string | null; ok: boolean | null; detail: string };
    window_days: number; quality: Quality; error?: string | null;
  };
  registers: {
    address: number; name: string; kind: string; scale: number; unit: string; description: string;
  }[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  config: () => request<AppConfig>("/config"),
  schedule: (day?: string) => request<ScheduleResponse>(`/schedule${day ? `?day=${day}` : ""}`),
  backtest: (year: number, duration?: number) =>
    request<BacktestResponse>(`/backtest?year=${year}${duration ? `&duration=${duration}` : ""}`),
  runBacktest: (year: number, duration?: number) =>
    post<BacktestResponse>(`/backtest/run?year=${year}${duration ? `&duration=${duration}` : ""}`),
  runMarkets: (year: number, duration?: number) =>
    post<BacktestResponse>(`/markets/run?year=${year}${duration ? `&duration=${duration}` : ""}`),
  runForecast: (year: number, duration?: number) =>
    post<BacktestResponse>(`/forecast/run?year=${year}${duration ? `&duration=${duration}` : ""}`),
  system: () => request<SystemResponse>("/system"),
  setMode: (mode: string) => post<Telemetry>("/mode", { mode }),
  setManual: (power_kw: number) => post<Telemetry>("/manual", { power_kw }),
  simFault: (active: boolean) => post<{ active: boolean }>("/sim/fault", { active }),
  fetchNow: () => post<{ ok: boolean; detail: string }>("/fetch"),
};
