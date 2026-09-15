import { useEffect } from "react";
import {
  Package,
  PackageX,
  Server,
  ShoppingBag,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useSettingsContext } from "../../contexts/SettingsContext";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { PanelHeader } from "../common/PanelHeader";
import { PersonaPlazaPanel } from "../persona/PersonaPlazaPanel";
import { BuiltinSkillsPanel } from "./BuiltinSkillsPanel";
import { MarketplacePanel } from "./MarketplacePanel";
import { MCPPanel } from "./MCPPanel";
import {
  resolveSkillsHubTab,
  resolveWorkspaceHubTab,
  type SkillsHubTab,
  type WorkspaceHubTab,
} from "./SkillsHubPanel/state";
import { SkillsPanel } from "./SkillsPanel";

const TAB_PATHS: Record<SkillsHubTab, string> = {
  skills: "/skills",
  marketplace: "/marketplace",
  builtin: "/builtin-skills",
};

interface SkillsHubPanelProps {
  /** Render the skills hub inside the merged workspace page. */
  embedded?: boolean;
}

function WorkspaceHubPanel() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { hasAnyPermission } = useAuth();
  const { enableSkills } = useSettingsContext();

  const canReadSkills =
    enableSkills && hasAnyPermission([Permission.SKILL_READ]);
  const canReadConnectors = hasAnyPermission([Permission.MCP_READ]);
  const requestedTab = new URLSearchParams(location.search).get("tab");
  const visibleTab = resolveWorkspaceHubTab(
    requestedTab,
    canReadSkills,
    canReadConnectors,
  );

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    let changed = false;

    if (params.get("tab") !== visibleTab) {
      params.set("tab", visibleTab);
      changed = true;
    }
    if (visibleTab !== "skills" && params.has("subtab")) {
      params.delete("subtab");
      changed = true;
    }

    if (changed) {
      navigate(
        { pathname: "/workspace", search: `?${params.toString()}` },
        { replace: true },
      );
    }
  }, [location.search, navigate, visibleTab]);

  const tabEntries: {
    key: WorkspaceHubTab;
    label: string;
    icon: typeof Package;
  }[] = [
    {
      key: "expert",
      label: t("workspaceHub.tabs.expert"),
      icon: UserRound,
    },
  ];

  if (canReadSkills) {
    tabEntries.push({
      key: "skills",
      label: t("workspaceHub.tabs.skills"),
      icon: Sparkles,
    });
  }
  if (canReadConnectors) {
    tabEntries.push({
      key: "connectors",
      label: t("workspaceHub.tabs.connectors"),
      icon: Server,
    });
  }

  const handleTabChange = (tab: WorkspaceHubTab) => {
    const params = new URLSearchParams(location.search);
    params.set("tab", tab);
    if (tab === "skills") {
      params.set("subtab", "skills");
    } else {
      params.delete("subtab");
    }
    navigate({ pathname: "/workspace", search: `?${params.toString()}` });
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center border-b border-[var(--theme-border)] px-4 py-2">
        <div
          className="inline-flex items-center gap-1 rounded-lg bg-[var(--theme-bg-subtle)] p-1"
          role="tablist"
          aria-label={t("workspaceHub.title")}
        >
          {tabEntries.map(({ key, label, icon: Icon }) => {
            const isActive = visibleTab === key;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => handleTabChange(key)}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "is-active bg-[var(--theme-sidebar-active)] text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)] hover:text-[var(--theme-text)]"
                }`}
              >
                <Icon size={16} />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {visibleTab === "expert" ? (
          <PersonaPlazaPanel />
        ) : visibleTab === "skills" ? (
          <SkillsHubPanel embedded />
        ) : (
          <MCPPanel />
        )}
      </div>
    </div>
  );
}

export function SkillsHubPanel({ embedded = false }: SkillsHubPanelProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { hasAnyPermission, hasPermission } = useAuth();
  const { enableSkills } = useSettingsContext();
  const isWorkspaceRoute = location.pathname === "/workspace";

  const canReadSkills = hasAnyPermission([Permission.SKILL_READ]);
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_READ]);
  const canManageBuiltin = hasPermission(Permission.BUILTIN_SKILL_MANAGE);

  const workspaceSubtab = new URLSearchParams(location.search).get("subtab");
  const requestedTab: SkillsHubTab = embedded
    ? workspaceSubtab === "marketplace"
      ? "marketplace"
      : workspaceSubtab === "builtin"
        ? "builtin"
        : "skills"
    : location.pathname === "/marketplace"
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

  useEffect(() => {
    if (!visibleTab || (!embedded && isWorkspaceRoute)) return;

    if (embedded) {
      const params = new URLSearchParams(location.search);
      if (
        location.pathname === "/workspace" &&
        params.get("tab") === "skills" &&
        params.get("subtab") === visibleTab
      ) {
        return;
      }
      params.set("tab", "skills");
      params.set("subtab", visibleTab);
      navigate(
        { pathname: "/workspace", search: `?${params.toString()}` },
        { replace: true },
      );
      return;
    }

    const targetPath = TAB_PATHS[visibleTab];
    if (location.pathname !== targetPath) {
      navigate(targetPath, { replace: true });
    }
  }, [embedded, isWorkspaceRoute, location, navigate, visibleTab]);

  if (!embedded && isWorkspaceRoute) {
    return <WorkspaceHubPanel />;
  }

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

  const tabEntries: {
    key: SkillsHubTab;
    label: string;
    icon: typeof Package;
  }[] = [];
  if (canReadSkills) {
    tabEntries.push({
      key: "skills",
      label: t("nav.skills"),
      icon: Package,
    });
  }
  if (canReadMarketplace) {
    tabEntries.push({
      key: "marketplace",
      label: t("nav.marketplace"),
      icon: ShoppingBag,
    });
  }
  if (canManageBuiltin) {
    tabEntries.push({
      key: "builtin",
      label: t("nav.builtin"),
      icon: ShieldCheck,
    });
  }

  const handleTabChange = (tab: SkillsHubTab) => {
    if (!embedded) {
      navigate(TAB_PATHS[tab]);
      return;
    }

    const params = new URLSearchParams(location.search);
    params.set("tab", "skills");
    params.set("subtab", tab);
    navigate({ pathname: "/workspace", search: `?${params.toString()}` });
  };

  return (
    <div className="skill-theme-shell flex h-full min-h-0 flex-col">
      <PanelHeader
        className="skill-panel-header"
        title={t("workspaceHub.title")}
        subtitle={t("workspaceHub.subtitle")}
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
                    onClick={() => handleTabChange(key)}
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
