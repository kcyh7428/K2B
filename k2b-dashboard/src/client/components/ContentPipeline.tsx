import React from "react";
import { usePolling } from "../hooks/usePolling.js";

interface PipelineData {
  ideas: number;
  adopted: number;
  drafts: number;
  published: number;
}

interface Stage {
  label: string;
  count: number;
  colorClass: string;
}

export function ContentPipeline() {
  const { data, loading } = usePolling<PipelineData>(
    "/api/content-pipeline",
    30000,
  );

  const stages: Stage[] = data
    ? [
        { label: "ideas", count: data.ideas, colorClass: "text-blue" },
        { label: "adopted", count: data.adopted, colorClass: "text-amber" },
        { label: "drafts", count: data.drafts, colorClass: "text-secondary" },
        { label: "published", count: data.published, colorClass: "text-green" },
      ]
    : [];

  return (
    <div className="panel">
      <span className="panel-title">Content Pipeline</span>

      {loading && !data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Loading...
        </div>
      )}

      {data && (
        <div className="pipeline-flow">
          {stages.map((stage, i) => (
            <React.Fragment key={stage.label}>
              {i > 0 && (
                <span className="pipeline-arrow text-muted">&rsaquo;</span>
              )}
              <div className="pipeline-stage">
                <span className={`pipeline-count ${stage.colorClass}`}>
                  {stage.count}
                </span>
                <span className="pipeline-label text-muted">
                  {stage.label}
                </span>
              </div>
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}
