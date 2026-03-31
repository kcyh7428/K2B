import React from "react";
import { usePolling } from "../hooks/usePolling.js";

interface VaultCounts {
  total: number;
  daily: number;
  people: number;
  projects: number;
  features: number;
  insights: number;
  contentIdeas: number;
}

interface SystemData {
  vault: VaultCounts;
}

export function VaultStats() {
  const { data, loading } = usePolling<SystemData>("/api/system", 30000);

  if (loading && !data) {
    return (
      <div className="panel">
        <span className="panel-title">Vault Stats</span>
        <div className="text-muted">Loading...</div>
      </div>
    );
  }

  const vault = data?.vault;
  const total = vault?.total ?? 0;

  const grid = [
    { label: "Daily", count: vault?.daily ?? 0 },
    { label: "People", count: vault?.people ?? 0 },
    { label: "Projects", count: vault?.projects ?? 0 },
    { label: "Features", count: vault?.features ?? 0 },
    { label: "Insights", count: vault?.insights ?? 0 },
    { label: "Content Ideas", count: vault?.contentIdeas ?? 0 },
  ];

  return (
    <div className="panel">
      <span className="panel-title">Vault Stats</span>
      <div className="vault-total">{total}</div>
      <div className="vault-total-label text-muted">total notes</div>
      <div className="vault-grid">
        {grid.map((item) => (
          <div key={item.label} className="vault-grid-item">
            <div className="vault-grid-count">{item.count}</div>
            <div className="vault-grid-label text-muted">{item.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
