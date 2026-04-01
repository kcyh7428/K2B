import { useState, useCallback } from "react";
import { usePolling } from "../hooks/usePolling.js";
import { Tag } from "./Tag.js";

interface InboxItem {
  filename: string;
  title: string;
  type: string;
  origin: string;
  date: string;
  tags: string[];
  reviewAction: string;
  reviewNotes: string;
  path: string;
  excerpt: string;
}

interface InboxData {
  items: InboxItem[];
  readyCount: number;
  totalCount: number;
  oldestAgeDays: number;
  statusCounts: Record<string, number>;
}

type FilterType = "all" | "video-capture" | "research-briefing" | "feature-idea";

function daysAgo(dateStr: string): number {
  if (!dateStr) return 0;
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return 0;
  return Math.floor((Date.now() - date.getTime()) / (1000 * 60 * 60 * 24));
}

function daysAgoLabel(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "1d";
  return `${days}d`;
}

function ageColorClass(days: number): string {
  if (days >= 5) return "text-red";
  if (days >= 2) return "text-amber";
  return "text-muted";
}

function originColor(origin: string): "next" | "shipped" | "planned" | "default" {
  if (origin === "keith") return "shipped";
  if (origin === "k2b-extract") return "next";
  if (origin === "k2b-generate") return "planned";
  return "default";
}

function typeLabel(type: string): string {
  switch (type) {
    case "video-capture": return "Video";
    case "research-briefing": return "Research";
    case "feature-idea": return "Feature";
    case "content-idea": return "Content";
    default: return type || "note";
  }
}

function cleanExcerpt(text: string): string {
  return text
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*/g, "")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .trim()
    .slice(0, 200);
}

const FILTER_OPTIONS: { key: FilterType; label: string }[] = [
  { key: "all", label: "All" },
  { key: "video-capture", label: "Videos" },
  { key: "research-briefing", label: "Research" },
  { key: "feature-idea", label: "Features" },
];

export function Inbox() {
  const { data, loading, refresh } = usePolling<InboxData>("/api/inbox", 30000);
  const [filter, setFilter] = useState<FilterType>("all");
  const [expandedItem, setExpandedItem] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState<string | null>(null);

  const performAction = useCallback(async (filename: string, action: "archive" | "snooze") => {
    setActionPending(`${filename}-${action}`);
    try {
      const res = await fetch(`/api/inbox/${filename}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (res.ok) {
        setTimeout(refresh, 500);
      }
    } catch { /* ignore */ }
    setActionPending(null);
  }, [refresh]);

  if (loading && !data) {
    return (
      <div className="panel panel-priority">
        <span className="panel-title panel-title-priority">Inbox</span>
        <div className="text-muted">Loading...</div>
      </div>
    );
  }

  const items = data?.items ?? [];
  const totalCount = data?.totalCount ?? 0;
  const oldestAge = data?.oldestAgeDays ?? 0;

  // Filter
  const filteredItems = filter === "all"
    ? items
    : items.filter((i) => i.type === filter);

  // Count per type for tab badges
  const typeCounts: Record<string, number> = {};
  for (const item of items) {
    const t = item.type || "other";
    typeCounts[t] = (typeCounts[t] || 0) + 1;
  }

  const ageColor = ageColorClass(oldestAge);

  return (
    <div className="panel panel-priority">
      <div className="inbox-header">
        <span className="panel-title panel-title-priority" style={{ marginBottom: 0 }}>
          Inbox &middot; {totalCount}
        </span>
        <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11 }}>
          {oldestAge > 0 && (
            <span className={ageColor} style={{ fontWeight: 600 }}>
              oldest: {oldestAge}d
            </span>
          )}
        </div>
      </div>

      {/* Filter tabs */}
      <div className="inbox-filters">
        {FILTER_OPTIONS.map((f) => {
          const count = f.key === "all" ? totalCount : (typeCounts[f.key] || 0);
          if (f.key !== "all" && count === 0) return null;
          return (
            <button
              key={f.key}
              className={`inbox-filter-btn ${filter === f.key ? "inbox-filter-active" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label} <span className="inbox-filter-count">{count}</span>
            </button>
          );
        })}
      </div>

      {/* Items */}
      <div className="inbox-items">
        {filteredItems.map((item) => {
          const age = daysAgo(item.date);
          const isExpanded = expandedItem === item.filename;
          const isActioning = actionPending?.startsWith(item.filename);

          return (
            <div key={item.path} className={`inbox-card ${isExpanded ? "inbox-card-expanded" : ""}`}>
              <div
                className="inbox-card-header"
                onClick={() => setExpandedItem(isExpanded ? null : item.filename)}
                style={{ cursor: "pointer" }}
              >
                <div className="inbox-card-title-row">
                  <span className={`inbox-age ${ageColorClass(age)}`}>
                    {daysAgoLabel(age)}
                  </span>
                  <span className="inbox-card-title">{item.filename}</span>
                </div>
                <div className="inbox-card-actions">
                  <button
                    className="btn btn-neutral inbox-action-btn"
                    title="Snooze 3 days"
                    disabled={!!isActioning}
                    onClick={(e) => { e.stopPropagation(); performAction(item.filename, "snooze"); }}
                  >
                    {isActioning && actionPending === `${item.filename}-snooze` ? "..." : "Snooze"}
                  </button>
                  <button
                    className="btn btn-danger inbox-action-btn"
                    title="Archive"
                    disabled={!!isActioning}
                    onClick={(e) => { e.stopPropagation(); performAction(item.filename, "archive"); }}
                  >
                    {isActioning && actionPending === `${item.filename}-archive` ? "..." : "Archive"}
                  </button>
                </div>
              </div>
              <div className="inbox-card-meta">
                <Tag variant={originColor(item.origin)}>{item.origin}</Tag>
                <Tag variant="default">{typeLabel(item.type)}</Tag>
                {item.tags?.slice(0, 3).map((tag) => (
                  <span key={tag} className="inbox-tag">{tag}</span>
                ))}
                {item.reviewAction && (
                  <Tag variant="next">{item.reviewAction}</Tag>
                )}
              </div>
              {isExpanded && item.excerpt && (
                <div className="inbox-card-preview text-secondary">
                  {cleanExcerpt(item.excerpt)}
                </div>
              )}
            </div>
          );
        })}
        {filteredItems.length === 0 && (
          <div className="text-muted" style={{ fontSize: 12 }}>
            {filter === "all" ? "Inbox empty" : `No ${filter} items`}
          </div>
        )}
      </div>
    </div>
  );
}
