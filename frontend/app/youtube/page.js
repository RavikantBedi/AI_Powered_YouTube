"use client";

import { useEffect, useState } from "react";
import { ErrorNote, Panel } from "@/components/ui";
import { api } from "@/lib/api";

const OOB = "urn:ietf:wg:oauth:2.0:oob";

export default function YouTube() {
  const [status, setStatus] = useState(null);
  const [authUrl, setAuthUrl] = useState("");
  const [code, setCode] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    api
      .youtubeStatus()
      .then(setStatus)
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      const d = await api.youtubeConnect(OOB);
      setAuthUrl(d.authorization_url);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  async function submitCode() {
    setBusy(true);
    setError("");
    try {
      setResult(await api.youtubeCallback(code, OOB));
      setAuthUrl("");
      setCode("");
      await load();
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-xl font-semibold text-slate-100">YouTube connection</h1>
      <ErrorNote error={error} />

      <Panel title="Status">
        <div className="space-y-1 text-sm">
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                status?.connected ? "bg-emerald-400" : "bg-red-400"
              }`}
            />
            <span className="text-slate-300">
              {status?.connected ? "Account connected" : "No account connected"}
            </span>
          </div>
          <div className="text-xs text-muted">
            OAuth client secret:{" "}
            {status?.client_secret_present ? "found" : "missing — see docs/DEPLOYMENT.md"}
          </div>
        </div>
      </Panel>

      <Panel title="Connect an account">
        <ol className="space-y-4 text-sm text-slate-300">
          <li>
            <div className="mb-2">
              1. Place your Google Cloud OAuth client secret JSON at the path in{" "}
              <code className="font-mono text-xs text-amber">YOUTUBE_CLIENT_SECRET_FILE</code>.
            </div>
          </li>
          <li>
            <div className="mb-2">2. Start the authorization flow.</div>
            <button className="btn-primary" disabled={busy} onClick={connect}>
              Get authorization URL
            </button>
            {authUrl ? (
              <a
                href={authUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-2 block break-all rounded-md border border-edge bg-ink p-2 font-mono text-[11px] text-amber underline"
              >
                {authUrl}
              </a>
            ) : null}
          </li>
          <li>
            <div className="mb-2">3. Grant access, then paste the code Google shows you.</div>
            <div className="flex gap-2">
              <input
                className="input font-mono text-xs"
                value={code}
                placeholder="4/0A…"
                onChange={(e) => setCode(e.target.value)}
              />
              <button
                className="btn-primary shrink-0"
                disabled={busy || !code}
                onClick={submitCode}
              >
                Connect
              </button>
            </div>
          </li>
        </ol>

        {result ? (
          <div className="mt-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            Connected to {result.channel_title || result.channel_id}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
