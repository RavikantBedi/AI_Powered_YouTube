"use client";

// The live production graph. Stages are grouped into the layers the pipeline
// actually runs in, so the shape on screen matches the shape of the system.
const LAYERS = [
  { name: "Intake", stages: ["REQUIREMENTS", "PLANNING"] },
  { name: "Research", stages: ["RESEARCH", "FACT_CHECK"] },
  { name: "Story", stages: ["STORY", "SCRIPT"] },
  { name: "Design", stages: ["STORYBOARD", "CHARACTERS", "VISUAL_DIRECTION"] },
  { name: "Production", stages: ["PRODUCTION_FANOUT", "IMAGE_QA", "MOTION", "VIDEO_QA"] },
  { name: "Assembly", stages: ["MUSIC", "SUBTITLES", "EDIT", "AUDIO_QA"] },
  { name: "Packaging", stages: ["PACKAGING", "COPYRIGHT", "POLICY"] },
  { name: "Release", stages: ["FINAL_QA", "PUBLISH", "NOTIFY"] },
];

const LABELS = {
  PRODUCTION_FANOUT: "IMAGES + VOICE",
  PACKAGING: "THUMBNAIL + SEO",
};

function StageCell({ stage }) {
  const done = stage.state === "done";
  const active = stage.state === "active";
  return (
    <div
      className={`relative overflow-hidden rounded border px-2.5 py-2 ${
        done
          ? "border-emerald-500/30 bg-emerald-500/10"
          : active
            ? "border-amber/50 bg-amber/10"
            : "border-edge bg-ink"
      }`}
    >
      {active && stage.pct > 0 ? (
        <div
          className="absolute inset-y-0 left-0 bg-amber/15"
          style={{ width: `${Math.min(stage.pct, 100)}%` }}
        />
      ) : null}
      <div className="relative flex items-center justify-between gap-2">
        <span
          className={`truncate text-[11px] ${
            done ? "text-emerald-300" : active ? "text-amber" : "text-muted"
          }`}
        >
          {LABELS[stage.stage] || stage.stage.replace(/_/g, " ")}
        </span>
        <span className="shrink-0 text-[10px] text-muted">
          {done ? "✓" : active ? `${Math.round(stage.pct)}%` : ""}
        </span>
      </div>
      {active && stage.detail ? (
        <div className="relative mt-0.5 truncate text-[10px] text-muted">{stage.detail}</div>
      ) : null}
    </div>
  );
}

export default function AgentGraph({ stages = [] }) {
  const byName = Object.fromEntries(stages.map((s) => [s.stage, s]));

  return (
    <div className="space-y-2">
      {LAYERS.map((layer, i) => (
        <div key={layer.name}>
          <div className="mb-1 flex items-center gap-2">
            <span className="w-20 shrink-0 text-[10px] uppercase tracking-wide text-muted">
              {layer.name}
            </span>
            <div className="grid flex-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
              {layer.stages.map((name) => (
                <StageCell
                  key={name}
                  stage={byName[name] || { stage: name, state: "waiting", pct: 0 }}
                />
              ))}
            </div>
          </div>
          {i < LAYERS.length - 1 ? (
            <div className="ml-20 h-2 border-l border-edge pl-2" />
          ) : null}
        </div>
      ))}
    </div>
  );
}
