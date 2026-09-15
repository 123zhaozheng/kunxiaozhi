import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { AgentInfo } from "../../types";
import { AgentModeIcon } from "../agent/AgentModeIcon";
import { resolveAgentDisplayName } from "../agent/agentCatalog";
import { sortAgentModes } from "../agent/agentModePresentation";

interface AgentModePillsProps {
  agents: AgentInfo[];
  currentAgent: string;
  onSelectAgent?: (id: string) => void;
}

/**
 * Segmented mode switcher: one grey track holds every mode and only the active
 * one gets a dark pill, rather than three separate bordered buttons.
 */
export const AgentModePills = memo(function AgentModePills({
  agents,
  currentAgent,
  onSelectAgent,
}: AgentModePillsProps) {
  const { i18n, t } = useTranslation();
  const modes = useMemo(() => sortAgentModes(agents), [agents]);

  if (agents.length <= 1) return null;

  return (
    <div className="welcome-mode-pills flex w-full justify-center">
      <div
        className="welcome-mode-track inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-full p-1"
        role="group"
        aria-label={t("agent.selectMode", "选择模式")}
      >
        {modes.map((agent) => {
          const isActive = agent.id === currentAgent;
          const displayName = resolveAgentDisplayName(agent, i18n.language, t);
          return (
            <button
              key={agent.id}
              type="button"
              aria-pressed={isActive}
              disabled={!onSelectAgent}
              onClick={() => onSelectAgent?.(agent.id)}
              className="welcome-mode-pill inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors duration-200 disabled:cursor-default sm:px-4 sm:py-2 sm:text-sm"
              data-active={isActive}
            >
              <AgentModeIcon agentId={agent.id} size={16} />
              <span className="whitespace-nowrap">{displayName}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
});
