"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Empty, ErrorNote, Panel, StatusBadge } from "@/components/ui";

export default function Projects() {
  const [projects, setProjects] = useState([]);
  const [counts, setCounts] = useState({});
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .listProjects(100)
        .then((d) => alive && (setProjects(d.projects), setCounts(d.counts), setError("")))
        .catch((e) => alive && setError(e.message));
    load();
    const timer = setInterval(load, 6000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const shown = filter ? projects.filter((p) => p.status === filter) : projects;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-slate-100">Projects</h1>
      <ErrorNote error={error} />

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setFilter("")}
          className={filter === "" ? "btn-primary" : "btn-ghost"}
        >
          all ({projects.length})
        </button>
        {Object.entries(counts).map(([status, n]) => (
          <button
            key={status}
            onClick={() => setFilter(status)}
            className={filter === status ? "btn-primary" : "btn-ghost"}
          >
            {status} ({n})
          </button>
        ))}
      </div>

      <Panel>
        {!shown.length ? (
          <Empty>Nothing here.</Empty>
        ) : (
          <div className="divide-y divide-edge">
            {shown.map((p) => (
              <Link
                key={p.id}
                href={`/projects/${p.id}`}
                className="flex flex-wrap items-center gap-3 py-3 hover:bg-edge/30"
              >
                <StatusBadge status={p.status} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-slate-200">
                    {p.requirement?.topic || p.request}
                  </div>
                  <div className="mt-0.5 font-mono text-[11px] text-muted">
                    {p.id} · {p.channel_id} · ${p.total_cost_usd}
                    {p.blocked_on ? ` · blocked on ${p.blocked_on}` : ""}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-mono text-xs text-muted">{p.current_stage || "—"}</div>
                  {p.quality?.overall_score ? (
                    <div className="text-xs text-amber">{p.quality.overall_score}/100</div>
                  ) : null}
                </div>
              </Link>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
