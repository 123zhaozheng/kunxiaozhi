import { useEffect } from "react";
import { Package, PackageX, ShoppingBag, Sparkles, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useSettingsContext } from "../../contexts/SettingsContext";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { PanelHeader } from "../common/PanelHeader";
import { MarketplacePanel } from "./MarketplacePanel";
import { SkillsPanel } from "./SkillsPanel";
import { BuiltinSkillsPanel } from "./BuiltinSkillsPanel";
import { resolveSkillsHubTab, type SkillsHubTab } from "./SkillsHubPanel/state";

const TAB_PATHS: Record<SkillsHubTab, string> = {
  skills: "/skills",
  marketplace: "/marketplace",
  builtin: "/builtin-skills",
};

export function SkillsHubPanel() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { hasAnyPermission, hasPermission } = useAuth();
  const { enableSkills } = useSettingsContext();

  const canReadSkills = hasAnyPermission([Permission.SKILL_READ]);
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_READ]);
  const canManageBuiltin = hasPermission(Permission.BUILTIN_SKILL_MANAGE);

  const requestedTab: SkillsHubTab =
    location.pathname === "/marketplace"
      ? "marketplace"
      : location.pathname === "/builtin-skills"
        ? "builtin"
        : "skills";

  // Builtin tab is admin-gated; fall back to the skills/marketplace resolution.
  const skillsMarketplaceTab = resolveSkillsHubTab(
    requestedTab === "builtin" ? undefined : requestedTab,
    canReadSkills,
    canReadMarketplace,
  );

  const visibleTab: SkillsHubTab | null =
    requestedTab === "builtin" && canManageBuiltin
      ? "builtin"
      : skillsMarketplaceTab;

  const showTabSwitcher = canReadSkills && canReadMarketplace;

  useEffect(() => {
    if (!visibleTab) return;
    const targetPath = TAB_PATHS[visibleTab];
    if (location.pathname !== targetPath) {
      navigate(targetPath, { replace: true });
    }
  }, [location.pathname, navigate, visibleTab]);

  if (!enableSkills) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-stone-500 dark:text-stone-400">
        <PackageX
          size={48}
          className="mb-3 text-stone-300 dark:text-stone-600"
        />
        <p className="text-center">{t("skills.featureDisabled")}</p>
      </div>
    );
  }

  if (!visibleTab) {
    return (
      <div className="flex h-full items-center justify-center text-stone-500 dark:text-stone-400">
        {t("skills.noPermission")}
      </div>
    );
  }

  // Build the tab switcher entries (skills/marketplace when both readable,
  // builtin when admin).
  const tabEntries: {
    key: SkillsHubTab;
    label: string;
    icon: typeof Package;
  }[] = [];
  if (showTabSwitcher) {
    tabEntries.push(
      {
        key: "skills",
        label: t("nav.skills"),
        icon: Package,
      },
      {
        key: "marketplace",
        label: t("nav.marketplace"),
        icon: ShoppingBag,
      },
    );
  }
  if (canManageBuiltin) {
    tabEntries.push({
      key: "builtin",
      label: t("nav.builtin"),
      icon: ShieldCheck,
    });
  }

  return (
    <div className="skill-theme-shell flex h-full min-h-0 flex-col">
      <PanelHeader
        className="skill-panel-header"
        title={t("skillsHub.title")}
        subtitle={t("skillsHub.subtitle")}
        icon={
          <Sparkles size={20} className="text-stone-600 dark:text-stone-400" />
        }
        actions={
          tabEntries.length > 0 ? (
            <div className="inline-flex rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-1">
              {tabEntries.map(({ key, label, icon: Icon }) => {
                const isActive = visibleTab === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => navigate(TAB_PATHS[key])}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-all ${
                      isActive
                        ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)] shadow-sm"
                        : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
                    }`}
                    aria-pressed={isActive}
                  >
                    <Icon size={16} />
                    <span className="hidden sm:inline">{label}</span>
                  </button>
                );
              })}
            </div>
          ) : undefined
        }
      />

      {/* Child panel handles its own padding via skill-panel-header + skill-content-area */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {visibleTab === "skills" ? (
          <SkillsPanel embedded />
        ) : visibleTab === "marketplace" ? (
          <MarketplacePanel embedded />
        ) : (
          <BuiltinSkillsPanel embedded />
        )}
      </div>
    </div>
  );
}
