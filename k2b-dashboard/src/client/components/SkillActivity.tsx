import React from "react";
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

          {/* Never Used */}
          {data.dormant.length > 0 && (
            <div className="skill-section">
              <div className="skill-section-label">Never Used</div>
              {data.dormant.map((s, i) => (
                <div key={i} className="skill-dormant-card">
                  <div className="skill-dormant-bar" />
                  <div className="skill-dormant-content">
                    <div className="skill-name">{stripPrefix(s.skill)}</div>
                    {s.description && (
                      <div className="text-secondary" style={{ fontSize: 11 }}>
                        {s.description}
                      </div>
                    )}
                    {s.tryHint && (
                      <div className="skill-try-hint">{s.tryHint}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
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
