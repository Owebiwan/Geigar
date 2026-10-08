import { useEffect, useRef, useState } from "react";
import { num, signed } from "../format";

interface SegmentedProps<T extends string | number> {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
}

/** Segmented selector; arrow keys move between options like a radio group. */
export function Segmented<T extends string | number>(p: SegmentedProps<T>) {
  const move = (dir: number) => {
    const i = p.options.findIndex((o) => o.value === p.value);
    p.onChange(p.options[(i + dir + p.options.length) % p.options.length].value);
  };
  return (
    <div className="seg" role="radiogroup" aria-label={p.label}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") move(1);
        if (e.key === "ArrowLeft") move(-1);
      }}>
      {p.options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" disabled={p.disabled}
          aria-checked={o.value === p.value} tabIndex={o.value === p.value ? 0 : -1}
          onClick={() => p.onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Two-step STOP: the first press arms, the second within 4 s confirms (SC-7). */
export function StopButton({ stopped, onStop, onResume, disabled }: {
  stopped: boolean; onStop: () => void; onResume: () => void; disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  if (stopped) {
    return (
      <button type="button" className="btn" onClick={onResume} disabled={disabled}>
        Resume auto
      </button>
    );
  }
  return (
    <button type="button" className={`btn stop${armed ? " armed" : ""}`} disabled={disabled}
      aria-live="polite"
      onClick={() => {
        clearTimeout(timer.current);
        if (armed) {
          setArmed(false);
          onStop();
        } else {
          setArmed(true);
          timer.current = setTimeout(() => setArmed(false), 4000);
        }
      }}>
      {armed ? "Confirm stop" : "Stop"}
    </button>
  );
}

/** Manual power slider. The request is clamped again by the controller's safety layer. */
export function PowerSlider({ max, min, value, disabled, onCommit }: {
  max: number; min: number; value: number; disabled?: boolean; onCommit: (kw: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div className="slider">
      <label className="label" htmlFor="manual-power">
        Manual power <b style={{ color: "var(--text)" }}>{signed(draft)} kW</b>
      </label>
      <input id="manual-power" type="range" min={min} max={max} step={10} value={draft}
        disabled={disabled} aria-valuetext={`${signed(draft)} kilowatts`}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={() => onCommit(draft)} onKeyUp={() => onCommit(draft)} />
      <div className="ends"><span>−{num(-min)} DISCHARGE</span><span>0</span>
        <span>CHARGE +{num(max)}</span></div>
    </div>
  );
}
