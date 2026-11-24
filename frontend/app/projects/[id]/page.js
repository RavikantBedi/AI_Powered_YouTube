"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import AgentGraph from "@/components/AgentGraph";
import { Empty, ErrorNote, Panel, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";

export default function ProjectMonitor() {
  const { id } = useParams();
  const [status, setStatus] = useState(null);
  const [detail, setDetail] = useState(null);
  const [executions, setExecutions] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, d, a] = await Promise.all([
        api.projectStatus(id),
        api.getProject(id),
        api.projectAgents(id),
      ]);
      setStatus(s);
      setDetail(d);
      setExecutions(a.executions);
      setError("");
      if (["done", "blocked", "failed", "cancelled"].includes(s.status)) {
        api.projectArtifacts(id).then((r) => setArtifacts(r.artifacts)).catch(() => {});
      }
    } catch (e) {
      setError(e.message);
    }
  }, [id]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(fn) {
    setBusy(true);
    try {
      await fn(id);
      await load();
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  const publish = status?.publish_result || {};
  const dod = detail?.definition_of_done;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold text-slate-100">
            {detail?.requirement?.topic || detail?.request || id}
          </h1>
          <div className="mt-1 flex items-center gap-2 font-mono text-xs text-muted">
            <StatusBadge status={status?.status || "created"} />
            <span>{id}</span>
            <span>· ${status?.cost_usd ?? 0}</span>
          </div>
        </div>
        <div className="flex gap-2">
          {status?.status === "running" ? (
            <>
              <button className="btn-ghost" disabled={busy} onClick={() => act(api.pause)}>
                Pause
              </button>
              <button className="btn-ghost" disabled={busy} onClick={() => act(api.cancel)}>
                Cancel
              </button>
            </>
          ) : (
            <button
              className="btn-primary"
              disabled={busy || status?.status === "done"}
              onClick={() => act(status?.status === "created" ? api.start : api.retry)}
            >
              {status?.status === "created" ? "Start" : "Resume"}
            </button>
          )}
        </div>
      </div>

      <ErrorNote error={error} />

      {status?.blocked_on ? (
        <div className="rounded-md border border-orange-500/30 bg-orange-500/10 px-4 py-3">
          <div className="text-sm font-medium text-orange-300">
            Blocked on: {status.blocked_on}
          </div>
          <div className="mt-1 text-xs text-orange-200/80">{status.error}</div>
          <div className="mt-2 text-xs text-muted">
            Resolve the dependency, then press Resume — the run continues from its last
            completed stage.
          </div>
        </div>
      ) : null}

      {publish.video_url ? (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
          <div className="text-sm font-medium text-emerald-300">Published</div>
          <a
            href={publish.video_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 block text-sm text-emerald-200 underline"
          >
            {publish.video_url}
          </a>
          <div className="mt-1 text-xs text-emerald-200/70">
            {publish.privacy_status} · thumbnail {publish.thumbnail_status} ·{" "}
            {publish.caption_status}
          </div>
        </div>
      ) : null}

      <Panel title="Production graph">
        <AgentGraph stages={status?.stages || []} />
      </Panel>

      {status?.quality?.overall_score ? (
        <Panel title="Quality report">
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
            {["overall_score", "content", "visual", "audio", "editing", "seo"].map((k) => (
              <div key={k} className="rounded-md border border-edge px-3 py-2 text-center">
                <div className="text-lg font-semibold text-slate-100">
                  {status.quality[k]}
                </div>
                <div className="text-[10px] uppercase tracking-wide text-muted">
                  {k.replace("_score", "")}
                </div>
              </div>
            ))}
          </div>
          {status.quality.issues?.length ? (
            <ul className="mt-4 space-y-1">
              {status.quality.issues.map((issue, i) => (
                <li key={i} className="text-xs text-muted">
                  <span className="text-slate-300">[{issue.severity}]</span> {issue.area}:{" "}
                  {issue.description}
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}

      {dod ? (
        <Panel title="Definition of done">
          <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(dod.checks).map(([check, ok]) => (
              <div key={check} className="flex items-center gap-2 text-xs">
                <span className={ok ? "text-emerald-400" : "text-muted"}>
                  {ok ? "✓" : "○"}
                </span>
                <span className={ok ? "text-slate-300" : "text-muted"}>
                  {check.replace(/_/g, " ")}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Agent executions">
          {!executions.length ? (
            <Empty>No agents have run yet.</Empty>
          ) : (
            <div className="max-h-96 space-y-1 overflow-y-auto">
              {executions
                .slice()
                .reverse()
                .map((e, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span
                      className={
                        e.status === "completed"
                          ? "text-emerald-400"
                          : e.status === "failed"
                            ? "text-red-400"
                            : "text-amber"
                      }
                    >
                      ●
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-slate-300">
                      {e.agent_id}
                      {e.attempt > 1 ? ` (try ${e.attempt})` : ""}
                    </span>
                    <span className="shrink-0 text-muted">
                      {e.duration_seconds ? `${e.duration_seconds}s` : ""}
                    </span>
                    <span className="w-14 shrink-0 text-right text-muted">
                      {e.cost_usd ? `$${e.cost_usd}` : ""}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </Panel>

        <Panel title="Event log">
          <div className="max-h-96 space-y-1 overflow-y-auto font-mono text-[11px]">
            {(status?.events || []).map((e, i) => (
              <div key={i} className="flex gap-2">
                <span className="shrink-0 text-amber">{e.type}</span>
                <span className="min-w-0 truncate text-muted">
                  {JSON.stringify(e.payload)}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {artifacts.length ? (
        <Panel title={`Artifacts (${artifacts.length})`}>
          <div className="max-h-72 space-y-0.5 overflow-y-auto">
            {artifacts.map((a) => (
              <a
                key={a.path}
                href={api.artifactUrl(id, a.path)}
                className="flex items-center justify-between rounded px-2 py-1 font-mono text-[11px] text-slate-400 hover:bg-edge/40 hover:text-slate-200"
              >
                <span className="truncate">{a.path}</span>
                <span className="shrink-0 text-muted">
                  {(a.bytes / 1024).toFixed(0)} KB
                </span>
              </a>
            ))}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
