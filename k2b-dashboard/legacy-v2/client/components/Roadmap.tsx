import React from "react";
import { usePolling } from "../hooks/usePolling.js";
import { ExpandableRow } from "./ExpandableRow.js";
import { Tag } from "./Tag.js";

interface Feature {
  name: string;
  status: string;
  date: string;
  description: string;
  filePath: string;
  hasEval: boolean;
}

interface RoadmapData {
  features: Feature[];
  stats: { shipped: number; planned: number; total: number };
}

function statusVariant(status: string): "next" | "planned" | "shipped" | "default" {
  if (status === "next") return "next";
  if (status === "shipped") return "shipped";
  if (status === "planned" || status === "backlog") return "planned";
  return "default";
}

function cleanDescription(desc: string): string {
  return desc.replace(/^#+\s*/, '').replace(/^Feature:\s*/i, '').trim();
}

export function Roadmap() {
  const { data, loading } = usePolling<RoadmapData>("/api/roadmap", 60000);

  if (loading && !data) {
    return (
      <div className="panel panel-priority">
        <span className="panel-title panel-title-priority">Roadmap</span>
        <div className="text-muted">Loading...</div>
      </div>
    );
  }

  const features = data?.features ?? [];
  const stats = data?.stats ?? { shipped: 0, planned: 0 };

  return (
    <div className="panel panel-priority">
      <span className="panel-title panel-title-priority">{"\u2605"} Roadmap</span>
      <div className="panel-rows">
        {features.map((feature) => {
          const firstLine = cleanDescription(feature.description.split("\n").find(l => l.trim()) || "");
          return (
            <ExpandableRow
              key={feature.name}
              title={feature.name.replace(/^feature_/, '').replace(/-/g, ' ')}
              subtitle={firstLine}
              rightContent={
                <Tag variant={statusVariant(feature.status)}>{feature.status}</Tag>
              }
            >
              <div className="row-detail-text">
                <p>{feature.description}</p>
                <p className="text-muted">{feature.filePath}</p>
                {feature.hasEval && (
                  <span className="tag tag-shipped">eval</span>
                )}
              </div>
            </ExpandableRow>
          );
        })}
        {features.length === 0 && (
          <div className="text-muted">No features found</div>
        )}
      </div>
      <div className="panel-footer">
        <span className="text-secondary">
          {stats.shipped} shipped &middot; {stats.planned} planned
        </span>
      </div>
    </div>
  );
}
