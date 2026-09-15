import { useState, useRef, useEffect, useCallback, useMemo, memo } from "react";
import toast from "react-hot-toast";
import { Ban, Sparkles, Target, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ImageViewer } from "../common";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { isSandboxCapacityError } from "../../services/api/fetch";
import { ContactAdminDialog } from "../common/ContactAdminDialog";
import { SandboxCapacityDialog } from "./SandboxCapacityDialog";
import { useFileUpload } from "../../hooks/useFileUpload";
import { useMentionState } from "../../hooks/useMentionState";
import { useMentionSearch } from "../../hooks/useMentionSearch";
import { useTeamMentionSearch } from "../../hooks/useTeamMentionSearch";
import { useInputHistory } from "../../hooks/useInputHistory";
import { useTextareaResize } from "../../hooks/useTextareaResize";
import { usePasteHandler } from "../../hooks/usePasteHandler";
import { useAuth } from "../../hooks/useAuth";
import { MentionPopup } from "./MentionPopup";
import { TeamMentionPopup } from "./TeamMentionPopup";
import { ActiveGoalBar } from "./ActiveGoalBar";
import { ChatInputToolbar } from "./ChatInputToolbar";
import { ChatInputSelectors } from "./ChatInputSelectors";
import { ChatInputHelpMenu } from "./ChatInputHelpMenu";
import { ChatInputAttachments } from "./ChatInputAttachments";
import { getMentionPopupFixedPlacement } from "./chatInputViewport";
import { FILE_CATEGORY_PERMISSIONS } from "./chatInputConstants";
import {
  applySlashCommandSelection,
  getMatchingSlashItems,
  getSlashCommandQuery,
  stripSlashCommandQuery,
  type ChatInputSlashCommand,
  type ChatInputSlashListItem,
} from "./chatInputSlashCommands";
import { buildEmphasizedUserMessage } from "./chatInputSkillEmphasis";
import {
  consumePendingSelectionActionPrompt,
  SELECTION_ACTION_EVENT,
  type SelectionActionEventDetail,
} from "../common/selectionActionPopover";
import type { ChatInputProps } from "./chatInputTypes";
import type { FeaturePanel } from "../selectors/FeatureMenu";
import type { MessageAttachment, PersonaPreset } from "../../types";
import type { Team } from "../../types/team";

export type { ChatInputProps } from "./chatInputTypes";

