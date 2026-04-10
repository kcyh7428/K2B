import { usePolling } from "../hooks/usePolling.js";
import { StatusDot } from "./StatusDot.js";

interface Task {
  name: string;
  schedule: string;
  status: string;
  lastRun: string | null;
  nextRun: string;
  source: string;
}

interface TasksData {
  tasks: Task[];
  nextRun: string | null;
}

function taskDotStatus(task: Task): "online" | "warning" | "error" | "offline" {
  if (task.source === "pm2") {
    return task.status === "online" ? "online" : "error";
  }
  if (task.status === "error" || task.status === "failed") return "error";
  if (task.status === "active" || task.status === "online") return "online";
  return "offline";
}

function formatNextRun(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();
  if (diffMs < 0) return "overdue";
  const diffH = Math.floor(diffMs / (1000 * 60 * 60));
  const diffM = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
  if (diffH > 24) return `in ${Math.floor(diffH / 24)}d`;
  if (diffH > 0) return `in ${diffH}h ${diffM}m`;
  return `in ${diffM}m`;
}

export function ScheduledTasks() {
  const { data, loading } = usePolling<TasksData>("/api/tasks", 30000);

  return (
    <div className="panel">
      <span className="panel-title">Scheduled Tasks</span>

      {loading && !data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Loading...
        </div>
      )}

      {data && (
        <>
          {data.tasks.length === 0 ? (
            <div className="text-muted" style={{ fontSize: 12 }}>
              No scheduled tasks. Tasks run on Mac Mini via k2b-remote.
            </div>
          ) : (
            <div className="tasks-list">
              {data.tasks.map((t, i) => (
                <div key={i} className="task-row">
                  <StatusDot status={taskDotStatus(t)} />
                  <span className="task-name">{t.name}</span>
                  <span className="task-schedule text-muted">{t.schedule}</span>
                  {t.nextRun && (
                    <span className="task-next text-muted">{formatNextRun(t.nextRun)}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
