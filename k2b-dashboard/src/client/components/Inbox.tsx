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
}

function daysAgo(dateStr: string): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "";
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "1 day ago";
  return `${diffDays} days ago`;
}

function originColor(origin: string): "next" | "shipped" | "planned" | "default" {
  if (origin === "keith") return "shipped";
  if (origin === "k2b-extract") return "next";
  if (origin === "k2b-generate") return "planned";
  return "default";
}

function cleanExcerpt(text: string): string {
  return text
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*/g, "")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .trim()
    .slice(0, 200);
}

export function Inbox() {
  const { data, loading } = usePolling<InboxData>("/api/inbox", 30000);

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
  const readyCount = data?.readyCount ?? 0;

  return (
    <div className="panel panel-priority">
      <div className="inbox-header">
        <span className="panel-title panel-title-priority">
          Inbox &middot; {totalCount} items
        </span>
        <span className="text-secondary" style={{ fontSize: 11 }}>
          {readyCount} ready for processing
        </span>
      </div>
      <div className="inbox-items">
        {items.map((item) => (
          <div key={item.path} className="inbox-card">
            <div className="inbox-card-header">
              <span className="inbox-card-title">{item.filename}</span>
              <span className="text-muted" style={{ fontSize: 11, flexShrink: 0 }}>
                {daysAgo(item.date)}
              </span>
            </div>
            {item.excerpt && (
              <div className="inbox-card-excerpt">
                {cleanExcerpt(item.excerpt)}
              </div>
            )}
            <div className="inbox-card-meta">
              <Tag variant={originColor(item.origin)}>{item.origin}</Tag>
              <Tag variant="default">{item.type}</Tag>
              {item.tags?.slice(0, 3).map((tag) => (
                <span key={tag} className="inbox-tag">{tag}</span>
              ))}
              {item.reviewAction && (
                <Tag variant="next">{item.reviewAction}</Tag>
              )}
            </div>
          </div>
        ))}
        {items.length === 0 && (
          <div className="text-muted">Inbox empty</div>
        )}
      </div>
    </div>
  );
}
