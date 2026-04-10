import React from "react";
import { usePolling } from "../hooks/usePolling.js";
import { StatusDot } from "./StatusDot.js";

interface Pm2Process {
  name: string;
  status: string;
  uptime: number;
  memory: number;
  cpu: number;
  pid: number;
  restarts: number;
}

interface HealthData {
  uptime?: number;
  memory?: number;
  [key: string]: unknown;
}

interface SystemData {
  processes: Pm2Process[];
  health: HealthData | null;
  vault: { total: number };
  git: { hash: string; message: string; date: string } | null;
}

function formatUptime(ms: number): string {
  if (ms <= 0) return "---";
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 0) return `${days}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${seconds}s`;
}

function formatMemory(bytes: number): string {
  if (bytes <= 0) return "---";
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

function dotStatus(status: string): "online" | "warning" | "error" | "offline" {
  if (status === "online") return "online";
  if (status === "launching" || status === "stopping") return "warning";
  if (status === "errored") return "error";
  return "offline";
}

export function SystemStatus() {
  const { data, loading } = usePolling<SystemData>("/api/system", 15000);

  if (loading && !data) {
    return (
      <div className="panel">
        <span className="panel-title">System Status</span>
        <div className="text-muted">Loading...</div>
      </div>
    );
  }

  const processes = data?.processes ?? [];
  const totalMemory = processes.length > 0
    ? processes.reduce((sum, p) => sum + p.memory, 0)
    : (data?.health?.memory as number) || 0;
  const sessionCount = processes.length > 0 ? processes.length : (data?.health ? 1 : 0);
  const syncStatus = data?.git ? "synced" : "unknown";

  return (
    <div className="panel">
      <span className="panel-title">System Status</span>
      <div className="system-processes">
        {processes.map((proc) => (
          <div key={proc.name} className="system-process-row">
            <StatusDot status={dotStatus(proc.status)} />
            <span className="system-process-name">{proc.name}</span>
            <span className="text-muted">{formatUptime(proc.uptime)}</span>
          </div>
        ))}
        {processes.length === 0 && data?.health && (
          <>
            <div className="system-process-row">
              <StatusDot status="online" />
              <span className="system-process-name">k2b-remote</span>
              <span className="text-muted">
                {data.health.uptime ? formatUptime(data.health.uptime as number) : "running"}
              </span>
            </div>
            <div className="text-muted" style={{ fontSize: 11, marginTop: 4 }}>
              pm2 status unavailable on this machine
            </div>
          </>
        )}
        {processes.length === 0 && !data?.health && (
          <div className="text-muted">No processes found</div>
        )}
      </div>
      <div className="panel-footer">
        <span className="text-secondary">mem {formatMemory(totalMemory)}</span>
        <span className="text-muted">|</span>
        <span className="text-secondary">{sessionCount} processes</span>
        <span className="text-muted">|</span>
        <span className="text-secondary">vault {syncStatus}</span>
      </div>
    </div>
  );
}
