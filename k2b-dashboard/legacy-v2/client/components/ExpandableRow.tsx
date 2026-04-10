import React, { useState, type ReactNode } from "react";

interface ExpandableRowProps {
  title: string;
  subtitle?: string;
  rightContent?: ReactNode;
  children?: ReactNode;
  defaultExpanded?: boolean;
}

export function ExpandableRow({
  title,
  subtitle,
  rightContent,
  children,
  defaultExpanded = false,
}: ExpandableRowProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div
      className={`expandable-row${expanded ? " expanded" : ""}`}
      onClick={() => setExpanded(!expanded)}
    >
      <div className="row-header">
        <div className="row-title-area">
          <div className="row-title">{title}</div>
          {subtitle && <div className="row-subtitle">{subtitle}</div>}
        </div>
        <div className="row-right">
          {rightContent}
          <span className="chevron">&#9654;</span>
        </div>
      </div>
      {children && (
        <div className="row-detail" onClick={(e) => e.stopPropagation()}>
          {children}
        </div>
      )}
    </div>
  );
}
