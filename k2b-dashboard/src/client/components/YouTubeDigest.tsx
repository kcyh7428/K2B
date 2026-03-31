import { usePolling } from "../hooks/usePolling.js";
import { ExpandableRow } from "./ExpandableRow.js";
import { Tag } from "./Tag.js";

interface RecommendedVideo {
  video_id: string;
  title: string;
  channel: string;
  duration: string;
  status: string;
  nudge_date: string;
}

interface CurrentQueueVideo {
  videoId: string;
  title: string;
  duration: string;
  channel: string;
}

interface ProcessedVideo {
  videoId: string;
  date: string;
  title: string;
  notes: string;
}

interface YouTubeData {
  watch: {
    pending: RecommendedVideo[];
    totalCount: number;
  };
  queue: {
    current: CurrentQueueVideo[];
    recentlyProcessed: ProcessedVideo[];
    totalProcessed: number;
  };
}

function formatNudgeDate(dateStr: string): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function YouTubeDigest() {
  const { data, loading } = usePolling<YouTubeData>("/api/youtube", 60000);

  if (loading && !data) {
    return (
      <div className="panel panel-priority">
        <span className="panel-title panel-title-priority">
          {"\u2605"} YouTube Digest
        </span>
        <div className="text-muted">Loading...</div>
      </div>
    );
  }

  const watchVideos = data?.watch.pending ?? [];
  const currentQueue = data?.queue.current ?? [];
  const recentlyProcessed = data?.queue.recentlyProcessed ?? [];
  const totalProcessed = data?.queue.totalProcessed ?? 0;

  return (
    <div className="panel panel-priority">
      <span className="panel-title panel-title-priority">
        {"\u2605"} YouTube Digest
      </span>
      <div className="youtube-columns">
        <div className="youtube-column">
          <div className="youtube-column-header text-secondary">
            K2B Watch -- recommended to you ({watchVideos.length})
          </div>
          <div className="panel-rows">
            {watchVideos.map((video) => {
              const nudge = video.nudge_date
                ? `nudged ${formatNudgeDate(video.nudge_date)}`
                : "";
              const parts = [video.channel, video.duration, nudge].filter(Boolean);
              return (
                <ExpandableRow
                  key={video.video_id}
                  title={video.title}
                  subtitle={parts.join(" \u00b7 ")}
                >
                  <div className="row-detail-actions">
                    <button className="btn" disabled title="Coming in v2">
                      Watch
                    </button>
                    <button className="btn" disabled title="Coming in v2">
                      Skip
                    </button>
                  </div>
                </ExpandableRow>
              );
            })}
            {watchVideos.length === 0 && (
              <div className="text-muted">No pending recommendations</div>
            )}
          </div>
        </div>
        <div className="youtube-column">
          <div className="youtube-column-header text-secondary">
            K2B Queue -- in playlist ({currentQueue.length})
          </div>
          <div className="panel-rows">
            {currentQueue.map((video) => (
              <ExpandableRow
                key={video.videoId}
                title={video.title}
                subtitle={`${video.channel} \u00b7 ${video.duration}`}
                rightContent={<Tag variant="next">pending</Tag>}
              >
                <div className="row-detail-actions">
                  <button className="btn" disabled title="Coming in v2">
                    Process
                  </button>
                  <button className="btn" disabled title="Coming in v2">
                    Skip
                  </button>
                </div>
              </ExpandableRow>
            ))}
            {currentQueue.length === 0 && (
              <div className="text-muted">Queue empty</div>
            )}
          </div>

          {recentlyProcessed.length > 0 && (
            <>
              <div className="youtube-column-header text-muted" style={{ marginTop: 12 }}>
                Recently processed ({totalProcessed} total)
              </div>
              <div className="panel-rows">
                {recentlyProcessed.slice(0, 3).map((video) => {
                  const notesLower = (video.notes || "").toLowerCase();
                  const outcome = notesLower.includes("skip") ? "skipped" : "done";
                  return (
                    <ExpandableRow
                      key={video.videoId + video.date}
                      title={video.title || video.videoId}
                      subtitle={`${video.date} \u00b7 ${outcome}`}
                      rightContent={
                        <Tag variant={outcome === "done" ? "shipped" : "default"}>
                          {outcome}
                        </Tag>
                      }
                    >
                      <div className="row-detail-text">
                        <p className="text-secondary">
                          {video.notes || "No processing notes"}
                        </p>
                      </div>
                    </ExpandableRow>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
