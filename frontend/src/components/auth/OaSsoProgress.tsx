/**
 * Staged OA login progress — orbit ring + step trail (not a linear progress bar).
 */

import { useTranslation } from "react-i18next";
import { Check } from "lucide-react";

export type OaSsoProgressStepId =
  | "secure"
  | "verify"
  | "account"
  | "enter";

const STEP_ORDER: OaSsoProgressStepId[] = [
  "secure",
  "verify",
  "account",
  "enter",
];

export interface OaSsoProgressProps {
  /** 0..3 while loading; 4 = all done (success flash) */
  activeIndex: number;
  className?: string;
}

export function OaSsoProgress({ activeIndex, className = "" }: OaSsoProgressProps) {
  const { t } = useTranslation();
  const labels: Record<OaSsoProgressStepId, string> = {
    secure: t("auth.oaSso.stepSecure"),
    verify: t("auth.oaSso.stepVerify"),
    account: t("auth.oaSso.stepAccount"),
    enter: t("auth.oaSso.stepEnter"),
  };

  const ringProgress = Math.min(1, (activeIndex + 0.35) / STEP_ORDER.length);

  return (
    <div
      className={`oa-sso-progress flex flex-col items-center ${className}`}
      aria-live="polite"
      aria-busy={activeIndex < STEP_ORDER.length}
    >
      <div className="oa-sso-orbit relative mb-8 h-28 w-28 sm:h-32 sm:w-32">
        <svg
          className="absolute inset-0 h-full w-full -rotate-90"
          viewBox="0 0 100 100"
          aria-hidden
        >
          <circle
            cx="50"
            cy="50"
            r="42"
            fill="none"
            className="stroke-stone-200/80 dark:stroke-stone-700/80"
            strokeWidth="3"
          />
          <circle
            cx="50"
            cy="50"
            r="42"
            fill="none"
            className="oa-sso-ring-stroke stroke-teal-500/90 dark:stroke-teal-400/90"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={`${ringProgress * 264} 264`}
          />
        </svg>
        <div className="oa-sso-orbit-spinner absolute inset-0">
          <div className="oa-sso-orbit-dot" />
        </div>
        <div className="absolute inset-0 flex items-center justify-center">
          <img
            src="/images/lamb.webp"
            alt=""
            className="h-12 w-12 object-contain opacity-90 sm:h-14 sm:w-14"
          />
        </div>
      </div>

      <ul className="w-full max-w-[16rem] space-y-2 sm:max-w-xs">
        {STEP_ORDER.map((id, index) => {
          const done = activeIndex > index;
          const current = activeIndex === index;
          return (
            <li
              key={id}
              className={`oa-sso-step flex items-center gap-3 rounded-xl px-3 py-2 text-left text-sm transition-all duration-500 ${
                current
                  ? "bg-teal-500/10 text-teal-800 dark:bg-teal-500/15 dark:text-teal-100"
                  : done
                    ? "text-stone-500 dark:text-stone-400"
                    : "text-stone-400/70 dark:text-stone-600"
              }`}
            >
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold transition-colors ${
                  done
                    ? "border-teal-500/40 bg-teal-500/15 text-teal-600 dark:text-teal-300"
                    : current
                      ? "border-teal-500/50 text-teal-600 dark:text-teal-300"
                      : "border-stone-200 dark:border-stone-700"
                }`}
              >
                {done ? <Check size={14} strokeWidth={2.5} /> : index + 1}
              </span>
              <span className={current ? "font-medium" : ""}>{labels[id]}</span>
              {current && (
                <span className="oa-sso-step-shimmer ml-auto h-1.5 w-8 rounded-full bg-teal-400/40" />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}