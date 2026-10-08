import { useT } from "../i18n";

export const TABS = ["COMMAND", "SCHEDULE", "BACKTEST", "SYSTEM"] as const;
export type Tab = (typeof TABS)[number];

/** Segmented top tab bar with roving focus and arrow-key navigation. */
export function TabBar({ active, onChange }: { active: Tab; onChange: (tab: Tab) => void }) {
  const t = useT();
  return (
    <nav className="tabs" role="tablist" aria-label={t("Screens")}
      onKeyDown={(e) => {
        const i = TABS.indexOf(active);
        const next = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length]
          : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : null;
        if (next) {
          onChange(next);
          document.getElementById(`tab-${next}`)?.focus();
        }
      }}>
      {TABS.map((tab) => (
        <button key={tab} id={`tab-${tab}`} type="button" role="tab" className="tab"
          aria-selected={tab === active} aria-controls="screen" tabIndex={tab === active ? 0 : -1}
          onClick={() => onChange(tab)}>
          {t(tab)}
        </button>
      ))}
    </nav>
  );
}
