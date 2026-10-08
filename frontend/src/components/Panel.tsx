import type { CSSProperties, ReactNode } from "react";

interface PanelProps {
  title: string;
  code?: string;
  status?: "on" | "off" | "fault";
  right?: ReactNode;
  grid?: boolean;
  area?: string;
  children: ReactNode;
}

/** Outlined panel with bracketed corners, header label, ID code and status dot (SC-1). */
export function Panel({ title, code, status, right, grid, area, children }: PanelProps) {
  const style: CSSProperties | undefined = area ? { gridArea: area } : undefined;
  const cls = ["panel", grid && "gridded", status === "fault" && "fault"].filter(Boolean).join(" ");
  return (
    <section className={cls} style={style} aria-label={title}>
      <span className="corner tl" /><span className="corner tr" />
      <span className="corner bl" /><span className="corner br" />
      <header className="panel-head">
        <h2>
          {status && (
            <span className={`dot ${status === "on" ? "on" : status === "fault" ? "fault" : ""}`}
              aria-hidden="true" />
          )}
          {title}
        </h2>
        {code && <span className="id">{code}</span>}
        {right && <span className="right">{right}</span>}
      </header>
      {children}
    </section>
  );
}
