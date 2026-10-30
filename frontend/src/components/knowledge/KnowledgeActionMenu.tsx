import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal, type LucideIcon } from "lucide-react";

export interface ActionMenuItem {
  key: string;
  label: string;
  icon: LucideIcon;
  danger?: boolean;
  onSelect: () => void;
}

const DEFAULT_TRIGGER_CLS =
  "relative z-10 -mr-1.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]";

export function KnowledgeActionMenu({
  items,
  label,
  buttonClassName = DEFAULT_TRIGGER_CLS,
}: {
  items: ActionMenuItem[];
  label: string;
  buttonClassName?: string;
}) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (!pos) return;
    const close = () => setPos(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [pos]);

  // Keep the menu inside the viewport once its size is known.
  useLayoutEffect(() => {
    const el = menuRef.current;
    if (!pos || !el) return;
    const rect = el.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) {
      el.style.top = `${Math.max(8, window.innerHeight - rect.height - 8)}px`;
    }
  }, [pos]);

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (pos) {
      setPos(null);
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPos({ top: rect.bottom + 4, left: Math.max(8, rect.right - 144) });
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={!!pos}
        className={buttonClassName}
      >
        <MoreHorizontal size={16} />
      </button>
      {pos &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            style={{ top: pos.top, left: pos.left }}
            onClick={(e) => e.stopPropagation()}
            className="fixed z-[999] w-36 overflow-hidden rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] py-1 shadow-lg"
          >
            {items.map(({ key, label: itemLabel, icon: Icon, danger, onSelect }) => (
              <button
                key={key}
                type="button"
                role="menuitem"
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-[var(--theme-sidebar-hover)] ${
                  danger ? "text-red-600 dark:text-red-400" : "text-[var(--theme-text)]"
                }`}
                onClick={() => {
                  setPos(null);
                  onSelect();
                }}
              >
                <Icon size={14} />
                {itemLabel}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
