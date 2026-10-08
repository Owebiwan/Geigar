import { useEffect, useRef, useState } from "react";
import type { Telemetry } from "./client";

export interface Sample {
  t: number;
  power_kw: number;
  setpoint_kw: number;
  soc: number;
}

const HISTORY = 240;

/** Live telemetry over WebSocket (SC-5) with a rolling history for the waveform. */
export function useTelemetry(): { live: Telemetry | null; history: Sample[]; connected: boolean } {
  const [live, setLive] = useState<Telemetry | null>(null);
  const [history, setHistory] = useState<Sample[]>([]);
  const [connected, setConnected] = useState(false);
  const lastTs = useRef(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const open = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/api/ws/telemetry`);
      socket.onopen = () => setConnected(true);
      socket.onmessage = (event) => {
        const msg = JSON.parse(event.data as string) as Telemetry;
        setLive(msg);
        if (msg.ts && msg.power_kw != null && msg.soc != null) {
          const t = Date.parse(msg.ts);
          // A replay that loops back to midnight starts a fresh trace.
          const restarted = t < lastTs.current;
          lastTs.current = t;
          const sample = {
            t, power_kw: msg.power_kw, setpoint_kw: msg.setpoint_kw ?? 0, soc: msg.soc,
          };
          setHistory((h) => (restarted ? [sample] : [...h.slice(-(HISTORY - 1)), sample]));
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(open, 2000);
      };
      socket.onerror = () => socket?.close();
    };
    open();
    return () => {
      closed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return { live, history, connected };
}
