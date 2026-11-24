"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Empty, ErrorNote, Panel, Stat, StatusBadge } from "@/components/ui";

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .dashboard()
        .then((d) => alive && (setData(d), setError("")))
        .catch((e) => alive && setError(e.message));
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const totals = data?.totals;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Dashboard</h1>
        <Link href="/new" className="btn-primary">
          Create video
        </Link>
      </div>

      <ErrorNote error={error} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Active" value={totals?.active ?? "–"} />
        <Stat label="Completed" value={totals?.completed ?? "–"} />
        <Stat
          label="Success rate"
          value={totals ? `${totals.success_rate}%` : "–"}
          hint={totals ? `${totals.failed} failed or blocked` : ""}
        />
        <Stat
          label="Total spend"
          value={totals ? `$${totals.total_cost_usd}` : "–"}
          hint={totals?.average_quality ? `avg quality ${totals.average_quality}` : ""}
        />
      </div>

      <Panel title="Capabilities">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(data?.capabilities || {}).map(([name, info]) => (
            <div
              key={name}
              className="flex items-start justify-between rounded-md border border-edge px-3 py-2"
            >
              <div className="min-w-0">
                <div className="text-sm text-slate-200">{name}</div>
                <div className="truncate text-xs text-muted">
                  {info.ready
                    ? info.available.join(", ")
                    : `needs ${info.missing_credentials.join(", ") || "configuration"}`}
                </div>
              </div>
              <span
                className={`ml-2 mt-1 h-2 w-2 shrink-0 rounded-full ${
                  info.ready ? "bg-emerald-400" : "bg-red-400"
                }`}
              />
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Recent productions"
        action={
          <Link href="/projects" className="text-xs text-amber hover:underline">
            view all
          </Link>
        }
      >
        {!data?.recent?.length ? (
          <Empty>No productions yet.</Empty>
        ) : (
          <div className="divide-y divide-edge">
            {data.recent.map((p) => (
              <Link
                key={p.id}
                href={`/projects/${p.id}`}
                className="flex items-center gap-3 py-2.5 hover:bg-edge/30"
              >
                <StatusBadge status={p.status} />
                <span className="min-w-0 flex-1 truncate text-sm text-slate-300">
                  {p.requirement?.topic || p.request}
                </span>
                <span className="shrink-0 font-mono text-xs text-muted">
                  {p.current_stage || "—"}
                </span>
              </Link>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
