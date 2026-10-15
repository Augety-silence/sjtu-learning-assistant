import type { ReactNode } from "react";
import type { WidgetMotionMode } from "@/components/experiments/widgetMotion";

const MODES: { mode: WidgetMotionMode; label: string }[] = [
  { mode: "static", label: "静态" },
  { mode: "auto", label: "轻动效" },
];

/**
 * Renders the same case twice (static / light motion) so variants can be
 * compared side by side. Each column mounts its own component instance.
 */
export function DemoCase({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: (mode: WidgetMotionMode) => ReactNode;
}) {
  return (
    <section className="widget-lab-case" aria-label={title}>
      <header className="widget-lab-case-head">
        <h3>{title}</h3>
        {note && <p>{note}</p>}
      </header>
      <div className="widget-lab-case-grid">
        {MODES.map(({ mode, label }) => (
          <div key={mode} className="widget-lab-case-cell" data-mode={mode}>
            <span className="widget-lab-mode-label">{label}</span>
            {children(mode)}
          </div>
        ))}
      </div>
    </section>
  );
}

/** Small event log so demos can show exactly when callbacks fire. */
export function CallbackLog({ entries }: { entries: string[] }) {
  return (
    <ol className="widget-lab-log" aria-label="回调记录">
      {entries.length === 0 ? (
        <li className="widget-lab-log-empty">尚未触发回调</li>
      ) : (
        entries
          .slice(-4)
          .map((entry, index) => <li key={`${index}-${entry}`}>{entry}</li>)
      )}
    </ol>
  );
}
