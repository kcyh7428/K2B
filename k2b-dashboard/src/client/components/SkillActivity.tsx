import { useState } from "react";
import { usePolling } from "../hooks/usePolling.js";
import { StatusDot } from "./StatusDot.js";

interface ActiveSkill {
  skill: string;
  count: number;
  lastUsed: string;
}

interface DormantSkill {
  skill: string;
  description: string;
  tryHint: string;
}

interface SkillsData {
  active: ActiveSkill[];
  dormant: DormantSkill[];
  totalInvocations: number;
  activeCount: number;
  dormantCount: number;
}

function dotStatus(count: number): "online" | "warning" | "offline" {
  if (count >= 10) return "online";
  if (count >= 3) return "warning";
  return "offline";
}

function barColor(count: number): string {
  if (count >= 10) return "#22c55e";
  if (count >= 3) return "#3b82f6";
  return "#333";
}

function stripPrefix(name: string): string {
  return name.replace(/^k2b-/, "");
}

function shortDescription(desc: string): string {
  // Extract the first sentence (before "Use when" or second "--")
  const cleaned = desc.replace(/^k2b-\S+\s*/, "");
  const firstSentence = cleaned.split(/\.\s|--\s/)[0]?.trim();
  if (!firstSentence || firstSentence.length < 5) return cleaned.slice(0, 80);
  return firstSentence.length > 80 ? firstSentence.slice(0, 77) + "..." : firstSentence;
}

function DormantSection({ dormant }: { dormant: DormantSkill[] }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="skill-section">
      <div
        className="skill-section-label"
        style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center" }}
        onClick={() => setExpanded(!expanded)}
      >
        <span>{dormant.length} skills never used</span>
        <span style={{ fontSize: 10, transition: "transform 0.2s", transform: expanded ? "rotate(90deg)" : "none" }}>
          &#9654;
        </span>
      </div>
      {expanded && dormant.map((s, i) => (
        <div key={i} className="skill-dormant-card-v2">
          <div className="skill-dormant-header">
            <span className="skill-name">{stripPrefix(s.skill)}</span>
            {s.tryHint && <span className="skill-try-hint">{s.tryHint}</span>}
          </div>
          {s.description && (
            <div className="skill-dormant-desc text-secondary">
              {shortDescription(s.description)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function SkillActivity() {
  const { data, loading } = usePolling<SkillsData>("/api/skills", 30000);

  return (
    <div className="panel panel-intelligence">
      <span className="panel-title panel-title-intelligence">
        Skill Activity
      </span>

      {loading && !data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Loading...
        </div>
      )}

      {data && (
        <>
          {/* Active (7 days) */}
          <div className="skill-section">
            <div className="skill-section-label">Active (7 days)</div>
            {(() => {
              const sorted = [...data.active].sort((a, b) => b.count - a.count);
              const maxCount = sorted.length > 0 ? sorted[0].count : 1;
              return sorted.map((s, i) => (
                <div key={i} className="skill-active-row">
                  <StatusDot status={dotStatus(s.count)} />
                  <span className="skill-name">{stripPrefix(s.skill)}</span>
                  <div className="skill-bar-bg">
                    <div
                      className="skill-bar"
                      style={{
                        width: `${(s.count / maxCount) * 100}%`,
                        background: barColor(s.count),
                      }}
                    />
                  </div>
                  <span className="skill-count">{s.count}</span>
                  <span className="skill-last-used text-muted">{s.lastUsed}</span>
                </div>
              ));
            })()}
          </div>

          {/* Never Used -- collapsible */}
          {data.dormant.length > 0 && (
            <DormantSection dormant={data.dormant} />
          )}

          {/* Footer */}
          <div className="skill-footer text-muted">
            {data.totalInvocations} invocations &middot;{" "}
            {data.active.length} active &middot;{" "}
            {data.dormant.length} dormant
          </div>
        </>
      )}
    </div>
  );
}
