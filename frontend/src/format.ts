const BERLIN = "Europe/Berlin";

export const num = (v: number | null | undefined, digits = 0): string =>
  v == null || Number.isNaN(v)
    ? "---"
    : v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const signed = (v: number | null | undefined, digits = 0): string =>
  v == null || Number.isNaN(v) ? "---" : (v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), digits);

export const pad = (v: number | null | undefined, width: number): string =>
  v == null || Number.isNaN(v) ? "-".repeat(width) : String(Math.round(v)).padStart(width, "0");

const timeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: BERLIN, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const hmFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: BERLIN, hour: "2-digit", minute: "2-digit", hour12: false,
});
const dateFmt = new Intl.DateTimeFormat("en-CA", { timeZone: BERLIN });

export const berlinTime = (iso: string | number | Date): string => timeFmt.format(new Date(iso));
export const berlinHm = (iso: string | number | Date): string => hmFmt.format(new Date(iso));
/** YYYY-MM-DD of an instant in Berlin time. */
export const berlinDate = (iso: string | number | Date): string => dateFmt.format(new Date(iso));

export const shiftDay = (day: string, by: number): string => {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + by);
  return d.toISOString().slice(0, 10);
};

export const dayParts = (day: string) => {
  const d = new Date(`${day}T12:00:00Z`);
  return {
    month: d.toLocaleString("en-US", { month: "long", timeZone: "UTC" }),
    weekday: d.toLocaleString("en-US", { weekday: "long", timeZone: "UTC" }),
    day: d.getUTCDate(),
    year: d.getUTCFullYear(),
  };
};

export const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
