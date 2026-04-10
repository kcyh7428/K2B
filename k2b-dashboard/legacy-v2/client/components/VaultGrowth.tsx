import { usePolling } from "../hooks/usePolling.js";

interface DayCount {
  date: string;
  count: number;
}

interface GrowthData {
  growth: DayCount[];
}

export function VaultGrowth() {
  const { data, loading } = usePolling<GrowthData>("/api/vault/growth", 60000);

  if (loading && !data) return null;

  const days = data?.growth ?? [];
  if (days.length === 0) return null;

  const maxCount = Math.max(...days.map((d) => d.count), 1);
  const totalNew = days.reduce((sum, d) => sum + d.count, 0);
  const chartHeight = 60;

  return (
    <div className="vault-growth">
      <div className="vault-growth-header">
        <span className="vault-growth-label text-muted">30-day growth</span>
        <span className="vault-growth-total">
          +{totalNew} <span className="text-muted">notes</span>
        </span>
      </div>
      <div className="vault-growth-chart" style={{ height: chartHeight }}>
        {days.map((d) => {
          const h = maxCount > 0 ? (d.count / maxCount) * chartHeight : 0;
          return (
            <div
              key={d.date}
              className="vault-growth-bar"
              style={{ height: Math.max(h, 1) }}
              title={`${d.date}: ${d.count} notes`}
            />
          );
        })}
      </div>
    </div>
  );
}
