import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { setLocale } from "./format";

export type Lang = "en" | "de";

/** German strings keyed by the English source text. A missing key falls back to English. */
const DE: Record<string, string> = {
  // Shell
  COMMAND: "LEITSTAND", SCHEDULE: "FAHRPLAN", BACKTEST: "BACKTEST", SYSTEM: "SYSTEM",
  Screens: "Ansichten", Language: "Sprache",
  "API OFFLINE": "API OFFLINE", "CONTROLLER OFFLINE": "REGLER OFFLINE", FAULT: "STÖRUNG",
  ONLINE: "ONLINE",
  // COMMAND
  "State of charge": "Ladezustand", SOC: "SOC", Plan: "Plan", Window: "Fenster", Temp: "Temp",
  Power: "Leistung", Setpoint: "Sollwert", Bus: "Bus", Interval: "Intervall",
  Readouts: "Messwerte", "NO DAY": "KEIN TAG", Price: "Preis", "Revenue today": "Erlös heute",
  Charged: "Geladen", Discharged: "Entladen", "Re-plans": "Neuplanungen",
  "Off schedule": "Fahrplanabweichung", "Imbalance settlement": "Ausgleichsenergie",
  "price not published": "Preis nicht veröffentlicht",
  Control: "Steuerung", "SAFETY LAYER ACTIVE": "SICHERHEITSSCHICHT AKTIV", Mode: "Betriebsart",
  AUTO: "AUTO", MANUAL: "MANUELL", Stop: "Stopp", "Confirm stop": "Stopp bestätigen",
  "Resume auto": "Automatik fortsetzen", "Manual power": "Manuelle Leistung",
  DISCHARGE: "ENTLADEN", CHARGE: "LADEN", kilowatts: "Kilowatt",
  "Controller offline": "Regler offline", STATUS: "STATUS", REPLAY: "WIEDERGABE", LIVE: "LIVE",
  "Live telemetry": "Live-Telemetrie", SAMPLES: "MESSPUNKTE",
  "Live power and state of charge": "Leistung und Ladezustand live",
  Measured: "Gemessen", "WAITING FOR TELEMETRY…": "WARTE AUF TELEMETRIE…",
  IDLE: "LEERLAUF", CHARGING: "LÄDT", DISCHARGING: "ENTLÄDT", "NO DATA": "KEINE DATEN",
  "no data": "keine Daten",
  "Following schedule": "Folgt dem Fahrplan", "Manual setpoint": "Manueller Sollwert",
  "Clamped to power limit": "Auf Leistungsgrenze begrenzt",
  "Stopped by operator": "Vom Bediener gestoppt",
  "Device fault: output forced to 0 kW": "Gerätestörung: Leistung auf 0 kW gesetzt",
  "Link lost > 30 s: output forced to 0 kW": "Verbindung > 30 s verloren: Leistung auf 0 kW gesetzt",
  "Schedule stale: output forced to 0 kW": "Fahrplan veraltet: Leistung auf 0 kW gesetzt",
  "No schedule: output forced to 0 kW": "Kein Fahrplan: Leistung auf 0 kW gesetzt",
  "SoC at upper limit": "SOC an der oberen Grenze", "SoC at lower limit": "SOC an der unteren Grenze",
  Starting: "Startet",
  "OVER-TEMPERATURE": "ÜBERTEMPERATUR", "DEVICE FAULT": "GERÄTESTÖRUNG",
  "COMM WATCHDOG": "KOMMUNIKATIONS-WATCHDOG",
  // Charts
  "Hover for values · scroll or +/− to zoom · drag to pan":
    "Zeigen für Werte · Scrollen oder +/− zum Zoomen · Ziehen zum Verschieben",
  "Hover for values": "Zeigen für Werte", Zoom: "Zoom", Day: "Tag", window: "Fenster",
  "Hover a month for values": "Monat zeigen für Werte", Month: "Monat",
  "EUR / MONTH": "EUR / MONAT",
  optimizer: "Optimierer", "forecast model": "Prognosemodell", naive: "naiv",
  persistence: "Persistenz", "Monthly revenue": "Monatserlös",
  // SCHEDULE
  "Delivery day": "Liefertag", "Previous day": "Vorheriger Tag", "Next day": "Nächster Tag",
  "Plan summary": "Planübersicht", INTERVALS: "INTERVALLE", Optimizer: "Optimierer",
  Expected: "Erwartet", "Naive rule": "Naive Regel", Cycles: "Zyklen",
  "Price min": "Preis min", "Price max": "Preis max", "Solve time": "Rechenzeit",
  "Price and dispatch": "Preis und Einsatz", "LOADING…": "LÄDT…",
  "No complete day-ahead prices for this day.": "Keine vollständigen Day-Ahead-Preise für diesen Tag.",
  "{gaps} gap(s), {dups} duplicate(s). Prices are never interpolated.":
    "{gaps} Lücke(n), {dups} Duplikat(e). Preise werden nie interpoliert.",
  "Provisional plan on forecast prices · auction not cleared yet · model {model} · the controller only dispatches on published prices":
    "Vorläufiger Plan auf Prognosepreisen · Auktion noch nicht abgeschlossen · Modell {model} · der Regler fährt nur veröffentlichte Preise",
  "with load, wind and solar forecasts": "mit Last-, Wind- und Solarprognosen",
  "on past prices only": "nur aus Vergangenheitspreisen",
  "Price, dispatch and state of charge for {day}": "Preis, Einsatz und Ladezustand für {day}",
  "Forecast price": "Prognosepreis", Dispatch: "Einsatz", SoC: "SOC",
  Charge: "Laden", Discharge: "Entladen", Idle: "Leerlauf",
  "Interval table": "Intervalltabelle", ROWS: "ZEILEN", Start: "Beginn",
  "Price €/MWh": "Preis €/MWh", "Setpoint kW": "Sollwert kW", "SoC %": "SOC %",
  "Cash flow €": "Zahlungsfluss €", "Naive kW": "Naiv kW",
  // BACKTEST
  "Backtest {year}": "Backtest {year}", "Battery duration": "Speicherdauer",
  "NO RUN STORED FOR THIS SCENARIO.": "KEIN LAUF FÜR DIESES SZENARIO GESPEICHERT.",
  "Running full year…": "Ganzes Jahr läuft…", "Run backtest": "Backtest starten",
  "Optimizer revenue": "Erlös Optimierer", "€ {v} per MWh capacity": "€ {v} je MWh Kapazität",
  "Uplift vs naive rule": "Mehrerlös ggü. naiver Regel", "naive € {v}": "naiv € {v}",
  "Full cycles": "Vollzyklen", "{v} per day": "{v} pro Tag",
  "Forecast model keeps": "Prognosemodell hält", "persistence {v} %": "Persistenz {v} %",
  "Without foresight": "Ohne Voraussicht", "persistence € {v}": "Persistenz € {v}",
  "{days} DAYS · SOURCE {source} · {violations} CONSTRAINT VIOLATIONS · {skipped} DAYS SKIPPED FOR DATA GAPS":
    "{days} TAGE · QUELLE {source} · {violations} GRENZVERLETZUNGEN · {skipped} TAGE WEGEN DATENLÜCKEN AUSGELASSEN",
  Markets: "Märkte", "V2 · FCR + AFRR + DAY-AHEAD": "V2 · FCR + AFRR + DAY-AHEAD",
  "+{v} % vs day-ahead only": "+{v} % ggü. nur Day-Ahead",
  "Running multi-market backtest…": "Multi-Markt-Backtest läuft…",
  "Run multi-market backtest": "Multi-Markt-Backtest starten",
  "Sells FCR and aFRR capacity alongside day-ahead trading.":
    "Vermarktet FCR- und aFRR-Leistung zusätzlich zum Day-Ahead-Handel.",
  "Needs an ENTSO-E token: reserve prices are not published on SMARD.":
    "Benötigt ein ENTSO-E-Token: Regelleistungspreise werden auf SMARD nicht veröffentlicht.",
  "Running…": "Läuft…", "Re-run": "Neu berechnen",
  "Upper bound: the optimizer plans against prices that are already cleared. A real battery bids before the auction. Persistence plans on yesterday's prices and shows what survives without foresight.":
    "Obergrenze: Der Optimierer plant gegen bereits feststehende Preise. Ein realer Speicher bietet vor der Auktion. Persistenz plant mit den Preisen von gestern und zeigt, was ohne Voraussicht übrig bleibt.",
  "FORECAST MODEL · price error {mae} €/MWh · plans on forecast prices, paid at real prices. With inputs certain to be known before the auction it keeps {share} %.":
    "PROGNOSEMODELL · Preisfehler {mae} €/MWh · plant auf Prognosepreisen, abgerechnet zu realen Preisen. Mit Eingangsdaten, die sicher vor der Auktion vorliegen, hält es {share} %.",
  "Training forecast model…": "Prognosemodell wird trainiert…",
  "Run forecast model": "Prognosemodell starten",
  "Trains a price model and replays the year on it. Several minutes the first time.":
    "Trainiert ein Preismodell und spielt das Jahr damit durch. Beim ersten Mal mehrere Minuten.",
  "Special days": "Besondere Tage", "OPEN IN SCHEDULE": "IM FAHRPLAN ÖFFNEN",
  "MOST NEGATIVE PRICE": "NEGATIVSTER PREIS", "HIGHEST SPREAD": "GRÖSSTER SPREAD",
  "WORST DAY FOR NAIVE RULE": "SCHLECHTESTER TAG DER NAIVEN REGEL",
  "BEST DAY FOR OPTIMIZER": "BESTER TAG DES OPTIMIERERS",
  OPT: "OPT", NAIVE: "NAIV", PRICE: "PREIS",
  "MULTI-MARKET": "MULTI-MARKT", "DAY-AHEAD ONLY": "NUR DAY-AHEAD",
  "Day-ahead energy": "Day-Ahead-Energie", "aFRR up": "aFRR positiv", "aFRR down": "aFRR negativ",
  "Revenue by market: multi-market {a} euro, day-ahead only {b} euro":
    "Erlös nach Markt: Multi-Markt {a} Euro, nur Day-Ahead {b} Euro",
  "Per MW of power, per year": "Je MW Leistung und Jahr",
  "Average band sold": "Mittleres vermarktetes Band", up: "positiv", down: "negativ",
  "Energy held per kW sold": "Vorgehaltene Energie je kW",
  "Full cycles · violations": "Vollzyklen · Verletzungen",
  "Imbalance vs day-ahead price": "Ausgleichsenergie- ggü. Day-Ahead-Preis",
  "mean gap {a} €/MWh · 95 % below {b}": "mittlerer Abstand {a} €/MWh · 95 % unter {b}",
  "Capacity payments only, at cleared prices and with perfect foresight of three auctions: an upper bound. Activation energy is not modelled, and a 500 kW unit would need pooling to reach the 1 MW minimum bid.":
    "Nur Leistungspreise, zu Zuschlagspreisen und mit perfekter Voraussicht über drei Auktionen: eine Obergrenze. Abrufenergie ist nicht modelliert, und eine 500-kW-Anlage müsste gepoolt werden, um das Mindestgebot von 1 MW zu erreichen.",
  // SYSTEM
  "Modbus link": "Modbus-Verbindung", "LINK UP": "VERBUNDEN", "LINK DOWN": "GETRENNT",
  "Modbus link up, heartbeat {beat}": "Modbus-Verbindung steht, Heartbeat {beat}",
  "Modbus link down, heartbeat {beat}": "Modbus-Verbindung getrennt, Heartbeat {beat}",
  none: "keiner", Controller: "Regler", OFFLINE: "OFFLINE",
  "Last reply": "Letzte Antwort", "s ago": "s her", Decision: "Entscheidung", Faults: "Störungen",
  NONE: "KEINE", "Clear simulated fault": "Simulierte Störung löschen",
  "Inject simulated fault": "Störung simulieren",
  "Register readout": "Registerwerte", UNIT: "GERÄT", Name: "Name", Type: "Typ", Addr: "Adr",
  Raw: "Roh", Value: "Wert", Unit: "Einheit",
  "Data feed": "Datenquelle", Refresh: "Aktualisieren", Source: "Quelle",
  "ENTSO-E token": "ENTSO-E-Token", SET: "GESETZT", "NOT SET · USING SMARD": "NICHT GESETZT · SMARD",
  "Last fetch": "Letzter Abruf", "Daily job": "Tagesjob", "Job last run": "Job zuletzt gelaufen",
  NEVER: "NIE", "Last {n} days": "Letzte {n} Tage", Gaps: "Lücken", Duplicates: "Duplikate",
  GAP: "LÜCKE", "Fetch next day now": "Folgetag jetzt abrufen", "Fetch failed: {e}": "Abruf fehlgeschlagen: {e}",
  "Fault and dispatch log": "Störungs- und Einsatzprotokoll", "SAFETY EVENTS": "SICHERHEITSEREIGNISSE",
  "Time (sim)": "Zeit (Sim)", Kind: "Art", Reason: "Grund", "Measured kW": "Gemessen kW",
  "NO EVENTS": "KEINE EREIGNISSE", DISPATCH: "EINSATZ", SAFETY: "SICHERHEIT",
  SCHEDULE_REASON: "FAHRPLAN", STOP: "STOPP", "not run yet": "noch nicht gelaufen",
};

type Vars = Record<string, string | number>;
export type Translate = (text: string, vars?: Vars) => string;

const I18n = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: Translate }>({
  lang: "en", setLang: () => undefined, t: (text) => text,
});

function initial(): Lang {
  try {
    const saved = localStorage.getItem("bdo-lang");
    if (saved === "en" || saved === "de") return saved;
  } catch {
    /* storage unavailable: fall through to the default */
  }
  return "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initial);
  // Set during render so numbers formatted in this same pass already use the new locale.
  setLocale(lang === "de" ? "de-DE" : "en-US");
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem("bdo-lang", next);
    } catch {
      /* not persisted */
    }
  }, []);
  const value = useMemo(() => {
    const t: Translate = (text, vars) => {
      const base = lang === "de" ? DE[text] ?? text : text;
      return vars ? base.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? "")) : base;
    };
    return { lang, setLang, t };
  }, [lang, setLang]);
  return <I18n.Provider value={value}>{children}</I18n.Provider>;
}

export const useI18n = () => useContext(I18n);
export const useT = (): Translate => useContext(I18n).t;