export const ChatInput = memo(function ChatInput({
  onSend,
  onStop,
  isLoading,
  disabled,
  canSend = true,
  tools = [],
  onToggleTool,
  onToggleCategory,
  onToggleAll,
  toolsLoading: _toolsLoading,
  enabledToolsCount = 0,
  totalToolsCount = 0,
  skills = [],
  onToggleSkill,
  onToggleSkillCategory,
  onToggleAllSkills,
  skillsLoading: _skillsLoading,
  pendingSkillNames = [],
  skillsMutating = false,
  enabledSkillsCount = 0,
  totalSkillsCount = 0,
  enableSkills = true,
  personaPresets = [],
  personaPresetsTotal,
  personaPresetsPage,
  onPersonaPresetsPageChange,
  onPersonaPresetsSearchChange,
  onPersonaPresetsTagChange,
  selectedPersonaPresetId,
  selectedPersonaName,
  personaSkillsControlled = false,
  personaPresetsLoading = false,
  personaPresetsMutating = false,
  onUsePersonaPreset,
  onCopyPersonaPreset,
  onClearPersonaPreset,
  canManagePersonaPresets = false,
  agentOptions,
  agentOptionValues = {},
  onToggleAgentOption,
  availableModels,
  currentModelId,
  onSelectModel,
  agents = [],
  currentAgent,
  onSelectAgent,
  selectedTeamId,
  onSelectTeam,
  onOpenTeamBuilder,
  attachments: externalAttachments,
  onAttachmentsChange: externalOnAttachmentsChange,
  onMentionQueryChange,
  emphasizedSkillNames = [],
  onEmphasizeSkill,
  onRemoveEmphasizedSkill,
  onClearEmphasizedSkills,
  onRestoreEmphasizedSkills,
  pendingInput,
  onPendingInputConsumed,
  className,
  activeGoal,
  onClearActiveGoal,
  goalLabel,
  goalDurationLabel,
  goalClearLabel,
  showHelpMenu,
  helpMenuClassName,
}: ChatInputProps) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");

  // Consume external pendingInput: fill textarea and focus
  useEffect(() => {
    if (pendingInput) {
      setInput(pendingInput);
      onPendingInputConsumed?.();
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (textarea) {
          textarea.focus();
          textarea.selectionStart = textarea.selectionEnd = pendingInput.length;
        }
      });
    }
  }, [pendingInput, onPendingInputConsumed]);

  const [activePanel, setActivePanel] = useState<FeaturePanel>(null);
  const [internalAttachments, setInternalAttachments] = useState<
    MessageAttachment[]
  >([]);
  const [imageViewerSrc, setImageViewerSrc] = useState<string | null>(null);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [contactAdminOpen, setContactAdminOpen] = useState(false);
  const [capacityModalOpen, setCapacityModalOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [slashHighlightIndex, setSlashHighlightIndex] = useState(0);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const slashMenuRef = useRef<HTMLDivElement>(null);
  const previousAgentRef = useRef(currentAgent);
  const [cursorPosition, setCursorPosition] = useState(0);
  const [slashMenuDismissed, setSlashMenuDismissed] = useState(false);
  const [mentionPopupPlacement, setMentionPopupPlacement] =
    useState<ReturnType<typeof getMentionPopupFixedPlacement>>(null);
  const { hasPermission } = useAuth();

  const uploadCategories = (
    Object.keys(FILE_CATEGORY_PERMISSIONS) as Array<
      keyof typeof FILE_CATEGORY_PERMISSIONS
    >
  ).filter((cat) => hasPermission(FILE_CATEGORY_PERMISSIONS[cat]));

  const attachments = externalAttachments ?? internalAttachments;
  const setAttachments = externalOnAttachmentsChange ?? setInternalAttachments;

  const {
    uploadFiles,
    uploadLimits,
    validateCount,
    cancelUpload,
    retryUpload,
  } =
    useFileUpload({
      attachments,
      onAttachmentsChange: setAttachments,
    });

  const { history, pushHistory, navigateUp, navigateDown } = useInputHistory();

  const { scheduleTextareaResize } = useTextareaResize(textareaRef, input);

  const { handlePaste } = usePasteHandler({
    textareaRef,
    input,
    setInput,
    uploadFiles,
    validateCount,
    scheduleTextareaResize,
  });

  const mentionMode = currentAgent === "team" ? "team" : "persona";
  const requiresTeamSelection = currentAgent === "team" && !selectedTeamId;
  const mentionEnabled =
    mentionMode === "team" ? !!onSelectTeam : !!onUsePersonaPreset;

  useEffect(() => {
    const enteredTeamMode =
      currentAgent === "team" && previousAgentRef.current !== "team";
    previousAgentRef.current = currentAgent;
    if (enteredTeamMode && !selectedTeamId && onSelectTeam) {
      setActivePanel("team");
    }
  }, [currentAgent, onSelectTeam, selectedTeamId]);

  const {
    mention,
    moveHighlight: moveMentionHighlight,
    setHighlightedIndex: setMentionHighlight,
    setResultCount: setMentionResultCount,
    resetMention,
    dismissMention,
  } = useMentionState(input, cursorPosition, mentionEnabled);

  const mentionSearch = useMentionSearch(
    mention.query,
    mention.isActive && mentionMode === "persona",
  );
  const teamMentionSearch = useTeamMentionSearch(
    mention.query,
    mention.isActive && mentionMode === "team",
  );

  useEffect(() => {
    if (mention.isActive) {
      setMentionResultCount(
        mentionMode === "team"
          ? teamMentionSearch.teams.length
          : mentionSearch.presets.length,
      );
    }
  }, [
    mention.isActive,
    mentionMode,
    mentionSearch.presets.length,
    teamMentionSearch.teams.length,
    setMentionResultCount,
  ]);

  useEffect(() => {
    if (!onMentionQueryChange) return;
    onMentionQueryChange(mention.isActive ? mention.query : null);
  }, [mention.isActive, mention.query, onMentionQueryChange]);

  useEffect(() => {
    if (!onMentionQueryChange || !selectedPersonaPresetId || !mention.isActive)
      return;
    const before = input.substring(0, mention.atIndex);
    const after = input.substring(mention.atIndex + mention.query.length + 1);
    setInput(before + after);
    setCursorPosition(before.length || 0);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (textarea) {
        textarea.selectionStart = textarea.selectionEnd = before.length;
        textarea.focus();
        scheduleTextareaResize();
      }
    });
    resetMention();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires only on preset selection
  }, [selectedPersonaPresetId]);

  useEffect(() => {
    if (!onMentionQueryChange || !selectedTeamId || !mention.isActive) return;
    const before = input.substring(0, mention.atIndex);
    const after = input.substring(mention.atIndex + mention.query.length + 1);
    setInput(before + after);
    setCursorPosition(before.length || 0);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (textarea) {
        textarea.selectionStart = textarea.selectionEnd = before.length;
        textarea.focus();
        scheduleTextareaResize();
      }
    });
    resetMention();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires only on team selection
  }, [selectedTeamId]);

  useEffect(() => {
    const applySelectionActionPrompt = (prompt: string) => {
      setInput((previous) => {
        const next = previous.trim()
          ? `${previous.trim()}\n\n${prompt}`
          : prompt;
        setCursorPosition(next.length);
        requestAnimationFrame(() => {
          const textarea = textareaRef.current;
          if (!textarea) return;
          textarea.focus();
          textarea.selectionStart = textarea.selectionEnd = next.length;
          scheduleTextareaResize();
        });
        return next;
      });
    };

    const pendingPrompt = consumePendingSelectionActionPrompt();
    if (pendingPrompt) {
      applySelectionActionPrompt(pendingPrompt);
    }

    const handleSelectionAction = (event: Event) => {
      const detail = (event as CustomEvent<SelectionActionEventDetail>).detail;
      if (!detail?.prompt) return;
      applySelectionActionPrompt(detail.prompt);
    };

    window.addEventListener(SELECTION_ACTION_EVENT, handleSelectionAction);
    return () => {
      window.removeEventListener(SELECTION_ACTION_EVENT, handleSelectionAction);
    };
  }, [scheduleTextareaResize]);

  // Ctrl+T / Cmd+T -> open team picker
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac =
        typeof navigator !== "undefined" &&
        navigator.platform.toUpperCase().indexOf("MAC") >= 0;
      const modifier = isMac ? e.metaKey : e.ctrlKey;
      if (modifier && e.key === "t") {
        e.preventDefault();
        if (currentAgent === "team" && onSelectTeam) {
          setActivePanel((prev) => (prev === "team" ? null : "team"));
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [currentAgent, onSelectTeam]);

  useEffect(() => {
    if (!mention.isActive) {
      setMentionPopupPlacement(null);
      return;
    }

    const updateMentionPopupPlacement = () => {
      const container = containerRef.current;
      setMentionPopupPlacement(
        getMentionPopupFixedPlacement({
          inputRect: container?.getBoundingClientRect() ?? null,
          viewportHeight: window.visualViewport?.height ?? window.innerHeight,
        }),
      );
    };

    updateMentionPopupPlacement();
    window.addEventListener("resize", updateMentionPopupPlacement);
    window.addEventListener("scroll", updateMentionPopupPlacement, true);
    window.visualViewport?.addEventListener(
      "resize",
      updateMentionPopupPlacement,
    );
    window.visualViewport?.addEventListener(
      "scroll",
      updateMentionPopupPlacement,
    );
    return () => {
      window.removeEventListener("resize", updateMentionPopupPlacement);
      window.removeEventListener("scroll", updateMentionPopupPlacement, true);
      window.visualViewport?.removeEventListener(
        "resize",
        updateMentionPopupPlacement,
      );
      window.visualViewport?.removeEventListener(
        "scroll",
        updateMentionPopupPlacement,
      );
    };
  }, [mention.isActive]);

  const personaAvatar = useMemo(() => {
    if (!selectedPersonaPresetId) return null;
    const preset = personaPresets.find((p) => p.id === selectedPersonaPresetId);
    if (!preset) return null;
    return {
      avatar: preset.avatar ?? undefined,
      primaryTag: preset.tags[0] || "",
    };
  }, [selectedPersonaPresetId, personaPresets]);

  const slashItems = useMemo(
    () => getMatchingSlashItems(input, cursorPosition, skills),
    [input, cursorPosition, skills],
  );
  const slashQuery = getSlashCommandQuery(input, cursorPosition);
  const slashCommandOpen = slashQuery !== null && !slashMenuDismissed;

  useEffect(() => {
    setSlashHighlightIndex(0);
  }, [slashQuery, slashItems.length]);

  useEffect(() => {
    setSlashMenuDismissed(false);
  }, [slashQuery]);

  useEffect(() => {
    if (!slashCommandOpen) return;
    const handleMouseDown = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (slashMenuRef.current?.contains(target)) return;
      if (textareaRef.current?.contains(target)) return;
      setSlashMenuDismissed(true);
    };
    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, [slashCommandOpen]);

  const applySlashCommand = useCallback(
    (command: ChatInputSlashCommand) => {
      const next = applySlashCommandSelection(input, cursorPosition, command);
      setInput(next.input);
      setCursorPosition(next.cursorPosition);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        textarea.focus();
        textarea.selectionStart = textarea.selectionEnd = next.cursorPosition;
        scheduleTextareaResize();
      });
    },
    [cursorPosition, input, scheduleTextareaResize],
  );

  const applySlashSkill = useCallback(
    (skillName: string) => {
      if (!emphasizedSkillNames.includes(skillName)) {
        onEmphasizeSkill?.(skillName);
      }
      const next = stripSlashCommandQuery(input, cursorPosition);
      setInput(next.input);
      setCursorPosition(next.cursorPosition);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        textarea.focus();
        textarea.selectionStart = textarea.selectionEnd = next.cursorPosition;
        scheduleTextareaResize();
      });
    },
    [
      cursorPosition,
      emphasizedSkillNames,
      input,
      onEmphasizeSkill,
      scheduleTextareaResize,
    ],
  );

  const applySlashItem = useCallback(
    (item: ChatInputSlashListItem) => {
      if (item.kind === "command") {
        applySlashCommand(item.command);
        return;
      }
      applySlashSkill(item.name);
    },
    [applySlashCommand, applySlashSkill],
  );

  const applyMentionSelection = useCallback(
    (preset: PersonaPreset) => {
      if (!mention.isActive) return;
      const before = input.substring(0, mention.atIndex);
      const after = input.substring(mention.atIndex + mention.query.length + 1);
      const newInput = before + after;
      setInput(newInput);
      setCursorPosition(before.length || 0);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (textarea) {
          textarea.selectionStart = textarea.selectionEnd = before.length;
          textarea.focus();
          scheduleTextareaResize();
        }
      });
      onUsePersonaPreset?.(preset);
      resetMention();
    },
    [input, mention, onUsePersonaPreset, resetMention, scheduleTextareaResize],
  );

  const applyTeamMentionSelection = useCallback(
    (team: Team) => {
      if (!mention.isActive) return;
      const before = input.substring(0, mention.atIndex);
      const after = input.substring(mention.atIndex + mention.query.length + 1);
      const newInput = before + after;
      setInput(newInput);
      setCursorPosition(before.length || 0);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (textarea) {
          textarea.selectionStart = textarea.selectionEnd = before.length;
          textarea.focus();
          scheduleTextareaResize();
        }
      });
      onSelectTeam?.(team.id);
      resetMention();
    },
    [input, mention, onSelectTeam, resetMention, scheduleTextareaResize],
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSend || requiresTeamSelection) return;
    if (input.trim() && canSubmit) {
      const draft = input;
      const trimmed = input.trim();
      const draftAttachments = [...attachments];
      const draftSkills = [...emphasizedSkillNames];
      let accepted = false;
      const handleAccepted = () => {
        if (accepted) return;
        accepted = true;
        pushHistory(trimmed);
        setInput("");
        setAttachments([]);
        onClearEmphasizedSkills?.();
        requestAnimationFrame(() => {
          if (textareaRef.current) {
            textareaRef.current.style.height = "auto";
          }
        });
      };

      setIsSubmitting(true);
      try {
        await onSend(
          buildEmphasizedUserMessage(
            trimmed,
            emphasizedSkillNames,
            (names) =>
              t("chat.skillEmphasis.mustUse", {
                names,
                defaultValue: "请必须使用{{names}}技能。",
              }),
          ),
          agentOptionValues,
          draftAttachments,
          handleAccepted,
        );
        // Preserve compatibility with callers that do not expose a separate
        // admission boundary.
        handleAccepted();
      } catch (error) {
        if (isSandboxCapacityError(error)) {
          // Restore the exact submission even if a later stage reported the
          // typed admission error after accepting the input callback.
          setInput(draft);
          setAttachments(draftAttachments);
          onRestoreEmphasizedSkills?.(draftSkills);
          setCapacityModalOpen(true);
          requestAnimationFrame(() => {
            const textarea = textareaRef.current;
            if (!textarea) return;
            textarea.focus();
            textarea.selectionStart = textarea.selectionEnd = draft.length;
            scheduleTextareaResize();
          });
        }
      } finally {
        setIsSubmitting(false);
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    const isComposing =
      e.nativeEvent.isComposing || e.key === "Process" || e.keyCode === 229;
    if (isComposing) return;

    if (slashCommandOpen) {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (slashItems.length > 0) {
          setSlashHighlightIndex(
            (index) => (index - 1 + slashItems.length) % slashItems.length,
          );
        }
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (slashItems.length > 0) {
          setSlashHighlightIndex((index) => (index + 1) % slashItems.length);
        }
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        if (slashItems.length > 0) {
          const item = slashItems[slashHighlightIndex] ?? slashItems[0];
          if (item) applySlashItem(item);
        }
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setInput("");
        setCursorPosition(0);
        return;
      }
    }

    if (mention.isActive) {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        moveMentionHighlight("up");
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        moveMentionHighlight("down");
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        if (mentionMode === "team") {
          const highlighted = teamMentionSearch.teams[mention.highlightedIndex];
          if (highlighted) applyTeamMentionSelection(highlighted);
        } else {
          const highlighted = mentionSearch.presets[mention.highlightedIndex];
          if (highlighted) applyMentionSelection(highlighted);
        }
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        resetMention();
        return;
      }
    }

    const newlineModifier = localStorage.getItem("newlineModifier") || "shift";

    if (e.key === "Enter") {
      const needsModifier = newlineModifier === "ctrl" ? e.ctrlKey : e.shiftKey;
      if (needsModifier) return;

      e.preventDefault();
      if (isLoading) {
        setStopConfirmOpen(true);
      } else {
        handleSubmit(e);
      }
      return;
    }

    const textarea = textareaRef.current;
    const atTop =
      textarea?.selectionStart === 0 && textarea?.selectionEnd === 0;
    const value = textarea?.value ?? "";
    const atBottom =
      textarea?.selectionStart === value.length &&
      textarea?.selectionEnd === value.length;

    if (e.key === "ArrowUp" && atTop) {
      e.preventDefault();
      const prev = navigateUp(input);
      if (prev !== null) {
        setInput(prev);
        requestAnimationFrame(() => {
          if (textarea) {
            textarea.selectionStart = textarea.selectionEnd = prev.length;
          }
        });
      }
    } else if (e.key === "ArrowDown" && (atBottom || history.length > 0)) {
      e.preventDefault();
      const next = navigateDown();
      if (next !== null) {
        setInput(next);
        requestAnimationFrame(() => {
          if (textarea) {
            textarea.selectionStart = textarea.selectionEnd =
              textarea.value.length;
          }
        });
      }
    }
  };

  const hasContent = !!input.trim() && !disabled;
  const hasUploadingAttachment = attachments.some((a) => a.isUploading);
  const hasFailedAttachment = attachments.some((a) => !!a.uploadError);
  const canSubmit =
    hasContent &&
    canSend &&
    !isLoading &&
    !isSubmitting &&
    !hasUploadingAttachment &&
    !hasFailedAttachment &&
    !requiresTeamSelection;

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(false);
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    if (!validateCount(files.length)) return;
    uploadFiles(files);
  };

  const thinkingOption = agentOptions?.enable_thinking;
  const thinkingValue = thinkingOption
    ? agentOptionValues.enable_thinking ?? thinkingOption.default
    : undefined;

  return (
    <div
      className="chat-input-shell sm:px-4 pb-3 sm:pb-5"
      style={{ backgroundColor: "var(--theme-bg)" }}
    >
      <form
        onSubmit={handleSubmit}
        className={
          className ?? "mx-auto max-w-3xl lg:max-w-4xl xl:max-w-5xl px-2"
        }
      >
        <div
          ref={containerRef}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`chat-input-container flex flex-col relative w-full rounded-3xl px-1 border transition-all duration-300 ${
            isDraggingOver ? "border-dashed shadow-lg border-2" : ""
          }`}
          data-mention-active={mention.isActive || undefined}
          style={{
            backgroundColor: "var(--theme-bg-card)",
            borderColor: isDraggingOver
              ? "var(--theme-primary)"
              : "var(--theme-border)",
            boxShadow: isDraggingOver
              ? undefined
              : "0 2px 12px rgba(0,0,0,0.06)",
          }}
        >
          <ActiveGoalBar
            goal={activeGoal ?? null}
            label={goalLabel}
            durationLabel={goalDurationLabel}
            clearLabel={goalClearLabel}
            onClear={onClearActiveGoal}
            disabled={isLoading || !canSend}
            embedded
          />
          {mention.isActive && mentionMode === "persona" && (
            <MentionPopup
              presets={mentionSearch.presets}
              highlightedIndex={mention.highlightedIndex}
              selectedPresetId={selectedPersonaPresetId}
              isLoading={mentionSearch.isLoading}
              isLoadingMore={mentionSearch.isLoadingMore}
              hasMore={mentionSearch.hasMore}
              onSelect={applyMentionSelection}
              onHover={setMentionHighlight}
              onClose={dismissMention}
              onLoadMore={mentionSearch.loadMore}
              placement={mentionPopupPlacement ?? undefined}
            />
          )}
          {mention.isActive && mentionMode === "team" && (
            <TeamMentionPopup
              teams={teamMentionSearch.teams}
              highlightedIndex={mention.highlightedIndex}
              selectedTeamId={selectedTeamId}
              isLoading={teamMentionSearch.isLoading}
              onSelect={applyTeamMentionSelection}
              onHover={setMentionHighlight}
              onClose={dismissMention}
              placement={mentionPopupPlacement ?? undefined}
            />
          )}

          <ChatInputAttachments
            attachments={attachments}
            onAttachmentsChange={setAttachments}
            onCancelUpload={cancelUpload}
            onRetryUpload={retryUpload}
            onImageViewerOpen={(url) => setImageViewerSrc(url)}
          />

          <div className="px-2.5 pt-1">
            {emphasizedSkillNames.length > 0 && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 pt-2">
                {emphasizedSkillNames.map((name) => (
                  <span
                    key={name}
                    className="group inline-flex min-w-0 items-center gap-1.5"
                  >
                    <Sparkles
                      size={14}
                      className="shrink-0"
                      style={{ color: "var(--theme-text-secondary)" }}
                    />
                    <span className="max-w-48 truncate text-sm font-semibold text-blue-600 dark:text-blue-400 font-serif">
                      {name}
                    </span>
                    <button
                      type="button"
                      className="inline-flex rounded p-0.5 text-stone-400 opacity-0 transition-opacity group-hover:opacity-100 hover:text-stone-600 dark:text-stone-500 dark:hover:text-stone-300"
                      aria-label={t("chat.skillEmphasis.remove", {
                        name,
                        defaultValue: "移除技能 {{name}}",
                      })}
                      onClick={() => onRemoveEmphasizedSkill?.(name)}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="relative">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  setCursorPosition(e.target.selectionStart);
                }}
                onClick={(e) => {
                  setCursorPosition(e.currentTarget.selectionStart);
                }}
                onKeyUp={(e) => {
                  setCursorPosition(e.currentTarget.selectionStart);
                }}
                onFocus={scheduleTextareaResize}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={
                  canSend
                    ? mentionMode === "team"
                      ? t("chat.teamPlaceholder")
                      : t("chat.placeholder")
                    : t("chat.noPermission")
                }
                disabled={disabled || !canSend}
                className="bg-transparent outline-none w-full pt-[10px] resize-none text-[15px] disabled:opacity-50 leading-relaxed overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] min-h-[40px] sm:min-h-[44px]"
                style={{
                  color: "var(--theme-text)",
                  paddingLeft: 4,
                }}
                rows={1}
              />
            </div>
          </div>
          {slashCommandOpen && (
            <div
              ref={slashMenuRef}
              role="listbox"
              className="absolute bottom-full left-1 z-30 mb-2 w-72 max-w-[calc(100%-0.5rem)] overflow-hidden rounded-xl border shadow-lg"
              style={{
                backgroundColor: "var(--theme-bg-card)",
                borderColor: "var(--theme-border)",
                color: "var(--theme-text)",
              }}
            >
              {slashItems.length === 0 ? (
                <div
                  className="px-3 py-2 text-sm"
                  style={{ color: "var(--theme-text-secondary)" }}
                >
                  {t("chat.slashNoMatches", "没有匹配的技能")}
                </div>
              ) : (
                slashItems.map((item, index) => (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    aria-selected={index === slashHighlightIndex}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      applySlashItem(item);
                    }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors"
                    style={{
                      backgroundColor:
                        index === slashHighlightIndex
                          ? "var(--theme-bg-hover, rgba(128,128,128,0.08))"
                          : "transparent",
                    }}
                    onMouseEnter={() => setSlashHighlightIndex(index)}
                  >
                    {item.kind === "command" ? (
                      <Target
                        size={15}
                        className="shrink-0"
                        style={{ color: "var(--theme-primary)" }}
                      />
                    ) : (
                      <Sparkles
                        size={15}
                        className="shrink-0"
                        style={{ color: "var(--theme-primary)" }}
                      />
                    )}
                    <span
                      className={
                        item.kind === "command"
                          ? "font-mono text-xs"
                          : "truncate text-sm font-medium"
                      }
                    >
                      {item.kind === "command"
                        ? item.command.command
                        : item.name}
                    </span>
                    <span
                      className="min-w-0 flex-1 truncate"
                      style={{ color: "var(--theme-text-secondary)" }}
                    >
                      {item.kind === "command"
                        ? t(item.command.labelKey, item.command.fallbackLabel)
                        : item.description}
                    </span>
                  </button>
                ))
              )}
            </div>
          )}

          <ChatInputToolbar
            activePanel={activePanel}
            onActivePanelChange={setActivePanel}
            canSend={canSend}
            isLoading={isLoading}
            canSubmit={canSubmit}
            hasUploadingAttachment={hasUploadingAttachment}
            enabledToolsCount={enabledToolsCount}
            totalToolsCount={totalToolsCount}
            enabledSkillsCount={enabledSkillsCount}
            totalSkillsCount={totalSkillsCount}
            hasPersonaSelector={!!onUsePersonaPreset}
            personaName={selectedPersonaName}
            hasAgentSelector={agents.length > 1 && !!onSelectAgent}
            agentName={agents.find((a) => a.id === currentAgent)?.name}
            availableModels={availableModels}
            currentModelId={currentModelId}
            onSelectModel={onSelectModel}
            thinkingOption={thinkingOption}
            thinkingValue={thinkingValue}
            onChangeThinking={
              onToggleAgentOption
                ? (value) => onToggleAgentOption("enable_thinking", value)
                : undefined
            }
            uploadCategories={uploadCategories}
            uploadLimits={uploadLimits}
            uploadFiles={uploadFiles}
            selectedPersonaName={selectedPersonaName}
            personaAvatar={personaAvatar}
            onClearPersonaPreset={onClearPersonaPreset}
            currentAgent={currentAgent}
            selectedTeamId={selectedTeamId}
            onSelectTeam={onSelectTeam}
            agentOptions={agentOptions}
            agentOptionValues={agentOptionValues}
            onToggleAgentOption={onToggleAgentOption}
            onStopClick={() => setStopConfirmOpen(true)}
            onNoPermissionClick={() => setContactAdminOpen(true)}
          />
        </div>
      </form>

      <ChatInputSelectors
        activePanel={activePanel}
        onActivePanelChange={setActivePanel}
        tools={tools}
        onToggleTool={onToggleTool}
        onToggleCategory={onToggleCategory}
        onToggleAll={onToggleAll}
        enabledToolsCount={enabledToolsCount}
        totalToolsCount={totalToolsCount}
        skills={skills}
        onToggleSkill={onToggleSkill}
        onToggleSkillCategory={onToggleSkillCategory}
        onToggleAllSkills={onToggleAllSkills}
        pendingSkillNames={pendingSkillNames}
        skillsMutating={skillsMutating}
        enabledSkillsCount={enabledSkillsCount}
        totalSkillsCount={totalSkillsCount}
        enableSkills={enableSkills}
        personaSkillsControlled={personaSkillsControlled}
        selectedPersonaName={selectedPersonaName}
        personaPresets={personaPresets}
        personaPresetsTotal={personaPresetsTotal}
        personaPresetsPage={personaPresetsPage}
        onPersonaPresetsPageChange={onPersonaPresetsPageChange}
        onPersonaPresetsSearchChange={onPersonaPresetsSearchChange}
        onPersonaPresetsTagChange={onPersonaPresetsTagChange}
        selectedPersonaPresetId={selectedPersonaPresetId}
        personaPresetsLoading={personaPresetsLoading}
        personaPresetsMutating={personaPresetsMutating}
        onUsePersonaPreset={onUsePersonaPreset}
        onCopyPersonaPreset={onCopyPersonaPreset}
        onClearPersonaPreset={onClearPersonaPreset}
        canManagePersonaPresets={canManagePersonaPresets}
        agents={agents}
        currentAgent={currentAgent}
        onSelectAgent={onSelectAgent}
        selectedTeamId={selectedTeamId}
        onSelectTeam={onSelectTeam}
        onOpenTeamBuilder={onOpenTeamBuilder}
        agentOptions={agentOptions}
        agentOptionValues={agentOptionValues}
        onToggleAgentOption={onToggleAgentOption}
      />

      {showHelpMenu && <ChatInputHelpMenu className={helpMenuClassName} />}

      {imageViewerSrc && (
        <ImageViewer
          src={imageViewerSrc}
          isOpen={!!imageViewerSrc}
          onClose={() => setImageViewerSrc(null)}
        />
      )}

      <ConfirmDialog
        isOpen={stopConfirmOpen}
        title={t("chat.stopConfirmTitle")}
        message={t("chat.stopConfirmMessage")}
        confirmText={t("chat.stop")}
        cancelText={t("common.cancel")}
        variant="warning"
        onConfirm={() => {
          setStopConfirmOpen(false);
          onStop();
          toast.custom(() => (
            <div
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium"
              style={{
                background:
                  "color-mix(in srgb, var(--theme-primary) 10%, transparent)",
                border:
                  "1px solid color-mix(in srgb, var(--theme-primary) 20%, transparent)",
                color: "var(--theme-primary)",
              }}
            >
              <Ban size={16} className="shrink-0" />
              <span>{t("chat.status.cancelled")}</span>
            </div>
          ));
        }}
        onCancel={() => setStopConfirmOpen(false)}
      />

      <ContactAdminDialog
        isOpen={contactAdminOpen}
        onClose={() => setContactAdminOpen(false)}
        reason="noPermission"
      />

      <SandboxCapacityDialog
        isOpen={capacityModalOpen}
        onClose={() => setCapacityModalOpen(false)}
        title={t("chat.sandboxCapacityTitle", "太火热了")}
        message={t(
          "chat.sandboxCapacityMessage",
          "现在有点太火热啦，沙盒资源暂满，请稍后重试。也可以先切换到 Fast 模式继续聊。",
        )}
        helpText={t(
          "chat.sandboxCapacityHelp",
          "如需协助，可反馈数据资产部赵正通",
        )}
      />
    </div>
  );
});
