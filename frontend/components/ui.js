"use client";

export function Stat({ label, value, hint }) {
  return (
    <div className="panel p-4">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-100">{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

const STATUS_STYLES = {
  done: "bg-emerald-500/15 text-emerald-300",
  running: "bg-amber/15 text-amber",
  blocked: "bg-orange-500/15 text-orange-300",
  failed: "bg-red-500/15 text-red-300",
  cancelled: "bg-slate-500/15 text-slate-400",
  paused: "bg-sky-500/15 text-sky-300",
  created: "bg-slate-500/15 text-slate-400",
};

export function StatusBadge({ status }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        STATUS_STYLES[status] || STATUS_STYLES.created
      }`}
    >
      {status}
    </span>
  );
}

export function Panel({ title, action, children, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between border-b border-edge px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return (
    <div className="mb-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      {String(error)}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="py-8 text-center text-sm text-muted">{children}</div>;
}
