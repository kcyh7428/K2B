import React from "react";

interface StatusDotProps {
  status: "online" | "warning" | "error" | "offline";
}

export function StatusDot({ status }: StatusDotProps) {
  return <span className={`status-dot ${status}`} />;
}
