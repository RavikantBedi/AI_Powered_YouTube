"use client";

import { useEffect, useState } from "react";
import { Empty, ErrorNote, Panel } from "@/components/ui";
import { api } from "@/lib/api";

const STATUS_COLOR = {
  production: "text-emerald-300",
  staging: "text-sky-300",
  test: "text-amber",
  sandbox: "text-muted",
  disabled: "text-red-300",
};

export default function Agents() {
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .agents()
      .then((d) => setAgents(d.agents))
      .catch((e) => setError(e.message));
  }, []);

  const builtin = agents.filter((a) => a.origin === "builtin");
  const factory = agents.filter((a) => a.origin === "factory");

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-slate-100">Agent registry</h1>
      <ErrorNote error={error} />

      <Panel title={`Built-in agents (${builtin.length})`}>
        <div className="divide-y divide-edge">
          {builtin.map((a) => (
            <div key={a.agent_id} className="flex flex-wrap items-center gap-3 py-2.5">
              <span className="font-mono text-sm text-slate-200">{a.agent_id}</span>
              <span className="text-xs text-muted">v{a.version}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted">
                {a.capabilities.join(", ")}
              </span>
              <span className={`text-xs ${STATUS_COLOR[a.status] || "text-muted"}`}>
                {a.status}
              </span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title={`Factory-created agents (${factory.length})`}>
        {!factory.length ? (
          <Empty>
            The Agent Factory has not needed to build anything yet. It runs automatically
            when a production requires a capability no registered agent provides.
          </Empty>
        ) : (
          <div className="divide-y divide-edge">
            {factory.map((a) => (
              <div key={a.agent_id} className="py-2.5">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-mono text-sm text-slate-200">{a.agent_id}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-muted">
                    {a.capabilities.join(", ")}
                  </span>
                  <span className={`text-xs ${STATUS_COLOR[a.status] || "text-muted"}`}>
                    {a.status}
                  </span>
                </div>
                {a.spec?.benchmark ? (
                  <div className="mt-1 text-[11px] text-muted">
                    tests {a.spec.benchmark.tests_passed}/{a.spec.benchmark.tests_total}
                    {a.spec.security_notes?.length
                      ? ` · security: ${a.spec.security_notes.join("; ")}`
                      : ""}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
        <p className="mt-4 text-xs text-muted">
          Generated agents are declarative specifications, not generated code. They are
          promoted sandbox → test → staging → production only after their generated test
          cases pass and a security review approves them.
        </p>
      </Panel>
    </div>
  );
}
