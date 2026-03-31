import React, { type ReactNode } from "react";

interface TagProps {
  children: ReactNode;
  variant?: "next" | "planned" | "shipped" | "default";
}

export function Tag({ children, variant = "default" }: TagProps) {
  const variantClass = variant !== "default" ? ` tag-${variant}` : "";
  return <span className={`tag${variantClass}`}>{children}</span>;
}
