"use client";

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
const KEY_STORAGE = "agency_api_key";

export function getApiKey() {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(KEY_STORAGE) || "";
}

export function setApiKey(key) {
  window.localStorage.setItem(KEY_STORAGE, key);
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": getApiKey(),
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

export const api = {
  dashboard: () => request("/api/dashboard"),
  capabilities: () => request("/api/capabilities"),
  listProjects: (limit = 50) => request(`/api/projects?limit=${limit}`),
  getProject: (id) => request(`/api/projects/${id}`),
  projectStatus: (id) => request(`/api/projects/${id}/status`),
  projectAgents: (id) => request(`/api/projects/${id}/agents`),
  projectArtifacts: (id) => request(`/api/projects/${id}/artifacts`),
  createProject: (body) =>
    request("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  start: (id) => request(`/api/projects/${id}/start`, { method: "POST" }),
  pause: (id) => request(`/api/projects/${id}/pause`, { method: "POST" }),
  cancel: (id) => request(`/api/projects/${id}/cancel`, { method: "POST" }),
  retry: (id) => request(`/api/projects/${id}/retry`, { method: "POST" }),
  agents: () => request("/api/agents"),
  channels: () => request("/api/channels"),
  discoverTopics: (body) =>
    request("/api/topics/discover", { method: "POST", body: JSON.stringify(body) }),
  youtubeStatus: () => request("/api/youtube/status"),
  youtubeConnect: (redirect_uri) =>
    request("/api/youtube/connect", {
      method: "POST",
      body: JSON.stringify({ redirect_uri }),
    }),
  youtubeCallback: (code, redirect_uri) =>
    request("/api/youtube/callback", {
      method: "POST",
      body: JSON.stringify({ code, redirect_uri }),
    }),
  artifactUrl: (id, path) =>
    `${BASE}/api/projects/${id}/artifacts/download?path=${encodeURIComponent(path)}`,
};
