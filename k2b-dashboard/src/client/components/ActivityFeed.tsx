import React from "react";
import { usePolling } from "../hooks/usePolling.js";

interface ActivityItem {
  timestamp: string;
  description: string;
  type: string;
}

type ActivityData = ActivityItem[];

function formatTime(timestamp: string): string {
  const d = new Date(timestamp);
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function formatDayLabel(timestamp: string): string {
  const d = new Date(timestamp);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const itemDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round(
    (today.getTime() - itemDay.getTime()) / (1000 * 60 * 60 * 24),
  );

  if (diffDays === 0) return "today";
  if (diffDays === 1) return "yesterday";
  return d.toLocaleDateString("en-GB", { month: "short", day: "numeric" });
}

function prefix(type: string): string {
  if (type === "vault_change") return "+";
  if (type === "git_commit") return ">";
  if (type === "skill_usage") return "*";
  return " ";
}

export function ActivityFeed() {
  const { data, loading } = usePolling<ActivityData>("/api/activity", 30000);

  const items = (Array.isArray(data) ? data : []).slice(0, 15);

  // Group by day
  let lastDay = "";

  return (
    <div className="panel">
      <span className="panel-title">Activity Feed</span>

      {loading && !data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Loading...
        </div>
      )}

      {items.length === 0 && data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          No recent activity
        </div>
      )}

      <div className="activity-list">
        {items.map((item, i) => {
          const dayLabel = formatDayLabel(item.timestamp);
          const showSeparator = dayLabel !== lastDay;
          lastDay = dayLabel;

          return (
            <React.Fragment key={i}>
              {showSeparator && (
                <div className="activity-day-separator text-muted">
                  {dayLabel}
                </div>
              )}
              <div className="activity-row">
                <span className="activity-time text-muted">
                  {formatTime(item.timestamp)}
                </span>
                <span className="activity-prefix text-secondary">
                  {prefix(item.type)}
                </span>
                <span className="activity-desc">{item.description}</span>
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
