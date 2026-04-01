import { usePolling } from "../hooks/usePolling.js";

interface Suggestion {
  priority: number;
  message: string;
  command: string;
}

interface SuggestedData {
  suggestion: Suggestion;
  all: Suggestion[];
}

export function SuggestedAction() {
  const { data, loading } = usePolling<SuggestedData>(
    "/api/suggested-action",
    30000,
  );

  if (loading && !data) return null;

  const s = data?.suggestion;
  if (!s) return null;

  const isUrgent = s.priority <= 2;
  const copyCommand = async () => {
    if (s.command) {
      try {
        await navigator.clipboard.writeText(s.command);
      } catch { /* fallback: user can read it */ }
    }
  };

  return (
    <div
      className={`suggested-action ${isUrgent ? "suggested-action-urgent" : ""}`}
      onClick={copyCommand}
      title={s.command ? `Click to copy: ${s.command}` : undefined}
      style={{ cursor: s.command ? "pointer" : "default" }}
    >
      <span className="suggested-action-label">Next action</span>
      <span className="suggested-action-message">{s.message}</span>
      {s.command && (
        <span className="suggested-action-command text-muted">{s.command}</span>
      )}
    </div>
  );
}
