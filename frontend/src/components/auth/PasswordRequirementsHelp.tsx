import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { CircleHelp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { calculatePasswordRequirementsPosition } from "./passwordRequirementsPosition";

const SM_MIN_WIDTH_PX = 640;

export interface PasswordRequirementsHelpProps {
  context?: "registration" | "reset" | "forced" | "profile" | "admin";
  className?: string;
}

export function PasswordRequirementsHelp({
  context,
  className,
}: PasswordRequirementsHelpProps) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [isNarrow, setIsNarrow] = useState(
    () => typeof window !== "undefined" && window.innerWidth < SM_MIN_WIDTH_PX,
  );
  const [position, setPosition] = useState<ReturnType<
    typeof calculatePasswordRequirementsPosition
  > | null>(null);
  const id = useId().replace(/:/g, "");
  const panelId = `password-requirements-${id}`;
  const titleId = `${panelId}-title`;

  useEffect(() => {
    const handleResize = () => setIsNarrow(window.innerWidth < SM_MIN_WIDTH_PX);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        !wrapperRef.current?.contains(target) &&
        !panelRef.current?.contains(target)
      ) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || isNarrow || !wrapperRef.current) return;
    const updatePosition = () => {
      const anchor = wrapperRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const panel = panelRef.current?.getBoundingClientRect();
      setPosition(
        calculatePasswordRequirementsPosition(anchor, {
          width: window.innerWidth,
          height: window.innerHeight,
        }, {
          width: panel?.width || 320,
          height: panel?.height || 260,
        }),
      );
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, isNarrow]);

  const toggle = () => {
    if (open) {
      setOpen(false);
      triggerRef.current?.focus();
      return;
    }
    setOpen(true);
  };

  const requirements = [
    t("auth.passwordRequirements.length"),
    t("auth.passwordRequirements.characters"),
    t("auth.passwordRequirements.composition"),
    t("auth.passwordRequirements.identifiers"),
    ...(context === "profile"
      ? [t("auth.passwordRequirements.currentPassword")]
      : []),
    t("auth.passwordRequirements.strength"),
  ];

  const panel = (
    <div
      ref={panelRef}
      id={panelId}
      role="dialog"
      aria-labelledby={titleId}
      className="max-h-[min(70vh,18rem)] overflow-y-auto rounded-lg border border-stone-200 bg-white p-3 text-left shadow-xl dark:border-stone-700 dark:bg-stone-900"
      style={
        isNarrow
          ? undefined
          : {
              position: "fixed",
              left: position?.left ?? 0,
              top: position?.top ?? 0,
              width: position?.width ?? "min(20rem, calc(100vw - 24px))",
              visibility: position ? "visible" : "hidden",
              zIndex: 9999,
            }
      }
    >
      <h2
        id={titleId}
        className="mb-2 text-xs font-semibold text-stone-900 dark:text-stone-100"
      >
        {t("auth.passwordRequirements.title")}
      </h2>
      <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-stone-600 dark:text-stone-300">
        {requirements.map((requirement) => (
          <li key={requirement}>{requirement}</li>
        ))}
      </ul>
    </div>
  );

  return (
    <>
      <span ref={wrapperRef} className={`relative inline-flex ${className ?? ""}`}>
        <button
          ref={triggerRef}
          type="button"
          aria-label={t(
            open
              ? "auth.passwordRequirements.close"
              : "auth.passwordRequirements.open",
          )}
          aria-expanded={open}
          aria-controls={panelId}
          aria-haspopup="dialog"
          onClick={toggle}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full text-stone-400 transition-colors hover:text-stone-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60 dark:text-stone-500 dark:hover:text-stone-200"
        >
          <CircleHelp size={16} aria-hidden="true" />
        </button>
      </span>
      {open && isNarrow && (
        <div className="basis-full pt-1">
          <div className="w-[min(20rem,calc(100vw-24px))]">{panel}</div>
        </div>
      )}
      {open && !isNarrow && createPortal(panel, document.body)}
    </>
  );
}
