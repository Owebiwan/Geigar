import { useTween } from "../hooks/useTween";
import { num, signed } from "../format";
import { useT } from "../i18n";
import { arc, polar, TickRing } from "./RingGauge";

interface PowerDialProps {
  value: number | null | undefined;
  setpoint: number | null | undefined;
  max: number;
  fault?: boolean;
}

const HALF = 135; // degrees either side of top-centre zero
const SEGMENTS = 22;

/** Bipolar dial: charge fills solid to the right, discharge is outlined to the left. */
export function PowerDial({ value, setpoint, max, fault }: PowerDialProps) {
  const t = useT();
  const shown = useTween(value, 400);
  const frac = shown == null ? 0 : Math.max(-1, Math.min(1, shown / max));
  const lit = Math.round(Math.abs(frac) * SEGMENTS);
  const seg = HALF / SEGMENTS;
  const state = t(shown == null ? "NO DATA" : Math.abs(shown) < 0.5 ? "IDLE" : shown > 0
    ? "CHARGING" : "DISCHARGING");
  const [nx, ny] = polar(78, frac * HALF);
  const [bx, by] = polar(58, frac * HALF);
  const sp = setpoint == null ? null : Math.max(-1, Math.min(1, setpoint / max)) * HALF;
  return (
    <svg className="gauge" viewBox="0 0 200 200" role="img"
      aria-label={`${t("Power")}: ${value == null ? t("no data") : `${signed(value, 0)} kW, ${state}`}`}>
      <circle className="spin rev" cx="100" cy="100" r="97" fill="none" stroke="var(--line-dim)"
        strokeDasharray="12 4 1 4" />
      <TickRing />
      {Array.from({ length: SEGMENTS }, (_, i) => {
        const on = frac > 0 && i < lit;
        return (
          <path key={`c${i}`} d={arc(70, i * seg + 0.8, (i + 1) * seg - 0.8)} fill="none"
            stroke={on ? "var(--line)" : "var(--grid)"} strokeWidth={on ? 9 : 7} />
        );
      })}
      {Array.from({ length: SEGMENTS }, (_, i) => {
        const on = frac < 0 && i < lit;
        const a0 = -(i + 1) * seg + 0.8;
        const a1 = -i * seg - 0.8;
        // Discharge is drawn as two thin rails, the outline counterpart of the solid charge arc.
        return on ? (
          <g key={`d${i}`}>
            <path d={arc(74, a0, a1)} fill="none" stroke="var(--line)" strokeWidth={1.2} />
            <path d={arc(66, a0, a1)} fill="none" stroke="var(--line)" strokeWidth={1.2} />
          </g>
        ) : (
          <path key={`d${i}`} d={arc(70, a0, a1)} fill="none" stroke="var(--grid)" strokeWidth={7} />
        );
      })}
      <line x1={polar(62, 0)[0]} y1={polar(62, 0)[1]} x2={polar(80, 0)[0]} y2={polar(80, 0)[1]}
        stroke="var(--line)" />
      {sp != null && (
        <circle cx={polar(84, sp)[0]} cy={polar(84, sp)[1]} r="2.5" fill="none"
          stroke="var(--line)" />
      )}
      <line x1={bx} y1={by} x2={nx} y2={ny} stroke={fault ? "var(--alert)" : "var(--line)"}
        strokeWidth={2} />
      <circle cx="100" cy="100" r="54" fill="none" stroke="var(--line-dim)" />
      <text x="100" y="108" textAnchor="middle" fontSize="30" fontWeight="600" letterSpacing="1">
        {signed(shown, 0)}
      </text>
      <text className="cap" x="100" y="124" textAnchor="middle">KW</text>
      <text className="cap" x="100" y="78" textAnchor="middle">{state}</text>
      <text className="cap" x="72" y="166" textAnchor="middle">−{num(max)}</text>
      <text className="cap" x="128" y="166" textAnchor="middle">+{num(max)}</text>
    </svg>
  );
}
