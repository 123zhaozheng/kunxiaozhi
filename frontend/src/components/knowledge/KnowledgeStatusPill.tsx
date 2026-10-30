import type { ReactNode } from "react";
import type { StatusTone } from "./knowledgeUtils";

const TONE_CLASS: Record<StatusTone, string> = {
  neutral: "scb__status-pill--inactive",
  info: "scb__status-pill--published",
  warning: "scb__status-pill--published",
  success: "scb__status-pill--installed",
  danger: "scb__status-pill--danger",
};

export function KnowledgeStatusPill({
  tone,
  icon,
  title,
  children,
}: {
  tone: StatusTone;
  icon?: ReactNode;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span title={title} className={`scb__status-pill ${TONE_CLASS[tone]}`}>
      {icon}
      {children}
    </span>
  );
}
