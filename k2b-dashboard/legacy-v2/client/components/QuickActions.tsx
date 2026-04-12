import { useState } from "react";

const ACTIONS = [
  { label: "/daily", command: "/daily" },
  { label: "/review", command: "/review" },
  { label: "/content", command: "/content" },
  { label: "/sync", command: "/sync" },
  { label: "/observe", command: "/observe" },
];

export function QuickActions() {
  const [toast, setToast] = useState<string | null>(null);
  const [custom, setCustom] = useState("");

  const copyCommand = async (cmd: string) => {
    try {
      await navigator.clipboard.writeText(cmd);
      setToast(`Copied ${cmd}`);
      setTimeout(() => setToast(null), 2000);
    } catch {
      setToast("Copy failed");
      setTimeout(() => setToast(null), 2000);
    }
  };

  const handleCustom = () => {
    if (custom.trim()) {
      copyCommand(custom.trim());
      setCustom("");
    }
  };

  return (
    <div className="quick-actions">
      <div className="quick-actions-buttons">
        {ACTIONS.map((a) => (
          <button
            key={a.command}
            className="btn quick-action-btn"
            onClick={() => copyCommand(a.command)}
          >
            {a.label}
          </button>
        ))}
        <div className="quick-action-custom">
          <input
            type="text"
            className="quick-action-input"
            placeholder="/ custom command..."
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCustom()}
          />
          <button
            className="btn quick-action-btn"
            onClick={handleCustom}
            disabled={!custom.trim()}
          >
            Send
          </button>
        </div>
      </div>
      {toast && <span className="quick-action-toast">{toast}</span>}
    </div>
  );
}
