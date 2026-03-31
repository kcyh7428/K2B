import React from "react";
import { usePolling } from "../hooks/usePolling.js";

interface Task {
  name: string;
  schedule: string;
}

interface TasksData {
  tasks: Task[];
  nextRun: string | null;
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
            <>
              <div className="tasks-list">
                {data.tasks.map((t, i) => (
                  <div key={i} className="task-row">
                    <span className="task-name">{t.name}</span>
                    <span className="task-schedule text-muted">{t.schedule}</span>
                  </div>
                ))}
              </div>

              {data.nextRun && (
                <div className="task-footer text-muted">
                  next run: {data.nextRun}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
