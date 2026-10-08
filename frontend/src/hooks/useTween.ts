import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "../format";

/** Eases a displayed number towards `target`; used for count-up and gauge motion (UI-6). */
export function useTween(target: number | null | undefined, ms = 600, from = 0): number | null {
  const [value, setValue] = useState<number | null>(target == null ? null : from);
  const current = useRef<number>(from);

  useEffect(() => {
    if (target == null || Number.isNaN(target)) {
      setValue(null);
      return;
    }
    if (prefersReducedMotion() || ms <= 0) {
      current.current = target;
      setValue(target);
      return;
    }
    const start = current.current;
    const t0 = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const k = Math.min(1, (now - t0) / ms);
      const eased = 1 - Math.pow(1 - k, 3);
      current.current = start + (target - start) * eased;
      setValue(current.current);
      if (k < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, ms]);

  return value;
}
