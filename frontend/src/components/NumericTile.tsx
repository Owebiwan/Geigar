import { useTween } from "../hooks/useTween";
import { num, signed } from "../format";

interface TileProps {
  label: string;
  value: number | null | undefined;
  unit?: string;
  digits?: number;
  /** Shown under the numeral, e.g. the change against a reference value. */
  delta?: string;
  showSign?: boolean;
  big?: boolean;
  solid?: boolean;
  /** Live values update too often to ease; they snap. */
  live?: boolean;
}

/** Boxed numeric readout with a count-up on load (SC-3). */
export function NumericTile(p: TileProps) {
  const shown = useTween(p.value, p.live ? 0 : 900);
  const text = p.showSign ? signed(shown, p.digits ?? 0) : num(shown, p.digits ?? 0);
  return (
    <div className={`tile${p.big ? " big" : ""}${p.solid ? " solid" : ""}`}>
      <div className="label">
        <span>{p.label}</span>
        {p.unit && <span className="unit">{p.unit}</span>}
      </div>
      <div className="num">{text}</div>
      {p.delta && <div className="delta">{p.delta}</div>}
    </div>
  );
}
