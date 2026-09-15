import { type ReactNode } from "react";
import { Checkbox } from "./Checkbox";

export interface SkillBaseCardProps {
  title: string;
  description?: string;
  descriptionMaxLines?: 2 | 3;
  /** @deprecated Cards no longer render a colored banner; kept for callers. */
  gradient?: string[];
  bannerLeadingOverlay?: ReactNode;
  bannerOverlay?: ReactNode;
  icon?: ReactNode;
  statusPills?: ReactNode;
  tags?: ReactNode;
  meta?: ReactNode;
  extraContent?: ReactNode;
  footer?: ReactNode;
  muted?: boolean;
  selected?: boolean;
  selectionMode?: boolean;
  onSelect?: () => void;
  animated?: boolean;
  animationDelay?: number;
  className?: string;
  onClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
}

export function SkillBaseCard({
  title,
  description,
  descriptionMaxLines = 2,
  bannerLeadingOverlay,
  bannerOverlay,
  icon,
  statusPills,
  tags,
  meta,
  extraContent,
  footer,
  muted = false,
  selected = false,
  selectionMode = false,
  onSelect,
  animated = false,
  animationDelay = 0,
  className = "",
  onClick,
}: SkillBaseCardProps) {
  const lineClamp = descriptionMaxLines === 3 ? "line-clamp-3" : "line-clamp-2";

  return (
    <div
      className={`scb group flex h-full flex-col overflow-hidden bg-[var(--theme-bg-card)] ${
        muted ? "scb--muted" : ""
      } ${
        selected
          ? "ring-2 ring-[var(--theme-primary)] animate-[select-glow_2s_ease-in-out]"
          : ""
      } ${animated ? "scb--animated" : ""} ${
        selectionMode && onSelect ? "cursor-pointer" : ""
      } ${className}`}
      style={animated ? { animationDelay: `${animationDelay}ms` } : undefined}
      onClick={
        selectionMode && onSelect
          ? (e) => {
              if (
                !(e.target as HTMLElement).closest("button") &&
                !(e.target as HTMLElement).closest('[role="checkbox"]')
              ) {
                onSelect();
              }
            }
          : onClick
      }
    >
      <div className="flex flex-1 flex-col p-4 sm:p-5">
        <div className="flex items-start gap-3">
          {icon && <div className="scb__icon-ring shrink-0">{icon}</div>}
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-semibold text-[var(--theme-text)] leading-tight">
              {title}
            </h3>
            {statusPills}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
            {bannerLeadingOverlay}
            {selectionMode && onSelect && (
              <div
                className={`shrink-0 transition-transform duration-200 ${
                  selected ? "scale-110" : ""
                }`}
              >
                <Checkbox
                  size="lg"
                  checked={selected}
                  onChange={() => onSelect()}
                  className="shadow-sm"
                />
              </div>
            )}
            {bannerOverlay}
          </div>
        </div>

        {description && (
          <p
            className={`mt-3 text-[13px] leading-relaxed text-[var(--theme-text-secondary)] ${lineClamp} min-h-[3.25em]`}
          >
            {description}
          </p>
        )}

        {tags && <div className="mt-3">{tags}</div>}

        {extraContent && <div className="mt-3">{extraContent}</div>}

        <div className="flex-1" />

        {meta && <div className="mt-4">{meta}</div>}

        {footer && <div className="scb__footer">{footer}</div>}
      </div>
    </div>
  );
}
