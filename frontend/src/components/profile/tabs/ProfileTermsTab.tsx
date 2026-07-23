import { useTranslation } from "react-i18next";

export function ProfileTermsTab() {
  const { t } = useTranslation();

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-stone-800 dark:text-stone-100">
        {t("profile.termsTitle")}
      </h3>

      <div className="p-3 rounded-lg bg-amber-50/50 dark:bg-amber-500/[0.04]">
        <span className="text-xs leading-relaxed text-stone-600 dark:text-stone-300">
          {t("profile.termsItem1")}
        </span>
      </div>

      <div className="space-y-2">
        {(["termsItem2", "termsItem3"] as const).map((key) => (
          <div
            key={key}
            className="p-2.5 rounded-lg bg-stone-50/60 dark:bg-stone-800/40"
          >
            <span className="text-xs leading-relaxed text-stone-600 dark:text-stone-300">
              {t(`profile.${key}`)}
            </span>
          </div>
        ))}
      </div>

      <p className="text-[10px] text-stone-400 dark:text-stone-500 text-center pt-1">
        {t("auth.termsHint")}
      </p>
    </div>
  );
}
