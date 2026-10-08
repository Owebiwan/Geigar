import { scaleLinear } from "d3";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { num } from "../format";
import { useT } from "../i18n";

export interface Pane {
  id: string;
  label: string;
  unit: string;
  height: number;
  /** step = value held over the interval; line = point samples; bars = signed dispatch bars. */
  kind: "step" | "line" | "bars";
  values: number[];
  /** Optional reference series drawn dim and dashed. */
  compare?: { label: string; values: number[] };
  domain?: [number, number];
  digits?: number;
  scale?: number;
}

interface ChartProps {
  /** Interval start times in ms. */
  x: number[];
  /** Interval length in ms; 0 for point samples. */
  step: number;
  panes: Pane[];
  formatX: (ms: number) => string;
  zoomable?: boolean;
  scan?: boolean;
  onHover?: (index: number | null) => void;
  label: string;
}

const M = { left: 52, right: 12, top: 6, bottom: 20, gap: 14 };
const MIN_15 = 900_000;
const HOUR = 3_600_000;

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const obs = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);
  return [ref, width];
}

/** Multi-pane time-series chart with grid, crosshair readout and zoom (SC-4).
 *  Each pane has its own y axis; panes share the time axis. */
export function TimeSeriesChart(p: ChartProps) {
  const { x, step, panes } = p;
  const t = useT();
  const [ref, width] = useWidth();
  const clip = useId();
  const full: [number, number] = useMemo(
    () => (x.length ? [x[0], x[x.length - 1] + step] : [0, 1]), [x, step]);
  const [view, setView] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const drag = useRef<{ px: number; view: [number, number] } | null>(null);

  // A new day or series resets the zoom.
  useEffect(() => setView(null), [full[0], p.zoomable]); // eslint-disable-line react-hooks/exhaustive-deps

  const domain = p.zoomable && view ? view : full;
  const xs = scaleLinear().domain(domain).range([M.left, width - M.right]);
  const span = domain[1] - domain[0];
  const minSpan = Math.max(step || MIN_15, MIN_15) * 4; // closest zoom: four 15-minute intervals

  const clampView = (a: number, b: number): [number, number] => {
    let s = Math.min(Math.max(b - a, minSpan), full[1] - full[0]);
    if (!Number.isFinite(s)) s = full[1] - full[0];
    const start = Math.min(Math.max(a, full[0]), full[1] - s);
    return [start, start + s];
  };
  const zoomTo = (hours: number | null) => {
    if (hours == null) return setView(null);
    const centre = hover != null ? x[hover] : (domain[0] + domain[1]) / 2;
    setView(clampView(centre - (hours * HOUR) / 2, centre + (hours * HOUR) / 2));
  };
  const setHovered = (i: number | null) => {
    setHover(i);
    p.onHover?.(i);
  };
  const indexAt = (ms: number): number | null => {
    if (!x.length) return null;
    let lo = 0;
    let hi = x.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (x[mid] <= ms) lo = mid; else hi = mid - 1;
    }
    if (step === 0 && lo < x.length - 1 && ms - x[lo] > x[lo + 1] - ms) lo += 1;
    return lo;
  };

  const tops: number[] = [];
  let y = M.top;
  for (const pane of panes) {
    tops.push(y);
    y += pane.height + M.gap;
  }
  const height = y - M.gap + M.bottom;

  const tickStep = span > 12 * HOUR ? 3 * HOUR : span > 4 * HOUR ? HOUR : span > 90 * 60_000
    ? 30 * 60_000 : MIN_15;
  const xticks: number[] = [];
  for (let t = Math.ceil((domain[0] - full[0]) / tickStep) * tickStep + full[0]; t <= domain[1];
    t += tickStep) xticks.push(t);

  const visible = (i: number) => x[i] + (step || 0) >= domain[0] && x[i] <= domain[1];

  return (
    <div className="chart" ref={ref}>
      <div className="readout" aria-live="off">
        {hover != null && x[hover] != null ? (
          <>
            <span>T <b>{p.formatX(x[hover])}</b></span>
            {panes.map((pane) => (
              <span key={pane.id}>
                {pane.label} <b>{num(pane.values[hover] * (pane.scale ?? 1), pane.digits ?? 1)}</b>{" "}
                {pane.unit}
                {pane.compare && (
                  <> · {pane.compare.label}{" "}
                    <b>{num(pane.compare.values[hover] * (pane.scale ?? 1), pane.digits ?? 1)}</b></>
                )}
              </span>
            ))}
          </>
        ) : (
          <span>{t(p.zoomable ? "Hover for values · scroll or +/− to zoom · drag to pan"
            : "Hover for values")}</span>
        )}
      </div>
      <svg height={height} role="img" aria-label={p.label} tabIndex={0}
        onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = e.clientX - box.left;
          if (drag.current && p.zoomable) {
            const dx = ((drag.current.px - px) / (width - M.left - M.right)) * span;
            setView(clampView(drag.current.view[0] + dx, drag.current.view[1] + dx));
          }
          setHovered(px < M.left || px > width - M.right ? null : indexAt(xs.invert(px)));
        }}
        onPointerLeave={() => { drag.current = null; setHovered(null); }}
        onPointerDown={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          drag.current = { px: e.clientX - box.left, view: domain };
        }}
        onPointerUp={() => { drag.current = null; }}
        onWheel={(e) => {
          if (!p.zoomable) return;
          const box = e.currentTarget.getBoundingClientRect();
          const at = xs.invert(e.clientX - box.left);
          const k = e.deltaY < 0 ? 0.75 : 1 / 0.75;
          setView(clampView(at - (at - domain[0]) * k, at + (domain[1] - at) * k));
        }}
        onKeyDown={(e) => {
          if (!x.length) return;
          const i = hover ?? indexAt((domain[0] + domain[1]) / 2) ?? 0;
          if (e.key === "ArrowRight") setHovered(Math.min(x.length - 1, i + 1));
          else if (e.key === "ArrowLeft") setHovered(Math.max(0, i - 1));
          else if (p.zoomable && (e.key === "+" || e.key === "=")) zoomTo(span / HOUR / 2);
          else if (p.zoomable && e.key === "-") zoomTo(Math.min((full[1] - full[0]) / HOUR,
            (span / HOUR) * 2));
          else if (e.key === "Escape") { setView(null); setHovered(null); }
          else return;
          e.preventDefault();
        }}>
        <defs>
          <clipPath id={clip}>
            <rect x={M.left} y={0} width={Math.max(0, width - M.left - M.right)} height={height} />
          </clipPath>
        </defs>
        {panes.map((pane, k) => {
          const top = tops[k];
          const sc = pane.scale ?? 1;
          const all = pane.compare ? [...pane.values, ...pane.compare.values] : pane.values;
          const lo = pane.domain?.[0] ?? Math.min(0, ...all.map((v) => v * sc));
          const hi = pane.domain?.[1] ?? Math.max(...all.map((v) => v * sc), lo + 1);
          const ys = scaleLinear().domain([lo, hi]).nice(4).range([top + pane.height, top]);
          const path = (values: number[]) => {
            let d = "";
            values.forEach((v, i) => {
              if (!visible(i)) return;
              const yy = ys(v * sc).toFixed(1);
              if (pane.kind === "line") d += `${d ? "L" : "M"}${xs(x[i]).toFixed(1)} ${yy}`;
              else d += `${d ? "L" : "M"}${xs(x[i]).toFixed(1)} ${yy}H${xs(x[i] + step).toFixed(1)}`;
            });
            return d;
          };
          return (
            <g key={pane.id}>
              {ys.ticks(4).map((t) => (
                <g key={t}>
                  <line className="gridline" x1={M.left} x2={width - M.right} y1={ys(t)} y2={ys(t)} />
                  <text className="tick" x={M.left - 6} y={ys(t) + 3} textAnchor="end">
                    {num(t, Math.abs(hi - lo) < 5 ? 1 : 0)}
                  </text>
                </g>
              ))}
              <text className="tick" x={M.left + 4} y={top + 10}>
                {pane.label} · {pane.unit}
              </text>
              <g clipPath={`url(#${clip})`}>
                {pane.kind === "bars" ? (
                  <>
                    {pane.values.map((v, i) => {
                      if (!visible(i)) return null;
                      const x0 = xs(x[i]);
                      const w = Math.max(1, xs(x[i] + step) - x0 - 1);
                      if (Math.abs(v) < 1e-6) {
                        return <line key={i} className="idle" x1={x0} x2={x0 + w} y1={ys(0)}
                          y2={ys(0)} strokeWidth={2} />;
                      }
                      // Charge: solid above the axis. Discharge: hatched below it.
                      return v > 0
                        ? <rect key={i} className="solid" x={x0} y={ys(v * sc)} width={w}
                            height={ys(0) - ys(v * sc)} />
                        : <rect key={i} className="hatched" x={x0} y={ys(0)} width={w}
                            height={ys(v * sc) - ys(0)} />;
                    })}
                    <line className="axis" x1={M.left} x2={width - M.right} y1={ys(0)} y2={ys(0)} />
                  </>
                ) : (
                  <>
                    {pane.compare && <path className="series dim" d={path(pane.compare.values)} />}
                    <path className="series" d={path(pane.values)} />
                    {lo < 0 && <line className="axis" x1={M.left} x2={width - M.right} y1={ys(0)}
                      y2={ys(0)} />}
                  </>
                )}
              </g>
            </g>
          );
        })}
        {xticks.map((t) => (
          <text key={t} className="tick" x={xs(t)} y={height - 5} textAnchor="middle">
            {p.formatX(t)}
          </text>
        ))}
        {p.scan && (
          <g clipPath={`url(#${clip})`}>
            <rect className="scan" x={M.left} y={M.top} width={(width - M.left - M.right) * 0.12}
              height={height - M.top - M.bottom}
              style={{ transformBox: "view-box", transformOrigin: "0 0" }} />
          </g>
        )}
        {hover != null && x[hover] != null && visible(hover) && (
          <line className="cross" x1={xs(x[hover] + step / 2)} x2={xs(x[hover] + step / 2)}
            y1={M.top} y2={height - M.bottom} />
        )}
      </svg>
      {p.zoomable && (
        <div className="zoom" role="group" aria-label={t("Zoom")}>
          <span className="label">{t("Zoom")}</span>
          {([["Day", null], ["6 h", 6], ["2 h", 2], ["1 h", 1]] as const).map(([name, hours]) => (
            <button key={name} type="button" className="btn" onClick={() => zoomTo(hours)}
              aria-pressed={hours == null ? view == null : view != null
                && Math.abs(span - hours * HOUR) < 1000}>
              {name === "Day" ? t("Day") : name}
            </button>
          ))}
          <span className="label">{t("window")} {num(span / HOUR, 2)} h</span>
        </div>
      )}
    </div>
  );
}
