import { useState } from "react";
import { ChevronDown, RadioTower } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Project } from "../../../types";
import { ProjectItem } from "../../sidebar/ProjectItem";
import type { UnreadBySession } from "../../sidebar/unreadCounts";
import type { ProjectActions, SessionActions } from "./SessionListContent";

interface WeComChannelGroupProps {
  projects: Project[];
  channelProjects: Project[];
  currentSessionId: string | null;
  unreadBySession: UnreadBySession;
  sessionActions: SessionActions;
  projectActions: ProjectActions;
  scrollRoot: Element | null;
  autoExpandProjectId: string | null | undefined;
  onConsumeAutoExpandProjectId: (id: string) => void;
}

export function WeComChannelGroup({
  projects,
  channelProjects,
  currentSessionId,
  unreadBySession,
  sessionActions,
  projectActions,
  scrollRoot,
  autoExpandProjectId,
  onConsumeAutoExpandProjectId,
}: WeComChannelGroupProps) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(true);

  if (channelProjects.length === 0) {
    return null;
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => setIsExpanded((value) => !value)}
        className="sidebar-nav-btn flex h-8 w-full items-center gap-3 rounded-[10px] px-[9px] transition-colors"
        aria-expanded={isExpanded}
      >
        <RadioTower
          size={20}
          className="shrink-0 text-[var(--theme-text-secondary)]"
        />
        <span className="min-w-0 flex-1 truncate text-left text-[13px]">
          {t("sidebar.wecomChannel")}
        </span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-stone-300 transition-transform duration-200 dark:text-stone-600 ${
            isExpanded ? "" : "-rotate-90"
          }`}
        />
      </button>

      {isExpanded && (
        <div className="ml-3 mt-0.5 flex flex-col gap-px">
          {channelProjects
            .slice()
            .sort((a, b) => a.sort_order - b.sort_order)
            .map((project) => (
              <ProjectItem
                key={project.id}
                ref={(element) =>
                  projectActions.onSetProjectRef(project.id, element)
                }
                project={project}
                currentSessionId={currentSessionId}
                allProjects={projects}
                onSelectSession={sessionActions.onSelectSession}
                onDeleteSession={sessionActions.onDeleteSession}
                onMoveSession={sessionActions.onMoveSession}
                onToggleFavorite={sessionActions.onToggleFavorite}
                onShareSession={sessionActions.onShareSession}
                onRenameProject={projectActions.onRenameProject}
                onDeleteProject={projectActions.onDeleteProject}
                onUpdateIcon={projectActions.onUpdateIcon}
                scrollRoot={scrollRoot}
                draggingSessionId={null}
                forceExpandProjectId={autoExpandProjectId}
                onConsumeAutoExpand={onConsumeAutoExpandProjectId}
                unreadBySession={unreadBySession}
              />
            ))}
        </div>
      )}
    </div>
  );
}
