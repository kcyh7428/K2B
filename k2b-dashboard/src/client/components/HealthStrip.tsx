import { usePolling } from "../hooks/usePolling.js";

interface Alert {
  level: "red" | "yellow";
  message: string;
}

interface HealthData {
  status: "nominal" | "warning" | "critical";
  alerts: Alert[];
}

export function HealthStrip() {
  const { data } = usePolling<HealthData>("/api/health", 15000);

  if (!data || data.status === "nominal") {
    return (
      <div className="health-strip health-strip-nominal">
        <span className="health-dot health-dot-green" />
        All systems nominal
      </div>
    );
  }

  const stripClass =
    data.status === "critical"
      ? "health-strip health-strip-red"
      : "health-strip health-strip-yellow";

  return (
    <div className={stripClass}>
      {data.alerts.map((alert, i) => (
        <span key={i} className={`health-alert health-alert-${alert.level}`}>
          {alert.level === "red" ? "\u25cf" : "\u25b2"} {alert.message}
        </span>
      ))}
    </div>
  );
}
