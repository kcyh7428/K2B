import { useState } from "react";
import { usePolling } from "../hooks/usePolling.js";
import { Tag } from "./Tag.js";

interface Recommendation {
  video_id: string;
  title: string;
  channel: string;
  duration: string;
  status: string;
  outcome: string | null;
  rating: string | null;
  recommended_date: string;
  pick_reason: string;
  topics: string[];
  verdict_value?: string;
}

interface PendingVideo {
  videoId: string;
  title: string;
  duration: string;
  channel: string;
}

interface ExtractedVideo {
  videoId: string;
  date: string;
  title: string;
  notes: string;
}

interface YouTubeData {
  stats: {
    totalRecs: number;
    responseRate: number;
    lastRun: string;
  };
  recommendations: Recommendation[];
  pending: PendingVideo[];
  extracted: ExtractedVideo[];
  skippedCount: number;
  totalProcessed: number;
}

type ResponseType = "watch" | "screen" | "skip" | "comment" | "expired" | "pending";

function getResponseType(rec: Recommendation): ResponseType {
  if (rec.outcome === "promoted" || rec.outcome === "implemented") return "watch";
  if (rec.status === "promoted" || rec.status === "done") return "watch";
  if (rec.outcome === "screen") return "screen";
  if (rec.outcome === "skip" || rec.status === "skipped") return "skip";
  if (rec.outcome === "comment") return "comment";
  if (rec.status === "expired") return "expired";
  return "pending";
}

function responseBadgeClass(type: ResponseType): string {
  switch (type) {
    case "watch": return "yt-badge-watch";
    case "screen": return "yt-badge-screen";
    case "skip": return "yt-badge-skip";
    case "comment": return "yt-badge-comment";
    case "expired": return "yt-badge-expired";
    default: return "yt-badge-pending";
  }
}

function responseLabel(type: ResponseType): string {
  switch (type) {
    case "watch": return "Watch";
    case "screen": return "Screen";
    case "skip": return "Skip";
    case "comment": return "Comment";
    case "expired": return "Expired";
    default: return "Pending";
  }
}

function verdictLabel(rec: Recommendation): string | null {
  if (rec.verdict_value) return rec.verdict_value.toUpperCase();
  // Infer from pick_reason length / specificity
  if (rec.pick_reason && rec.pick_reason.length > 60) return "HIGH";
  return null;
}

export function YouTubeDigest() {
  const { data, loading } = usePolling<YouTubeData>("/api/youtube", 60000);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (loading && !data) {
    return (
      <div className="panel panel-priority">
        <span className="panel-title panel-title-priority">
          {"\u2605"} YouTube
        </span>
        <div className="text-muted">Loading...</div>
      </div>
    );
  }

  const stats = data?.stats;
  const recs = data?.recommendations ?? [];
  const pending = data?.pending ?? [];
  const extracted = data?.extracted ?? [];
  const skippedCount = data?.skippedCount ?? 0;

  return (
    <div className="panel panel-priority">
      {/* Header stats line */}
      <div className="yt-header">
        <span className="panel-title panel-title-priority" style={{ marginBottom: 0 }}>
          {"\u2605"} YouTube
        </span>
        <div className="yt-stats">
          <span>{stats?.totalRecs ?? 0} recs</span>
          <span className="yt-stats-sep">&middot;</span>
          <span>{stats?.responseRate ?? 0}% response rate</span>
          <span className="yt-stats-sep">&middot;</span>
          <span>last run: {stats?.lastRun || "never"}</span>
        </div>
      </div>

      <div className="youtube-columns">
        {/* Left: Recent Recommendations */}
        <div className="youtube-column">
          <div className="youtube-column-header text-secondary">
            Recommendations ({recs.length})
          </div>
          <div className="yt-rec-list">
            {recs.slice(0, 7).map((rec) => {
              const response = getResponseType(rec);
              const verdict = verdictLabel(rec);
              const isExpanded = expandedId === rec.video_id;

              return (
                <div
                  key={rec.video_id}
                  className={`yt-rec-item ${isExpanded ? "yt-rec-expanded" : ""}`}
                  onClick={() => setExpandedId(isExpanded ? null : rec.video_id)}
                >
                  <div className="yt-rec-row">
                    <span className={`yt-badge ${responseBadgeClass(response)}`}>
                      {responseLabel(response)}
                    </span>
                    <span className="yt-rec-title">{rec.title}</span>
                    {verdict && (
                      <span className="yt-verdict">{verdict}</span>
                    )}
                  </div>
                  <div className="yt-rec-meta text-muted">
                    {rec.channel} &middot; {rec.duration}
                    {rec.topics.length > 0 && (
                      <> &middot; {rec.topics.slice(0, 2).join(", ")}</>
                    )}
                  </div>
                  {isExpanded && rec.pick_reason && (
                    <div className="yt-rec-reason text-secondary">
                      {rec.pick_reason}
                    </div>
                  )}
                </div>
              );
            })}
            {recs.length === 0 && (
              <div className="text-muted">No recommendations yet</div>
            )}
          </div>
        </div>

        {/* Right: Screening Pipeline */}
        <div className="youtube-column">
          <div className="youtube-column-header text-secondary">
            Screening Pipeline
          </div>

          {/* Pending screening */}
          {pending.length > 0 && (
            <div className="yt-pipeline-section">
              <div className="yt-pipeline-label text-muted">
                Pending extraction ({pending.length})
              </div>
              {pending.map((v) => (
                <div key={v.videoId} className="yt-pipeline-item">
                  <Tag variant="next">pending</Tag>
                  <span className="yt-pipeline-title">{v.title}</span>
                  <span className="text-muted" style={{ fontSize: 10, flexShrink: 0 }}>
                    {v.duration}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Recently Extracted */}
          <div className="yt-pipeline-section">
            <div className="yt-pipeline-label text-muted">
              Recently Extracted ({extracted.length})
            </div>
            {extracted.slice(0, 5).map((v) => {
              const takeaway = v.notes
                ? v.notes.split("|")[0]?.trim().slice(0, 80)
                : "";
              return (
                <div key={v.videoId + v.date} className="yt-pipeline-item">
                  <Tag variant="shipped">done</Tag>
                  <span className="yt-pipeline-title">
                    {v.title || v.videoId}
                  </span>
                  {takeaway && (
                    <span className="yt-takeaway text-secondary">{takeaway}</span>
                  )}
                </div>
              );
            })}
            {extracted.length === 0 && (
              <div className="text-muted" style={{ fontSize: 12 }}>
                No extractions yet
              </div>
            )}
          </div>

          {/* Skipped count */}
          {skippedCount > 0 && (
            <div className="yt-skipped-count text-muted">
              {skippedCount} skipped
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
