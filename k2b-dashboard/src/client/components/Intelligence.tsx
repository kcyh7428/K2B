import React from "react";
import { usePolling } from "../hooks/usePolling.js";
import { ExpandableRow } from "./ExpandableRow.js";
import { Tag } from "./Tag.js";

interface Candidate {
  confidence: string;
  category?: string;
  description: string;
  evidence?: string;
}

interface Pattern {
  confidence: string;
  description: string;
  recommendation?: string;
}

interface Learning {
  id: string;
  area: string;
  learning: string;
  reinforced: number;
  confidence: string;
  date: string;
}

interface IntelligenceData {
  candidates: Candidate[];
  patterns: Pattern[];
  learnings: Learning[];
  observer: {
    lastAnalysis: string;
    observationsAnalyzed: number;
    currentObservations: number;
    summary: string;
  };
}

export function Intelligence() {
  const { data, loading } = usePolling<IntelligenceData>(
    "/api/intelligence",
    30000,
  );

  return (
    <div className="panel panel-intelligence">
      <span className="panel-title panel-title-intelligence">
        &#9733; Intelligence
      </span>

      {loading && !data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Loading...
        </div>
      )}

      {data && (
        <>
          {/* Pending Confirmation */}
          <div className="intelligence-section">
            <div className="intelligence-section-label">
              Pending Confirmation
            </div>
            {data.candidates.length === 0 && data.patterns.length === 0 && (
              <div className="text-muted" style={{ fontSize: 12 }}>
                No candidates pending
              </div>
            )}
            {data.candidates.map((c, i) => (
              <ExpandableRow
                key={`c-${i}`}
                title={c.description}
                rightContent={
                  <Tag variant={c.confidence === "high" ? "shipped" : "default"}>
                    {c.confidence}
                  </Tag>
                }
              >
                <div style={{ fontSize: 12, marginBottom: 8 }}>
                  {c.evidence || "No evidence recorded"}
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="btn btn-action" disabled title="Coming in v2">
                    Confirm
                  </button>
                  <button className="btn btn-danger" disabled title="Coming in v2">
                    Dismiss
                  </button>
                </div>
              </ExpandableRow>
            ))}
            {data.patterns.map((p, i) => (
              <ExpandableRow
                key={`p-${i}`}
                title={p.description}
                subtitle={p.recommendation || undefined}
                rightContent={
                  <Tag variant="default">{p.confidence}</Tag>
                }
              >
                <div style={{ fontSize: 12 }}>
                  {p.recommendation || "No recommendation"}
                </div>
              </ExpandableRow>
            ))}
          </div>

          {/* Recently Learned */}
          <div className="intelligence-section">
            <div className="intelligence-section-label">Recently Learned</div>
            {data.learnings.filter(l => l.learning).slice(0, 5).map((l, i) => (
              <div key={i} className="intelligence-learning-row">
                <span
                  className={`intelligence-badge ${l.reinforced >= 6 ? "intelligence-badge-high" : l.reinforced >= 3 ? "intelligence-badge-mid" : "intelligence-badge-low"}`}
                >
                  &times;{l.reinforced}
                </span>
                <span className="intelligence-learning-text">{l.learning}</span>
              </div>
            ))}
            {data.learnings.filter(l => l.learning).length === 0 && (
              <div className="text-muted" style={{ fontSize: 12 }}>
                No learnings captured yet
              </div>
            )}
          </div>

          {/* Observer */}
          <div className="intelligence-section">
            <div className="intelligence-section-label">Observer</div>
            <div className="text-secondary" style={{ fontSize: 12 }}>
              Last analysis: {data.observer.lastAnalysis || "never"}
              {" \u00b7 "}
              {data.observer.observationsAnalyzed} analyzed
              {" \u00b7 "}
              {data.patterns.length} patterns
            </div>
            <div className="text-muted" style={{ fontSize: 11 }}>
              Next: when 20+ new observations
              {data.observer.currentObservations > 0 &&
                ` (${data.observer.currentObservations} current)`}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
