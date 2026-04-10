import { useState, useEffect } from "react";
import { StatusDot } from "./StatusDot.js";
import { usePolling } from "../hooks/usePolling.js";

interface Pm2Process {
  name: string;
  status: string;
  uptime: number;
}

interface SystemData {
  processes: Pm2Process[];
  source: string;
}

function formatUptime(ms: number): string {
  if (ms <= 0) return "";
  const hours = Math.floor(ms / 1000 / 60 / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h`;
  return `${Math.floor(ms / 1000 / 60)}m`;
}

export function Header() {
  const { data, loading } = usePolling<SystemData>("/api/system", 15000);
  const [secondsAgo, setSecondsAgo] = useState(0);

  useEffect(() => {
    setSecondsAgo(0);
    const timer = setInterval(() => {
      setSecondsAgo((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [data]);

  const processes = data?.processes ?? [];
  const hasOnlineProcess = processes.some((p) => p.status === "online");
  const hasAnyProcess = processes.length > 0;

  const isOnline = hasOnlineProcess || hasAnyProcess;
  const statusLabel = loading && !data
    ? "connecting"
    : hasOnlineProcess
      ? "online"
      : hasAnyProcess
        ? "degraded"
        : "offline";
  const dotStatus = loading && !data
    ? "offline" as const
    : hasOnlineProcess
      ? "online" as const
      : hasAnyProcess
        ? "warning" as const
        : "error" as const;

  // Show uptime of the longest-running process
  const maxUptime = processes.reduce((max, p) => Math.max(max, p.uptime || 0), 0);
  const sourceLabel = data?.source === "mac-mini" ? " (Mac Mini)" : "";

  return (
    <header className="header">
      <div className="header-left">
        <span className="header-title">K2B Mission Control</span>
      </div>
      <div className="header-right">
        <div className="header-status">
          <StatusDot status={dotStatus} />
          <span>{statusLabel}{sourceLabel}</span>
        </div>
        {maxUptime > 0 && <span>up {formatUptime(maxUptime)}</span>}
        <span className="text-muted">refreshed {secondsAgo}s ago</span>
      </div>
    </header>
  );
}
