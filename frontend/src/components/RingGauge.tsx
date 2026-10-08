import { useTween } from "../hooks/useTween";
import { num } from "../format";

export const polar = (r: number, deg: number, cx = 100, cy = 100): [number, number] => {
  const a = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
};

export const arc = (r: number, from: number, to: number): string => {
  const [x0, y0] = polar(r, from);
  const [x1, y1] = polar(r, to);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${Math.abs(to - from) > 180 ? 1 : 0} ${
    to > from ? 1 : 0} ${x1.toFixed(2)} ${y1.toFixed(2)}`;
};

export function TickRing({ r = 90, count = 60 }: { r?: number; count?: number }) {
  return (
    <g>
      {Array.from({ length: count }, (_, i) => {
        const major = i % 5 === 0;
        const [x0, y0] = polar(r, (i * 360) / count);
        const [x1, y1] = polar(r - (major ? 6 : 3), (i * 360) / count);
        return (
          <line key={i} x1={x0} y1={y0} x2={x1} y2={y1}
            stroke={major ? "var(--line)" : "var(--line-dim)"} strokeWidth={1} />
        );
      })}
    </g>
  );
}

interface RingGaugeProps {
  label: string;
  value: number | null | undefined;
  min: number;
  max: number;
  unit: string;
  digits?: number;
  /** Optional markers on the arc, e.g. the allowed SoC window. */
  marks?: number[];
  fault?: boolean;
}

const START = -150;
const SWEEP = 300;
const SEGMENTS = 50;

/** Ring gauge: tick ring, segmented arc, centre readout; eases between values (SC-2). */
export function RingGauge({ label, value, min, max, unit, digits = 0, marks, fault }: RingGaugeProps) {
  const shown = useTween(value, 500, min);
  const frac = shown == null ? 0 : Math.min(1, Math.max(0, (shown - min) / (max - min)));
  const lit = Math.round(frac * SEGMENTS);
  const seg = SWEEP / SEGMENTS;
  const text = num(shown, digits);
  return (
    <svg className="gauge" viewBox="0 0 200 200" role="img"
      aria-label={`${label}: ${value == null ? "no data" : `${num(value, digits)} ${unit}`}`}>
      <circle className="spin" cx="100" cy="100" r="97" fill="none" stroke="var(--line-dim)"
        strokeDasharray="1 5" />
      <TickRing />
      <circle className="spin rev" cx="100" cy="100" r="80" fill="none" stroke="var(--grid)"
        strokeWidth="3" strokeDasharray="40 12 4 12" />
      {Array.from({ length: SEGMENTS }, (_, i) => (
        <path key={i} d={arc(70, START + i * seg + 0.8, START + (i + 1) * seg - 0.8)} fill="none"
          stroke={i < lit ? (fault ? "var(--alert)" : "var(--line)") : "var(--grid)"}
          strokeWidth={i < lit ? 9 : 7} />
      ))}
      {marks?.map((m) => {
        const deg = START + ((m - min) / (max - min)) * SWEEP;
        const [x0, y0] = polar(77, deg);
        const [x1, y1] = polar(60, deg);
        return <line key={m} x1={x0} y1={y0} x2={x1} y2={y1} stroke="var(--line)" strokeWidth={1} />;
      })}
      <circle cx="100" cy="100" r="54" fill="none" stroke="var(--line-dim)" />
      <text x="100" y="108" textAnchor="middle" fontSize="34" fontWeight="600" letterSpacing="1">
        {text}
      </text>
      <text className="cap" x="100" y="124" textAnchor="middle">{unit}</text>
      <text className="cap" x="100" y="78" textAnchor="middle">{label}</text>
      <text className="cap" x={polar(70, START - 12)[0]} y="171" textAnchor="middle">{min}</text>
      <text className="cap" x={polar(70, START + SWEEP + 12)[0]} y="171" textAnchor="middle">{max}</text>
    </svg>
  );
}
