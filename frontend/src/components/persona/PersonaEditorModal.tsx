import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { GlassSelect } from "../common/GlassSelect";
import {
  Plus,
  Pencil,
  X,
  Sparkles,
  Tag,
  ChevronDown,
  Save,
  Search,
  Camera,
  Loader2,
  Smile,
  MessageSquare,
  Check,
  Trash2,
  BookOpen,
} from "lucide-react";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { EditorSidebar } from "../common/EditorSidebar";
import toast from "react-hot-toast";
import { useAuth } from "../../hooks/useAuth";
import { useSettingsContext } from "../../contexts/SettingsContext";
import { DifyKbMultiSelect } from "../common/DifyKbMultiSelect";
import {
  buildPersonaPresetPayload,
  draftRowsToStarterPrompts,
  starterPromptsToDraftRows,
} from "./personaPresetEditor";
import { marketplaceApi, uploadApi, personaPresetApi } from "../../services/api";
import { compressImageFile } from "../../utils/imageCompression";
import { uuid } from "../../utils/uuid";
import {
  isPersonaImageAvatar,
  isEmojiAvatar,
  getEmojiAvatarUrl,
} from "./personaAvatar";
import { PersonaAvatarIcon, PersonaAvatarImage } from "./PersonaAvatarIcon";
import { Permission } from "../../types";
import type {
  PersonaPreset,
  PersonaPresetCreate,
  PersonaPresetStatus,
  PersonaPresetUpdate,
  PersonaWeComConfig,
  PreferredAgentId,
  MarketplaceSkillResponse,
  WeComNotifyTargetItem,
} from "../../types";
import { DEFAULT_PREFERRED_AGENT_ID, PREFERRED_AGENT_IDS } from "../../types";

const PERSONA_SKILL_PAGE_SIZE = 20;
const WECOM_DEFAULT_SEGMENT_TARGET_CHARS = 600;
const WECOM_SEGMENT_TARGET_CHAR_OPTIONS = [300, 500, 600] as const;

const AVATAR_EMOJIS: { emoji: string; labelKey: string }[] = [
  { emoji: "✨", labelKey: "personaPresets.emojiSparkles" },
  { emoji: "🤖", labelKey: "personaPresets.emojiRobot" },
  { emoji: "🎓", labelKey: "personaPresets.emojiAcademic" },
  { emoji: "💻", labelKey: "personaPresets.emojiCoding" },
  { emoji: "✍️", labelKey: "personaPresets.emojiWriting" },
  { emoji: "🛡️", labelKey: "personaPresets.emojiSecurity" },
  { emoji: "📊", labelKey: "personaPresets.emojiData" },
  { emoji: "⚡", labelKey: "personaPresets.emojiProductivity" },
  { emoji: "📦", labelKey: "personaPresets.emojiGeneral" },
  { emoji: "🎨", labelKey: "personaPresets.emojiArt" },
  { emoji: "🎵", labelKey: "personaPresets.emojiMusic" },
  { emoji: "📚", labelKey: "personaPresets.emojiLiterature" },
  { emoji: "🧠", labelKey: "personaPresets.emojiIntelligence" },
  { emoji: "🔬", labelKey: "personaPresets.emojiScience" },
  { emoji: "💬", labelKey: "personaPresets.emojiChat" },
  { emoji: "🌟", labelKey: "personaPresets.emojiStar" },
];

interface PersonaEditorModalProps {
  showModal: boolean;
  editingPreset: PersonaPreset | null;
  editorScope: "user" | "global";
  canAdmin: boolean;
  isMutating: boolean;
  createPreset: (data: PersonaPresetCreate) => Promise<PersonaPreset | null>;
  updatePreset: (
    presetId: string,
    data: PersonaPresetUpdate,
  ) => Promise<PersonaPreset | null>;
  onClose: () => void;
}

