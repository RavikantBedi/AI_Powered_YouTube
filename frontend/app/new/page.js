"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ErrorNote, Panel } from "@/components/ui";

const EXAMPLE =
  "Create a 12-minute Hindi documentary about the 1971 escape of three Indian " +
  "Air Force pilots from a Pakistani prison, and publish it to my channel.";

export default function NewVideo() {
  const router = useRouter();
  const [request, setRequest] = useState("");
  const [channel, setChannel] = useState("anidoc");
  const [channels, setChannels] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [topics, setTopics] = useState(null);

  useEffect(() => {
    api
      .channels()
      .then((d) => setChannels(d.channels))
      .catch((e) => setError(e.message));
  }, []);

  async function create(autoStart) {
    setBusy(true);
    setError("");
    try {
      const { project_id } = await api.createProject({
        request,
        channel,
        auto_start: autoStart,
      });
      router.push(`/projects/${project_id}`);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  async function suggest() {
    setBusy(true);
    setError("");
    try {
      const d = await api.discoverTopics({ channel, count: 6 });
      setTopics(d.topics);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-xl font-semibold text-slate-100">New video</h1>
      <ErrorNote error={error} />

      <Panel title="What video do you want?">
        <textarea
          className="input min-h-[120px] resize-y"
          placeholder={EXAMPLE}
          value={request}
          onChange={(e) => setRequest(e.target.value)}
        />
        <p className="mt-2 text-xs text-muted">
          Duration, language, style, and publishing intent are inferred from your request
          and the channel DNA. You will not be asked follow-up questions.
        </p>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label">Channel</label>
            <select
              className="input"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              {channels.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            className="btn-primary"
            disabled={busy || request.trim().length < 8}
            onClick={() => create(true)}
          >
            {busy ? "Working…" : "Create video"}
          </button>
          <button
            className="btn-ghost"
            disabled={busy || request.trim().length < 8}
            onClick={() => create(false)}
          >
            Save without starting
          </button>
          <button className="btn-ghost" disabled={busy} onClick={suggest}>
            Suggest topics
          </button>
        </div>
      </Panel>

      {topics ? (
        <Panel title="Researched topic ideas">
          <div className="space-y-2">
            {topics.map((t, i) => (
              <button
                key={i}
                onClick={() =>
                  setRequest(
                    `Create a 12-minute documentary about ${t.title}, and publish it to my channel.`
                  )
                }
                className="w-full rounded-md border border-edge px-3 py-2.5 text-left hover:bg-edge/40"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm text-slate-200">{t.title}</span>
                  <span className="shrink-0 font-mono text-xs text-amber">
                    {t.rank_score}
                  </span>
                </div>
                <div className="mt-1 text-xs text-muted">{t.hook}</div>
                <div className="mt-1 text-[11px] text-muted">
                  documentation {t.documentation} · interest {t.interest} · novelty{" "}
                  {t.novelty}
                </div>
              </button>
            ))}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
