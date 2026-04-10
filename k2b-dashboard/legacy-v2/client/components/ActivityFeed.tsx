import { useState } from "react";
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

function prefix(type: string): string {
  if (type === "vault_change") return "+";
  if (type === "git_commit") return ">";
  if (type === "skill_usage") return "*";
  return " ";
}

interface TimeBlock {
  label: string;
  items: ActivityItem[];
  summary: string;
}

function groupByTimeBlock(items: ActivityItem[]): TimeBlock[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);

  const blocks: Record<string, ActivityItem[]> = {};

  for (const item of items) {
    const d = new Date(item.timestamp);
    const itemDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    let label: string;

    if (itemDay.getTime() === today.getTime()) {
      const hour = d.getHours();
      if (hour < 12) label = "This morning";
      else if (hour < 17) label = "This afternoon";
      else label = "This evening";
    } else if (itemDay.getTime() === yesterday.getTime()) {
      label = "Yesterday";
    } else {
      label = d.toLocaleDateString("en-GB", { month: "short", day: "numeric" });
    }

    if (!blocks[label]) blocks[label] = [];
    blocks[label].push(item);
  }

  return Object.entries(blocks).map(([label, items]) => {
    const typeCounts: Record<string, number> = {};
    for (const item of items) {
      const typeLabel = item.type === "vault_change" ? "vault" : item.type === "git_commit" ? "git" : item.type === "skill_usage" ? "skill" : "other";
      typeCounts[typeLabel] = (typeCounts[typeLabel] || 0) + 1;
    }
    const summary = Object.entries(typeCounts)
      .map(([t, c]) => `${c} ${t}`)
      .join(", ");
    return { label, items, summary };
  });
}

export function ActivityFeed() {
  const { data, loading } = usePolling<ActivityData>("/api/activity", 30000);
  const [expandedBlocks, setExpandedBlocks] = useState<Set<string>>(new Set(["This morning", "This afternoon", "This evening"]));

  const items = (Array.isArray(data) ? data : []).slice(0, 30);
  const blocks = groupByTimeBlock(items);

  const toggleBlock = (label: string) => {
    setExpandedBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

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
        {blocks.map((block) => {
          const isExpanded = expandedBlocks.has(block.label);
          return (
            <div key={block.label} className="activity-block">
              <div
                className="activity-block-header"
                onClick={() => toggleBlock(block.label)}
              >
                <span className="activity-day-separator text-muted">
                  {block.label}
                </span>
                <span className="activity-block-summary text-muted">
                  {block.summary}
                </span>
                <span
                  className="activity-block-chevron text-muted"
                  style={{ transform: isExpanded ? "rotate(90deg)" : "none" }}
                >
                  &#9654;
                </span>
              </div>
              {isExpanded &&
                block.items.map((item, i) => (
                  <div key={i} className="activity-row">
                    <span className="activity-time text-muted">
                      {formatTime(item.timestamp)}
                    </span>
                    <span className="activity-prefix text-secondary">
                      {prefix(item.type)}
                    </span>
                    <span className="activity-desc">{item.description}</span>
                  </div>
                ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