export function PersonaEditorModal({
  showModal,
  editingPreset,
  editorScope: initialScope,
  canAdmin,
  isMutating,
  createPreset,
  updatePreset,
  onClose,
}: PersonaEditorModalProps) {
  const { t } = useTranslation();
  const [editorScope, setEditorScope] = useState<"user" | "global">(
    initialScope,
  );
  const [editorStatus, setEditorStatus] = useState<PersonaPresetStatus>(
    editingPreset?.status ??
      (initialScope === "global" ? "published" : "draft"),
  );
  const initialSkillNames = [...(editingPreset?.skill_names || [])] as string[];
  const [draft, setDraft] = useState({
    name: editingPreset?.name || "",
    description: editingPreset?.description || "",
    avatar: editingPreset?.avatar || "",
    system_prompt: editingPreset?.system_prompt || "",
    starter_prompts: starterPromptsToDraftRows(editingPreset?.starter_prompts),
    tags: editingPreset?.tags.join(", ") || "",
    skill_names: initialSkillNames,
    dify_kb_dataset_ids: [
      ...(editingPreset?.dify_kb_dataset_ids || []),
    ] as string[],
    preferred_agent_id:
      initialSkillNames.length > 0
        ? "search"
        : (editingPreset?.preferred_agent_id as PreferredAgentId | undefined) ||
          DEFAULT_PREFERRED_AGENT_ID,
  });

  useEffect(() => {
    if (showModal) {
      setEditorScope(initialScope);
      setEditorStatus(
        editingPreset?.status ??
          (initialScope === "global" ? "published" : "draft"),
      );
      const skillNames = [...(editingPreset?.skill_names || [])] as string[];
      setDraft({
        name: editingPreset?.name || "",
        description: editingPreset?.description || "",
        avatar: editingPreset?.avatar || "",
        system_prompt: editingPreset?.system_prompt || "",
        starter_prompts: starterPromptsToDraftRows(
          editingPreset?.starter_prompts,
        ),
        tags: editingPreset?.tags.join(", ") || "",
        skill_names: skillNames,
        dify_kb_dataset_ids: [
          ...(editingPreset?.dify_kb_dataset_ids || []),
        ] as string[],
        preferred_agent_id:
          skillNames.length > 0
            ? "search"
            : (editingPreset?.preferred_agent_id as PreferredAgentId | undefined) ||
              DEFAULT_PREFERRED_AGENT_ID,
      });
      setSkillSearch("");
      setSkillDropdownOpen(false);
      setIconPickerOpen(false);
      setShowWeCom(false);
      setWeComConfig(null);
      setNotifyTargets([]);
      setNotifyTargetInput("");
      setWeComDraft({
        aibotid: "",
        secret: "",
        stream_reply: true,
        send_thinking_message: true,
        segmented_reply: true,
        segment_target_chars: WECOM_DEFAULT_SEGMENT_TARGET_CHARS,
        session_ttl_hours: 24,
      });
    }
  }, [showModal, editingPreset, initialScope]);

  const [skillDropdownOpen, setSkillDropdownOpen] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  const [skillPage, setSkillPage] = useState(1);
  const [allSkills, setAllSkills] = useState<MarketplaceSkillResponse[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [hasMoreSkills, setHasMoreSkills] = useState(false);
  const skillDropdownRef = useRef<HTMLDivElement>(null);
  const avatarInputRef = useRef<HTMLInputElement>(null);
  const iconPickerRef = useRef<HTMLDivElement>(null);
  const [iconPickerOpen, setIconPickerOpen] = useState(false);
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false);
  const draftAvatarOwnerRef = useRef(`draft-${uuid()}`);

  // WeCom config state (only visible for global scope + channel:manage + editing existing preset)
  const { hasPermission } = useAuth();
  const canManageChannels = hasPermission(Permission.CHANNEL_MANAGE);
  const showWeComSection =
    editorScope === "global" && canManageChannels && !!editingPreset;
  const [showWeCom, setShowWeCom] = useState(false);
  const [wecomConfig, setWeComConfig] = useState<PersonaWeComConfig | null>(
    null,
  );
  const [wecomLoading, setWeComLoading] = useState(false);
  const [wecomSaving, setWeComSaving] = useState(false);
  const [wecomDraft, setWeComDraft] = useState({
    aibotid: "",
    secret: "",
    stream_reply: true,
    send_thinking_message: true,
    segmented_reply: true,
    segment_target_chars: WECOM_DEFAULT_SEGMENT_TARGET_CHARS,
    session_ttl_hours: 24,
  });
  // Notify target config state (within WeCom entry config, admin-only)
  const [notifyTargets, setNotifyTargets] = useState<WeComNotifyTargetItem[]>(
    [],
  );
  const [notifyTargetInput, setNotifyTargetInput] = useState("");
  const [notifySaving, setNotifySaving] = useState(false);

  // Dify knowledge base binding (only shown when DIFY_KB_ENABLED is on)
  const { settings: systemSettings } = useSettingsContext();
  const difyKbEnabled = useMemo(() => {
    if (!systemSettings) return false;
    const all = Object.values(systemSettings.settings).flat();
    const item = all.find((s) => s.key === "DIFY_KB_ENABLED");
    return item?.value === true || item?.value === "true";
  }, [systemSettings]);

  // Load WeCom config when editing an existing preset
  useEffect(() => {
    if (!showWeComSection || !editingPreset?.id) return;
    let cancelled = false;
    (async () => {
      setWeComLoading(true);
      try {
        const config = await personaPresetApi.getWeComConfig(editingPreset.id);
        if (cancelled) return;
        if (config) {
          setWeComConfig(config);
          setWeComDraft({
            aibotid: config.aibotid,
            secret: "",
            stream_reply: config.stream_reply,
            send_thinking_message: config.send_thinking_message,
            segmented_reply: config.segmented_reply,
            segment_target_chars:
              config.segment_target_chars ?? WECOM_DEFAULT_SEGMENT_TARGET_CHARS,
            session_ttl_hours: config.session_ttl_hours,
          });
        }
      } catch {
        // No config is fine (404)
      } finally {
        if (!cancelled) setWeComLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showWeComSection, editingPreset?.id]);

  const handleWeComSave = useCallback(async () => {
    if (!editingPreset?.id) return;
    setWeComSaving(true);
    try {
      const updated = await personaPresetApi.updateWeComConfig(
        editingPreset.id,
        {
          aibotid: wecomDraft.aibotid,
          secret: wecomDraft.secret || (wecomConfig?.has_secret ? "" : ""),
          stream_reply: wecomDraft.stream_reply,
          send_thinking_message: wecomDraft.send_thinking_message,
          segmented_reply: wecomDraft.segmented_reply,
          segment_target_chars: wecomDraft.segment_target_chars,
          session_ttl_hours: wecomDraft.session_ttl_hours,
        },
      );
      setWeComConfig(updated);
      setWeComDraft((prev) => ({ ...prev, secret: "" }));
      toast.success(
        t("personaPresets.wecom.saveSuccess", "WeCom configuration saved"),
      );
    } catch (err) {
      toast.error(
        (err as Error).message ||
          t(
            "personaPresets.wecom.saveFailed",
            "Failed to save WeCom configuration",
          ),
      );
    } finally {
      setWeComSaving(false);
    }
  }, [editingPreset?.id, wecomDraft, wecomConfig, t]);

  const handleWeComDelete = useCallback(async () => {
    if (!editingPreset?.id || !wecomConfig) return;
    setWeComSaving(true);
    try {
      await personaPresetApi.deleteWeComConfig(editingPreset.id);
      setWeComConfig(null);
      setNotifyTargets([]);
      setNotifyTargetInput("");
      setWeComDraft({
        aibotid: "",
        secret: "",
        stream_reply: true,
        send_thinking_message: true,
        segmented_reply: true,
        segment_target_chars: WECOM_DEFAULT_SEGMENT_TARGET_CHARS,
        session_ttl_hours: 24,
      });
      toast.success(
        t("personaPresets.wecom.deleteSuccess", "WeCom configuration deleted"),
      );
    } catch (err) {
      toast.error(
        (err as Error).message ||
          t(
            "personaPresets.wecom.deleteFailed",
            "Failed to delete WeCom configuration",
          ),
      );
    } finally {
      setWeComSaving(false);
    }
  }, [editingPreset?.id, wecomConfig, t]);

  // Load WeCom notify targets once the WeCom entry config exists
  useEffect(() => {
    if (!showWeComSection || !editingPreset?.id || !wecomConfig) return;
    let cancelled = false;
    (async () => {
      const result = await personaPresetApi.getWeComNotifyTargets(
        editingPreset.id,
      );
      if (!cancelled) setNotifyTargets(result.targets);
    })();
    return () => {
      cancelled = true;
    };
  }, [showWeComSection, editingPreset?.id, wecomConfig]);

  const handleAddNotifyTarget = useCallback(() => {
    const username = notifyTargetInput.trim();
    if (!username) return;
    setNotifyTargets((prev) =>
      prev.some((item) => item.username === username)
        ? prev
        : [...prev, { username, bound: false }],
    );
    setNotifyTargetInput("");
  }, [notifyTargetInput]);

  const handleRemoveNotifyTarget = useCallback((username: string) => {
    setNotifyTargets((prev) =>
      prev.filter((item) => item.username !== username),
    );
  }, []);

  const handleNotifyTargetsSave = useCallback(async () => {
    if (!editingPreset?.id) return;
    setNotifySaving(true);
    try {
      const result = await personaPresetApi.updateWeComNotifyTargets(
        editingPreset.id,
        notifyTargets.map((item) => item.username),
      );
      setNotifyTargets(result.targets);
      toast.success(
        t(
          "personaPresets.wecom.notifyTargetsSaveSuccess",
          "Notify targets saved",
        ),
      );
    } catch (err) {
      toast.error(
        (err as Error).message ||
          t(
            "personaPresets.wecom.notifyTargetsSaveFailed",
            "Failed to save notify targets",
          ),
      );
    } finally {
      setNotifySaving(false);
    }
  }, [editingPreset?.id, notifyTargets, t]);

  useEffect(() => {
    if (!showModal || !skillDropdownOpen) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setSkillsLoading(true);
      try {
        const page = await marketplaceApi.list({
          search: skillSearch.trim() || undefined,
          activeOnly: true,
          skip: (skillPage - 1) * PERSONA_SKILL_PAGE_SIZE,
          limit: PERSONA_SKILL_PAGE_SIZE,
        });
        if (cancelled) return;
        setAllSkills((previous) => {
          if (skillPage === 1) return page;
          const byName = new Map(previous.map((skill) => [skill.skill_name, skill]));
          for (const skill of page) byName.set(skill.skill_name, skill);
          return [...byName.values()];
        });
        setHasMoreSkills(page.length === PERSONA_SKILL_PAGE_SIZE);
      } catch {
        if (!cancelled) {
          setAllSkills([]);
          setHasMoreSkills(false);
        }
      } finally {
        if (!cancelled) setSkillsLoading(false);
      }
    }, skillPage === 1 ? 180 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [showModal, skillDropdownOpen, skillSearch, skillPage]);

  const handleSkillListScroll = useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      if (skillsLoading || !hasMoreSkills) {
        return;
      }
      const target = event.currentTarget;
      const distanceToBottom =
        target.scrollHeight - target.scrollTop - target.clientHeight;
      if (distanceToBottom <= 48) {
        setSkillPage((page) => page + 1);
      }
    },
    [hasMoreSkills, skillsLoading],
  );

  const displayedSkills = useMemo(() => {
    return [...allSkills].sort((a, b) => {
      const aSel = draft.skill_names.includes(a.skill_name) ? 0 : 1;
      const bSel = draft.skill_names.includes(b.skill_name) ? 0 : 1;
      return aSel - bSel;
    });
  }, [allSkills, draft.skill_names]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (
        skillDropdownOpen &&
        skillDropdownRef.current &&
        !skillDropdownRef.current.contains(target)
      ) {
        setSkillDropdownOpen(false);
      }
      if (
        iconPickerOpen &&
        iconPickerRef.current &&
        !iconPickerRef.current.contains(target)
      ) {
        setIconPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [skillDropdownOpen, iconPickerOpen]);

  const savePreset = useCallback(
    async () => {
      if (!draft.name.trim() || !draft.system_prompt.trim()) return;
      const normalizedDraft = {
        name: draft.name.trim(),
        description: draft.description.trim(),
        avatar: draft.avatar,
        system_prompt: draft.system_prompt.trim(),
        starter_prompts: draftRowsToStarterPrompts(draft.starter_prompts),
        tags: draft.tags
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        skill_names: draft.skill_names,
        dify_kb_dataset_ids: draft.dify_kb_dataset_ids,
        preferred_agent_id: draft.preferred_agent_id,
      };

      const editorOptions = {
        scope: editorScope,
        status: editorStatus,
      };
      const payload = editingPreset
        ? buildPersonaPresetPayload(
            editingPreset,
            normalizedDraft,
            editorOptions,
          )
        : buildPersonaPresetPayload(null, normalizedDraft, editorOptions);
      const saved = editingPreset
        ? await updatePreset(editingPreset.id, payload as PersonaPresetUpdate)
        : await createPreset(payload as PersonaPresetCreate);
      if (!saved) {
        toast.error(
          editingPreset
            ? t("personaPresets.updateFailed", "角色更新失败")
            : t("personaPresets.createFailed", "角色创建失败"),
        );
        return;
      }

      onClose();
      toast.success(
        editingPreset
          ? t("personaPresets.updateSuccess", "角色「{{name}}」已更新", {
              name: normalizedDraft.name,
            })
          : t("personaPresets.createSuccess", "角色「{{name}}」已创建", {
              name: normalizedDraft.name,
            }),
      );
    },
    [
      onClose,
      createPreset,
      draft,
      editingPreset,
      editorScope,
      editorStatus,
      t,
      updatePreset,
    ],
  );

  const handleSave = useCallback(async () => {
    await savePreset();
  }, [savePreset]);

  const handleAvatarUpload = useCallback(
    async (file: File) => {
      setIsUploadingAvatar(true);
      try {
        const compressed = await compressImageFile(file, {
          maxDimension: 256,
          targetSizeKB: 100,
          skipBelowKB: 100,
        });
        const result = await uploadApi.uploadFile(compressed, {
          folder: "persona-avatars",
          managedAsset: {
            kind: "persona",
            ownerRef: editingPreset?.id ?? draftAvatarOwnerRef.current,
          },
        }).promise;
        setDraft((prev) => ({ ...prev, avatar: result.url }));
      } catch (error) {
        console.error("Avatar upload failed:", error);
        toast.error(t("personaPresets.avatarUploadFailed", "头像上传失败"));
      } finally {
        setIsUploadingAvatar(false);
      }
    },
    [editingPreset?.id, t],
  );

  const isFormValid = draft.name.trim() && draft.system_prompt.trim();

  const title = editingPreset
    ? editingPreset.scope === "global"
      ? t("personaPresets.editOfficial", "编辑官方角色")
      : t("personaPresets.editMine", "编辑我的角色")
    : editorScope === "global"
      ? t("personaPresets.publishOfficial", "发布官方角色")
      : t("personaPresets.createMine", "新建我的角色");

  const subtitle =
    editorScope === "global"
      ? t(
          "personaPresets.officialHint",
          "官方角色会展示给所有用户，建议补全简介、标签和可用技能。",
        )
      : t("personaPresets.createHint", "定义角色的行为、语气和能力边界");

  return (
    <>
      <EditorSidebar
        open={showModal}
        onClose={onClose}
        title={title}
        subtitle={subtitle}
        icon={editingPreset ? <Pencil size={16} /> : <Plus size={16} />}
        footer={
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="btn-secondary">
              {t("common.cancel", "取消")}
            </button>
            <button
              onClick={handleSave}
              disabled={isMutating || !isFormValid}
              className="btn-primary disabled:opacity-50"
            >
              {isMutating ? <LoadingSpinner size="sm" /> : <Save size={16} />}
              {t("common.save", "保存")}
            </button>
          </div>
        }
      >
        <div className="es-form">
          {/* Profile: Avatar + Name + Description */}
          <div className="ppe-profile-section">
            <div className="ppe-avatar-upload">
              <div
                className="ppe-avatar-preview"
                onClick={() =>
                  !draft.avatar &&
                  !isUploadingAvatar &&
                  avatarInputRef.current?.click()
                }
              >
                {isEmojiAvatar(draft.avatar) ? (
                  <>
                    <PersonaAvatarImage
                      avatar={getEmojiAvatarUrl(draft.avatar)}
                      alt=""
                      className="ppe-avatar-img"
                    />
                    <button
                      type="button"
                      className="ppe-avatar-remove"
                      onClick={(e) => {
                        e.stopPropagation();
                        setDraft((prev) => ({ ...prev, avatar: "" }));
                      }}
                      title={t("common.remove", "移除")}
                    >
                      <X size={12} />
                    </button>
                  </>
                ) : isPersonaImageAvatar(draft.avatar) ? (
                  <>
                    <PersonaAvatarImage
                      avatar={draft.avatar}
                      alt=""
                      className="ppe-avatar-img"
                      onError={() =>
                        setDraft((prev) => ({ ...prev, avatar: "" }))
                      }
                    />
                    <button
                      type="button"
                      className="ppe-avatar-remove"
                      onClick={(e) => {
                        e.stopPropagation();
                        setDraft((prev) => ({ ...prev, avatar: "" }));
                      }}
                      title={t("common.remove", "移除")}
                    >
                      <X size={12} />
                    </button>
                  </>
                ) : draft.avatar ? (
                  <>
                    <div className="ppe-avatar-placeholder">
                      <PersonaAvatarIcon avatar={draft.avatar} size={20} />
                    </div>
                    <button
                      type="button"
                      className="ppe-avatar-remove"
                      onClick={(e) => {
                        e.stopPropagation();
                        setDraft((prev) => ({ ...prev, avatar: "" }));
                      }}
                      title={t("common.remove", "移除")}
                    >
                      <X size={12} />
                    </button>
                  </>
                ) : (
                  <div className="ppe-avatar-placeholder">
                    <Camera size={18} />
                  </div>
                )}
                {isUploadingAvatar && (
                  <div className="ppe-avatar-uploading">
                    <Loader2 size={16} className="animate-spin" />
                  </div>
                )}
              </div>
              <input
                ref={avatarInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                disabled={isUploadingAvatar}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleAvatarUpload(file);
                  e.target.value = "";
                }}
              />
              <div ref={iconPickerRef} className="relative">
                <button
                  type="button"
                  className="ppe-avatar-hint-btn"
                  disabled={isUploadingAvatar}
                  onClick={() => setIconPickerOpen((v) => !v)}
                >
                  <Smile size={12} />
                  {t("personaPresets.pickIcon", "选择图标")}
                </button>
                {iconPickerOpen && (
                  <div className="ppe-icon-picker">
                    {AVATAR_EMOJIS.map((item) => (
                      <button
                        key={item.emoji}
                        type="button"
                        className="ppe-icon-picker-item"
                        onClick={() => {
                          setDraft((prev) => ({
                            ...prev,
                            avatar: item.emoji,
                          }));
                          setIconPickerOpen(false);
                        }}
                        title={t(item.labelKey)}
                      >
                        <img
                          src={getEmojiAvatarUrl(item.emoji)}
                          alt={t(item.labelKey)}
                          width={20}
                          height={20}
                          style={{ objectFit: "contain" }}
                        />
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="ppe-profile-fields">
              <div className="ppe-field">
                <label className="ppe-label">
                  {t("personaPresets.name", "名称")}
                  <span className="ppe-required">*</span>
                </label>
                <input
                  value={draft.name}
                  onChange={(e) =>
                    setDraft((prev) => ({ ...prev, name: e.target.value }))
                  }
                  className="ppe-input"
                  placeholder={t(
                    "personaPresets.namePlaceholder",
                    "给角色起个名字",
                  )}
                />
              </div>
              <div className="ppe-field">
                <label className="ppe-label">
                  {t("personaPresets.description", "简介")}
                </label>
                <input
                  value={draft.description}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      description: e.target.value,
                    }))
                  }
                  className="ppe-input"
                  placeholder={t(
                    "personaPresets.descriptionPlaceholder",
                    "简短描述角色的能力和特点",
                  )}
                />
              </div>
            </div>
          </div>

          {/* Admin: Scope & Status */}
          {canAdmin && (
            <div
              className="ppe-section ppe-field-animated"
              style={{ animationDelay: "0ms" }}
            >
              <div className="grid gap-2 sm:gap-3 sm:grid-cols-2 ppe-admin-grid">
                <div className="ppe-field">
                  <label className="ppe-label">
                    {t("personaPresets.scope", "范围")}
                  </label>
                  <GlassSelect
                    value={editorScope}
                    onChange={(v) => setEditorScope(v as "user" | "global")}
                    options={[
                      {
                        value: "user",
                        label: t("personaPresets.mine", "我的"),
                      },
                      {
                        value: "global",
                        label: t("personaPresets.official", "官方"),
                      },
                    ]}
                  />
                </div>
                {editorScope === "global" && (
                  <div className="ppe-field">
                    <label className="ppe-label">
                      {t("personaPresets.status", "状态")}
                    </label>
                    <GlassSelect
                      value={editorStatus}
                      onChange={(v) =>
                        setEditorStatus(v as PersonaPresetStatus)
                      }
                      options={[
                        {
                          value: "draft",
                          label: t("personaPresets.draft", "草稿"),
                        },
                        {
                          value: "published",
                          label: t("personaPresets.published", "已发布"),
                        },
                        {
                          value: "archived",
                          label: t("personaPresets.archived", "已归档"),
                        },
                      ]}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Preferred Agent Template */}
          <div className="ppe-field">
            <label className="ppe-label">
              {t("personaPresets.preferredAgent", "能力模板")}
            </label>
            <GlassSelect
              value={draft.preferred_agent_id}
              onChange={(v) =>
                setDraft((prev) => ({
                  ...prev,
                  preferred_agent_id: v as PreferredAgentId,
                }))
              }
              options={PREFERRED_AGENT_IDS.map((id) => ({
                value: id,
                label: t(`personaPresets.agent.${id}`, id),
                disabled: id === "fast" && draft.skill_names.length > 0,
              }))}
            />
            <p className="ppe-hint">
              {t(
                "personaPresets.preferredAgentHint",
                "使用该角色开聊时默认采用的能力模板；会话内不可切换。",
              )}
            </p>
          </div>

          {/* System Prompt */}
          <div className="ppe-field">
            <label className="ppe-label">
              <MessageSquare size={13} className="ppe-label-icon" />
              {t("personaPresets.systemPrompt", "系统提示词")}
              <span className="ppe-required">*</span>
            </label>
            <div className="ppe-textarea-wrap">
              <textarea
                value={draft.system_prompt}
                onChange={(e) =>
                  setDraft((prev) => ({
                    ...prev,
                    system_prompt: e.target.value,
                  }))
                }
                rows={8}
                className="ppe-textarea"
                placeholder={t(
                  "personaPresets.systemPromptPlaceholder",
                  "定义角色的行为、语气和能力边界...",
                )}
              />
              <div className="ppe-char-counter">
                {draft.system_prompt.length}
              </div>
            </div>
          </div>

          {/* Starter Prompts */}
          <div className="ppe-field">
            <label className="ppe-label">
              <Sparkles size={13} className="ppe-label-icon" />
              {t("personaPresets.starterPrompts", "开场提示词")}
            </label>
            <div className="ppe-starter-list">
              {draft.starter_prompts.map((prompt, index) => (
                <div key={index} className="ppe-starter-row">
                  <input
                    value={prompt.icon}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        starter_prompts: prev.starter_prompts.map((item, i) =>
                          i === index
                            ? { ...item, icon: e.target.value }
                            : item,
                        ),
                      }))
                    }
                    className="ppe-input ppe-starter-icon"
                    placeholder={t("personaPresets.starterIcon", "图标")}
                  />
                  <input
                    value={prompt.text}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        starter_prompts: prev.starter_prompts.map((item, i) =>
                          i === index
                            ? { ...item, text: e.target.value }
                            : item,
                        ),
                      }))
                    }
                    className="ppe-input ppe-starter-text"
                    placeholder={t(
                      "personaPresets.starterPromptPlaceholder",
                      '输入提示词，或使用 {"zh":"...","en":"..."}',
                    )}
                  />
                  <button
                    type="button"
                    className="ppe-starter-remove"
                    onClick={() =>
                      setDraft((prev) => ({
                        ...prev,
                        starter_prompts: prev.starter_prompts.filter(
                          (_, i) => i !== index,
                        ),
                      }))
                    }
                    title={t("common.delete", "删除")}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              className="ppe-starter-add"
              onClick={() =>
                setDraft((prev) => ({
                  ...prev,
                  starter_prompts: [
                    ...prev.starter_prompts,
                    { icon: "", text: "" },
                  ],
                }))
              }
            >
              <Plus size={13} />
              {t("personaPresets.addStarterPrompt", "添加开场提示词")}
            </button>
          </div>

          {/* Tags + Skills */}
          <div className="ppe-meta-grid">
            <div className="ppe-field">
              <label className="ppe-label">
                <Tag size={13} className="ppe-label-icon" />
                {t("personaPresets.tagsInput", "标签")}
              </label>
              <input
                value={draft.tags}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, tags: e.target.value }))
                }
                className="ppe-input"
                placeholder={t(
                  "personaPresets.tagsInputPlaceholder",
                  "写作, 翻译, 代码",
                )}
              />
              {draft.tags.trim() && (
                <div className="ppe-chip-row">
                  {draft.tags
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean)
                    .map((tag) => (
                      <span key={tag} className="ppe-tag-chip">
                        {tag}
                      </span>
                    ))}
                </div>
              )}
            </div>

            <div className="ppe-field">
              <label className="ppe-label">
                <Sparkles size={13} className="ppe-label-icon" />
                {t("personaPresets.skillsInput", "Skills")}
              </label>
              <div ref={skillDropdownRef} className="relative">
                <button
                  type="button"
                  onClick={() => {
                    setSkillDropdownOpen((v) => !v);
                    setSkillSearch("");
                    setSkillPage(1);
                  }}
                  className={`ppe-skill-trigger ${
                    skillDropdownOpen ? "ppe-skill-trigger--open" : ""
                  }`}
                >
                  {draft.skill_names.length > 0 ? (
                    <span className="ppe-skill-trigger__count">
                      <Sparkles size={12} />
                      {t(
                        "personaPresets.skillCount",
                        "{{count}} 个技能已选择",
                        {
                          count: draft.skill_names.length,
                        },
                      )}
                    </span>
                  ) : (
                    <span className="ppe-skill-trigger__placeholder">
                      {t(
                        "personaPresets.skillsInputPlaceholder",
                        "选择技能...",
                      )}
                    </span>
                  )}
                  <ChevronDown
                    size={14}
                    className={`ppe-skill-trigger__chevron ${
                      skillDropdownOpen ? "rotate-180" : ""
                    }`}
                  />
                </button>

                {draft.skill_names.length > 0 && !skillDropdownOpen && (
                  <div className="ppe-skill-selected-area">
                    {draft.skill_names.map((name) => (
                      <span key={name} className="ppe-skill-chip">
                        {name}
                        <X
                          size={11}
                          className="ppe-skill-chip-remove"
                          onClick={() =>
                            setDraft((prev) => ({
                              ...prev,
                              skill_names: prev.skill_names.filter(
                                (n) => n !== name,
                              ),
                            }))
                          }
                        />
                      </span>
                    ))}
                  </div>
                )}

                {skillDropdownOpen && (
                  <div className="ppe-skill-dropdown">
                    <div className="ppe-skill-dropdown__header">
                      <div className="ppe-skill-dropdown__search-wrap">
                        <Search
                          size={14}
                          className="ppe-skill-dropdown__search-icon"
                        />
                        <input
                          type="text"
                          value={skillSearch}
                          onChange={(e) => {
                            setSkillSearch(e.target.value);
                            setSkillPage(1);
                          }}
                          placeholder={t(
                            "skills.searchPlaceholder",
                            "搜索技能...",
                          )}
                          className="ppe-skill-search"
                          autoFocus
                        />
                      </div>
                      {draft.skill_names.length > 0 && (
                        <button
                          type="button"
                          className="ppe-skill-dropdown__clear-all"
                          onClick={() =>
                            setDraft((prev) => ({ ...prev, skill_names: [] }))
                          }
                        >
                          {t("common.clearAll", "清除全部")}
                        </button>
                      )}
                    </div>

                    {draft.skill_names.length > 0 && (
                      <div className="ppe-skill-selected-bar">
                        {draft.skill_names.map((name) => (
                          <span key={name} className="ppe-skill-chip">
                            {name}
                            <X
                              size={11}
                              className="ppe-skill-chip-remove"
                              onClick={() =>
                                setDraft((prev) => ({
                                  ...prev,
                                  skill_names: prev.skill_names.filter(
                                    (n) => n !== name,
                                  ),
                                }))
                              }
                            />
                          </span>
                        ))}
                      </div>
                    )}

                    <div
                      className="ppe-skill-dropdown__list"
                      onScroll={handleSkillListScroll}
                    >
                      {displayedSkills.length > 0 ? (
                        displayedSkills.map((skill) => {
                          const isSelected = draft.skill_names.includes(
                            skill.skill_name,
                          );
                          return (
                            <button
                              key={skill.skill_name}
                              type="button"
                              onClick={() => {
                                setDraft((prev) => ({
                                  ...prev,
                                  skill_names: isSelected
                                    ? prev.skill_names.filter(
                                        (n) => n !== skill.skill_name,
                                      )
                                    : [...prev.skill_names, skill.skill_name],
                                  preferred_agent_id: isSelected
                                    ? prev.preferred_agent_id
                                    : "search",
                                }));
                              }}
                              className={`ppe-skill-option ${
                                isSelected ? "ppe-skill-option--selected" : ""
                              }`}
                            >
                              <div className="ppe-skill-option__check-ring">
                                {isSelected ? (
                                  <Check
                                    size={12}
                                    className="ppe-skill-option__check-icon"
                                  />
                                ) : (
                                  <Plus
                                    size={12}
                                    className="ppe-skill-option__plus-icon"
                                  />
                                )}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="text-sm font-medium truncate">
                                  {skill.skill_name}
                                </div>
                                {skill.description && (
                                  <div className="text-[11px] text-[var(--theme-text-secondary)] truncate mt-0.5">
                                    {skill.description}
                                  </div>
                                )}
                              </div>
                            </button>
                          );
                        })
                      ) : (
                        <div className="ppe-skill-dropdown__empty">
                          <Sparkles
                            size={20}
                            className="ppe-skill-dropdown__empty-icon"
                          />
                          <span>
                            {t("skills.noMatchingSkills", "没有匹配的技能")}
                          </span>
                        </div>
                      )}
                      {skillsLoading && displayedSkills.length > 0 && (
                        <div className="ppe-skill-dropdown__loading">
                          <Loader2 size={14} className="animate-spin" />
                          <span>{t("common.loading", "加载中...")}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Dify Knowledge Bases (only when DIFY_KB_ENABLED) */}
          {difyKbEnabled && (
            <div className="ppe-field">
              <label className="ppe-label">
                <BookOpen size={13} className="ppe-label-icon" />
                {t("personaPresets.difyKb", "Dify 知识库")}
              </label>
              <p
                className="text-xs mt-0.5 mb-2"
                style={{ color: "var(--theme-text-secondary)" }}
              >
                {t(
                  "personaPresets.difyKbHint",
                  "选择该角色可检索的 Dify 知识库；留空则不启用知识库检索。",
                )}
              </p>
              <DifyKbMultiSelect
                value={draft.dify_kb_dataset_ids}
                onChange={(ids) =>
                  setDraft((prev) => ({ ...prev, dify_kb_dataset_ids: ids }))
                }
              />
            </div>
          )}

          {/* WeCom Entry Config (global scope + channel:manage + editing existing preset only) */}
          {showWeComSection && (
            <div className="ppe-section ppe-field-animated">
              <button
                type="button"
                onClick={() => setShowWeCom(!showWeCom)}
                className="ppe-section-header cursor-pointer w-full flex items-center"
                style={{
                  background: "none",
                  border: "none",
                  padding: 0,
                  font: "inherit",
                  color: "inherit",
                }}
              >
                <MessageSquare size={13} className="ppe-label-icon" />
                <span className="text-sm font-medium">
                  {t("personaPresets.wecom.title", "WeCom Entry")}
                </span>
                <ChevronDown
                  size={14}
                  className={`ml-auto transition-transform ${
                    showWeCom ? "rotate-180" : ""
                  }`}
                />
              </button>
              {showWeCom && (
                <div className="mt-3 space-y-3">
                  {wecomLoading ? (
                    <div className="flex items-center justify-center py-4">
                      <LoadingSpinner size="sm" />
                    </div>
                  ) : (
                    <>
                      {/* aibotid */}
                      <div className="ppe-field">
                        <label className="ppe-label">
                          {t(
                            "personaPresets.wecom.aibotid",
                            "Bot ID (aibotid)",
                          )}
                        </label>
                        <input
                          type="text"
                          value={wecomDraft.aibotid}
                          onChange={(e) =>
                            setWeComDraft((prev) => ({
                              ...prev,
                              aibotid: e.target.value,
                            }))
                          }
                          className="ppe-input"
                          placeholder={t(
                            "personaPresets.wecom.aibotidPlaceholder",
                            "bot_xxxxxxxxxx",
                          )}
                        />
                      </div>

                      {/* secret */}
                      <div className="ppe-field">
                        <label className="ppe-label">
                          {t("personaPresets.wecom.secret", "Bot Secret")}
                          {wecomConfig?.has_secret && (
                            <span
                              className="text-xs ml-1"
                              style={{
                                color: "var(--theme-text-secondary)",
                              }}
                            >
                              {t(
                                "personaPresets.wecom.secretHint",
                                "Leave empty to keep current value",
                              )}
                            </span>
                          )}
                        </label>
                        <input
                          type="password"
                          value={wecomDraft.secret}
                          onChange={(e) =>
                            setWeComDraft((prev) => ({
                              ...prev,
                              secret: e.target.value,
                            }))
                          }
                          className="ppe-input"
                          placeholder={
                            wecomConfig?.has_secret
                              ? t("personaPresets.wecom.secretMask", "••••••••")
                              : t(
                                  "personaPresets.wecom.secretPlaceholder",
                                  "Enter bot secret",
                                )
                          }
                        />
                      </div>

                      {/* stream_reply toggle */}
                      <div className="ppe-field">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <label className="ppe-label">
                              {t(
                                "personaPresets.wecom.streamReply",
                                "Stream Reply",
                              )}
                            </label>
                            <p
                              className="text-xs mt-0.5"
                              style={{
                                color: "var(--theme-text-secondary)",
                              }}
                            >
                              {t(
                                "personaPresets.wecom.streamReplyDesc",
                                "Stream responses via WebSocket",
                              )}
                            </p>
                          </div>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={wecomDraft.stream_reply}
                            onClick={() =>
                              setWeComDraft((prev) => ({
                                ...prev,
                                stream_reply: !prev.stream_reply,
                              }))
                            }
                            className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 ${
                              wecomDraft.stream_reply
                                ? "bg-amber-500 shadow-sm shadow-amber-500/25"
                                : "bg-stone-200 dark:bg-stone-700"
                            }`}
                          >
                            <span
                              className={`pointer-events-none inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                                wecomDraft.stream_reply
                                  ? "translate-x-[18px]"
                                  : "translate-x-[3px]"
                              }`}
                            />
                          </button>
                        </div>
                      </div>

                      {/* send_thinking_message toggle */}
                      <div className="ppe-field">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <label className="ppe-label">
                              {t(
                                "personaPresets.wecom.sendThinkingMessage",
                                "Send Thinking Placeholder",
                              )}
                            </label>
                            <p
                              className="text-xs mt-0.5"
                              style={{
                                color: "var(--theme-text-secondary)",
                              }}
                            >
                              {t(
                                "personaPresets.wecom.sendThinkingMessageDesc",
                                "Send a placeholder message within the 5-second callback window",
                              )}
                            </p>
                          </div>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={wecomDraft.send_thinking_message}
                            onClick={() =>
                              setWeComDraft((prev) => ({
                                ...prev,
                                send_thinking_message:
                                  !prev.send_thinking_message,
                              }))
                            }
                            className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 ${
                              wecomDraft.send_thinking_message
                                ? "bg-amber-500 shadow-sm shadow-amber-500/25"
                                : "bg-stone-200 dark:bg-stone-700"
                            }`}
                          >
                            <span
                              className={`pointer-events-none inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                                wecomDraft.send_thinking_message
                                  ? "translate-x-[18px]"
                                  : "translate-x-[3px]"
                              }`}
                            />
                          </button>
                        </div>
                      </div>

                      {/* segmented_reply toggle */}
                      <div className="ppe-field">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <label className="ppe-label">
                              {t(
                                "personaPresets.wecom.segmentedReply",
                                "Segmented Reply",
                              )}
                            </label>
                            <p
                              className="text-xs mt-0.5"
                              style={{
                                color: "var(--theme-text-secondary)",
                              }}
                            >
                              {t(
                                "personaPresets.wecom.segmentedReplyDesc",
                                "Automatically split long replies into segments",
                              )}
                            </p>
                          </div>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={wecomDraft.segmented_reply}
                            onClick={() =>
                              setWeComDraft((prev) => ({
                                ...prev,
                                segmented_reply: !prev.segmented_reply,
                              }))
                            }
                            className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 ${
                              wecomDraft.segmented_reply
                                ? "bg-amber-500 shadow-sm shadow-amber-500/25"
                                : "bg-stone-200 dark:bg-stone-700"
                            }`}
                          >
                            <span
                              className={`pointer-events-none inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                                wecomDraft.segmented_reply
                                  ? "translate-x-[18px]"
                                  : "translate-x-[3px]"
                              }`}
                            />
                          </button>
                        </div>
                        {wecomDraft.segmented_reply && (
                          <div className="mt-3">
                            <label
                              htmlFor="wecom-segment-target-chars"
                              className="ppe-label"
                            >
                              {t(
                                "personaPresets.wecom.segmentTargetChars",
                                "Approximate characters per segment",
                              )}
                            </label>
                            <select
                              id="wecom-segment-target-chars"
                              className="ppe-input mt-1"
                              value={wecomDraft.segment_target_chars}
                              onChange={(e) =>
                                setWeComDraft((prev) => ({
                                  ...prev,
                                  segment_target_chars: Number(e.target.value),
                                }))
                              }
                            >
                              {WECOM_SEGMENT_TARGET_CHAR_OPTIONS.map(
                                (value) => (
                                  <option key={value} value={value}>
                                    {t(
                                      "personaPresets.wecom.segmentTargetCharsOption",
                                      "About {{count}} characters",
                                      { count: value },
                                    )}
                                  </option>
                                ),
                              )}
                            </select>
                            <p
                              className="text-xs mt-1"
                              style={{
                                color: "var(--theme-text-secondary)",
                              }}
                            >
                              {t(
                                "personaPresets.wecom.segmentTargetCharsDesc",
                                "Actual segments may be shorter at natural boundaries and always stay within WeCom's byte limit.",
                              )}
                            </p>
                          </div>
                        )}
                      </div>

                      {/* session_ttl_hours */}
                      <div className="ppe-field">
                        <label className="ppe-label">
                          {t(
                            "personaPresets.wecom.sessionTtlHours",
                            "Session TTL (hours)",
                          )}
                        </label>
                        <p
                          className="text-xs mt-0.5"
                          style={{ color: "var(--theme-text-secondary)" }}
                        >
                          {t(
                            "personaPresets.wecom.sessionTtlHoursDesc",
                            "Session expiration time, 0 means never expire",
                          )}
                        </p>
                        <input
                          type="number"
                          min={0}
                          max={720}
                          value={wecomDraft.session_ttl_hours}
                          onChange={(e) =>
                            setWeComDraft((prev) => ({
                              ...prev,
                              session_ttl_hours: parseInt(e.target.value) || 0,
                            }))
                          }
                          className="ppe-input"
                        />
                      </div>

                      {/* Save / Delete buttons */}
                      <div className="flex gap-2 pt-2">
                        <button
                          type="button"
                          onClick={handleWeComSave}
                          disabled={
                            wecomSaving ||
                            !wecomDraft.aibotid ||
                            (!wecomConfig?.has_secret && !wecomDraft.secret)
                          }
                          className="btn-primary flex-1 disabled:opacity-50"
                        >
                          {wecomSaving ? (
                            <LoadingSpinner size="sm" />
                          ) : (
                            <Save size={16} />
                          )}
                          {t("common.save", "Save")}
                        </button>
                        {wecomConfig && (
                          <button
                            type="button"
                            onClick={handleWeComDelete}
                            disabled={wecomSaving}
                            className="btn-secondary hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-900/30 dark:hover:text-red-400 disabled:opacity-50"
                          >
                            <Trash2 size={16} />
                            {t("common.delete", "Delete")}
                          </button>
                        )}
                      </div>

                      {/* Notify targets (only when a WeCom entry exists) */}
                      {wecomConfig && (
                        <div className="ppe-field pt-1">
                          <label className="ppe-label">
                            {t(
                              "personaPresets.wecom.notifyTargets",
                              "Notify Targets",
                            )}
                          </label>
                          <p
                            className="text-xs mt-0.5 mb-2"
                            style={{ color: "var(--theme-text-secondary)" }}
                          >
                            {t(
                              "personaPresets.wecom.notifyTargetsDesc",
                              "当企业微信用户对本 Persona 点赞/点踩时，向以下目标用户推送通知。",
                            )}
                          </p>

                          {/* target list */}
                          <div className="space-y-1.5">
                            {notifyTargets.length === 0 && (
                              <p
                                className="text-xs"
                                style={{
                                  color: "var(--theme-text-secondary)",
                                }}
                              >
                                {t(
                                  "personaPresets.wecom.notifyTargetsEmpty",
                                  "尚未配置通知对象",
                                )}
                              </p>
                            )}
                            {notifyTargets.map((item) => (
                              <div
                                key={item.username}
                                className="flex items-center justify-between gap-2 rounded-md bg-stone-100 px-2 py-1.5 dark:bg-stone-800/60"
                              >
                                <span className="text-sm font-medium">
                                  {item.username}
                                </span>
                                <span className="flex items-center gap-2">
                                  <span
                                    className={`inline-block rounded-full px-2 py-0.5 text-xs ${
                                      item.bound
                                        ? "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400"
                                        : "bg-stone-200 text-stone-600 dark:bg-stone-700 dark:text-stone-400"
                                    }`}
                                  >
                                    {item.bound
                                      ? t(
                                          "personaPresets.wecom.notifyTargetBound",
                                          "已绑定",
                                        )
                                      : t(
                                          "personaPresets.wecom.notifyTargetUnbound",
                                          "未绑定",
                                        )}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() =>
                                      handleRemoveNotifyTarget(item.username)
                                    }
                                    className="text-stone-400 hover:text-red-500"
                                    aria-label={t("common.delete", "Delete")}
                                  >
                                    <X size={14} />
                                  </button>
                                </span>
                              </div>
                            ))}
                          </div>

                          {/* add target */}
                          <div className="mt-2 flex gap-2">
                            <input
                              type="text"
                              value={notifyTargetInput}
                              onChange={(e) =>
                                setNotifyTargetInput(e.target.value)
                              }
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  e.preventDefault();
                                  handleAddNotifyTarget();
                                }
                              }}
                              className="ppe-input flex-1"
                              placeholder={t(
                                "personaPresets.wecom.notifyTargetInputPlaceholder",
                                "企业微信 userid / username",
                              )}
                            />
                            <button
                              type="button"
                              onClick={handleAddNotifyTarget}
                              disabled={!notifyTargetInput.trim()}
                              className="btn-secondary disabled:opacity-50"
                            >
                              <Plus size={16} />
                              {t(
                                "personaPresets.wecom.notifyTargetAdd",
                                "添加",
                              )}
                            </button>
                          </div>

                          <p
                            className="text-xs mt-2"
                            style={{ color: "var(--theme-text-secondary)" }}
                          >
                            {t(
                              "personaPresets.wecom.notifyTargetsUnboundHint",
                              "未绑定的用户需在企业微信向本机器人发送『绑定通知』完成绑定",
                            )}
                          </p>

                          <button
                            type="button"
                            onClick={handleNotifyTargetsSave}
                            disabled={notifySaving}
                            className="btn-primary mt-3 w-full disabled:opacity-50"
                          >
                            {notifySaving ? (
                              <LoadingSpinner size="sm" />
                            ) : (
                              <Save size={16} />
                            )}
                            {t(
                              "personaPresets.wecom.notifyTargetsSave",
                              "保存通知对象",
                            )}
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </EditorSidebar>
    </>
  );
}
