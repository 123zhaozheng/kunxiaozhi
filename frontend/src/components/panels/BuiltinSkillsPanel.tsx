import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  ShieldCheck,
  Plus,
  Archive,
  UploadCloud,
  FileArchive,
  Upload,
  Pencil,
  Trash2,
  ShoppingBag,
  Package,
} from "lucide-react";
import { builtinSkillApi } from "../../services/api/builtinSkill";
import { marketplaceApi } from "../../services/api/marketplace";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { EditorSidebar } from "../common/EditorSidebar";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { RoleSelector } from "../mcp/RoleSelector";
import type {
  BuiltinSkill,
  BuiltinSkillZipPreviewSkill,
  MarketplaceSkillResponse,
} from "../../types";

interface BuiltinSkillsPanelProps {
  /** When true, rendered inside SkillsHubPanel (no own page chrome). */
  embedded?: boolean;
}

type AddSource = "zip" | "marketplace";

export function BuiltinSkillsPanel({ embedded = false }: BuiltinSkillsPanelProps) {
  const { t } = useTranslation();

  const [skills, setSkills] = useState<BuiltinSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  // Add modal state
  const [showAdd, setShowAdd] = useState(false);
  const [addSource, setAddSource] = useState<AddSource>("zip");
  const [allowedRoles, setAllowedRoles] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // Zip upload state
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [zipPreviewing, setZipPreviewing] = useState(false);
  const [zipPreview, setZipPreview] = useState<
    BuiltinSkillZipPreviewSkill[]
  >([]);
  const [isDragging, setIsDragging] = useState(false);
  const zipInputRef = useRef<HTMLInputElement>(null);

  // Marketplace state
  const [marketplaceSkills, setMarketplaceSkills] = useState<
    MarketplaceSkillResponse[]
  >([]);
  const [marketplaceLoading, setMarketplaceLoading] = useState(false);
  const [marketplaceName, setMarketplaceName] = useState("");

  // Edit modal state
  const [editing, setEditing] = useState<BuiltinSkill | null>(null);
  const [editDescription, setEditDescription] = useState("");
  const [editRoles, setEditRoles] = useState<string[]>([]);
  const [editSubmitting, setEditSubmitting] = useState(false);

  // Delete confirm state
  const [deleteTarget, setDeleteTarget] = useState<BuiltinSkill | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);

  const fetchList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await builtinSkillApi.list({ limit: 500 });
      setSkills(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  const filtered = search.trim()
    ? skills.filter(
        (s) =>
          s.skill_name.toLowerCase().includes(search.trim().toLowerCase()) ||
          (s.description || "").toLowerCase().includes(search.trim().toLowerCase()),
      )
    : skills;

  // ===== Add modal helpers =====

  const resetAddState = useCallback(() => {
    setAddSource("zip");
    setAllowedRoles([]);
    setZipFile(null);
    setZipPreview([]);
    setMarketplaceName("");
    setIsDragging(false);
  }, []);

  const closeAdd = useCallback(() => {
    setShowAdd(false);
    resetAddState();
  }, [resetAddState]);

  const loadMarketplaceSkills = useCallback(async () => {
    setMarketplaceLoading(true);
    try {
      const list = await marketplaceApi.list({ limit: 500 });
      setMarketplaceSkills(list);
    } catch {
      setMarketplaceSkills([]);
    } finally {
      setMarketplaceLoading(false);
    }
  }, []);

  const handleZipFileChange = useCallback(
    async (file: File | null) => {
      if (!file) return;
      if (!file.name.endsWith(".zip")) {
        toast.error(t("builtinSkills.invalidZip"));
        return;
      }
      setZipFile(file);
      setZipPreview([]);
      setZipPreviewing(true);
      try {
        const result = await builtinSkillApi.previewZip(file);
        setZipPreview(result.skills);
      } catch (err) {
        toast.error(
          err instanceof Error
            ? err.message
            : t("builtinSkills.previewFailed"),
        );
        setZipFile(null);
      } finally {
        setZipPreviewing(false);
      }
    },
    [t],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files?.[0];
      handleZipFileChange(file ?? null);
    },
    [handleZipFileChange],
  );

  const handleAddSubmit = useCallback(async () => {
    setSubmitting(true);
    try {
      if (addSource === "zip") {
        if (!zipFile) {
          toast.error(t("builtinSkills.selectZipFile"));
          setSubmitting(false);
          return;
        }
        const result = await builtinSkillApi.uploadZip(zipFile, allowedRoles);
        toast.success(
          t("builtinSkills.createSuccess", { count: result.skill_count }),
        );
      } else {
        if (!marketplaceName.trim()) {
          toast.error(t("builtinSkills.marketplaceNameRequired"));
          setSubmitting(false);
          return;
        }
        await builtinSkillApi.fromMarketplace({
          marketplace_name: marketplaceName.trim(),
          allowed_roles: allowedRoles,
        });
        toast.success(t("builtinSkills.createSuccess", { count: 1 }));
      }
      closeAdd();
      fetchList();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("builtinSkills.createFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }, [
    addSource,
    zipFile,
    marketplaceName,
    allowedRoles,
    closeAdd,
    fetchList,
    t,
  ]);

  // ===== Row actions =====

  const handleToggle = useCallback(
    async (skill: BuiltinSkill) => {
      try {
        const updated = await builtinSkillApi.update(skill.skill_name, {
          is_active: !skill.is_active,
        });
        setSkills((prev) =>
          prev.map((s) =>
            s.skill_name === skill.skill_name ? updated : s,
          ),
        );
        toast.success(
          t(
            updated.is_active
              ? "builtinSkills.activateSuccess"
              : "builtinSkills.deactivateSuccess",
          ),
        );
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : t("builtinSkills.updateFailed"),
        );
      }
    },
    [t],
  );

  const openEdit = useCallback((skill: BuiltinSkill) => {
    setEditing(skill);
    setEditDescription(skill.description || "");
    setEditRoles(skill.allowed_roles || []);
  }, []);

  const handleEditSave = useCallback(async () => {
    if (!editing) return;
    setEditSubmitting(true);
    try {
      const updated = await builtinSkillApi.update(editing.skill_name, {
        description: editDescription,
        allowed_roles: editRoles,
      });
      setSkills((prev) =>
        prev.map((s) =>
          s.skill_name === editing.skill_name ? updated : s,
        ),
      );
      toast.success(t("builtinSkills.updateSuccess"));
      setEditing(null);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("builtinSkills.updateFailed"),
      );
    } finally {
      setEditSubmitting(false);
    }
  }, [editing, editDescription, editRoles, t]);

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleteSubmitting(true);
    try {
      await builtinSkillApi.delete(deleteTarget.skill_name);
      setSkills((prev) =>
        prev.filter((s) => s.skill_name !== deleteTarget.skill_name),
      );
      toast.success(t("builtinSkills.deleteSuccess"));
      setDeleteTarget(null);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("builtinSkills.deleteFailed"),
      );
    } finally {
      setDeleteSubmitting(false);
    }
  }, [deleteTarget, t]);

  const newZipCount = zipPreview.filter((s) => !s.already_exists).length;
  const canSubmit =
    addSource === "zip"
      ? !!zipFile && !zipPreviewing && newZipCount > 0
      : !!marketplaceName.trim();

  // ===== Render =====

  return (
    <div className={`flex h-full min-h-0 flex-col ${embedded ? "" : "p-4"}`}>
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-1 pb-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("builtinSkills.searchPlaceholder")}
          className="h-9 flex-1 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 text-sm text-[var(--theme-text)] placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-primary)] focus:outline-none"
        />
        <button
          type="button"
          onClick={() => {
            resetAddState();
            setShowAdd(true);
            loadMarketplaceSkills();
          }}
          className="btn-primary inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm"
        >
          <Plus size={16} />
          <span className="hidden sm:inline">
            {t("builtinSkills.add")}
          </span>
        </button>
      </div>

      {/* List */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-[var(--theme-text-secondary)]">
            <LoadingSpinner size="sm" />
            {t("builtinSkills.loading")}
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center text-sm text-red-500">
            {error}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-[var(--theme-text-secondary)]">
            <ShieldCheck
              size={40}
              className="mb-2 text-[var(--theme-text-tertiary)]"
            />
            <p className="text-sm">{t("builtinSkills.noSkills")}</p>
          </div>
        ) : (
          <div className="space-y-2 px-1 pb-4">
            {filtered.map((skill) => (
              <BuiltinSkillRow
                key={skill.skill_name}
                skill={skill}
                onToggle={() => handleToggle(skill)}
                onEdit={() => openEdit(skill)}
                onDelete={() => setDeleteTarget(skill)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Add modal */}
      <EditorSidebar
        open={showAdd}
        onClose={closeAdd}
        title={t("builtinSkills.addTitle")}
        subtitle={t("builtinSkills.addSubtitle")}
        icon={<ShieldCheck size={16} />}
        width="wide"
        footer={
          <div className="flex justify-end gap-2">
            <button
              onClick={closeAdd}
              disabled={submitting}
              className="btn-secondary disabled:opacity-50"
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={handleAddSubmit}
              disabled={!canSubmit || submitting}
              className="btn-primary disabled:opacity-50"
            >
              {submitting ? (
                <LoadingSpinner size="sm" color="text-white" />
              ) : (
                <Upload size={16} />
              )}
              <span className="hidden sm:inline">
                {submitting
                  ? t("builtinSkills.creating")
                  : t("builtinSkills.create")}
              </span>
            </button>
          </div>
        }
      >
        <div data-disable-global-file-drop="true" className="es-form space-y-4">
          {/* Source toggle */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-[var(--theme-text)]">
              {t("builtinSkills.source")}
            </label>
            <div className="inline-flex rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-1">
              {(
                [
                  {
                    key: "zip" as const,
                    label: t("builtinSkills.sourceZip"),
                    icon: Archive,
                  },
                  {
                    key: "marketplace" as const,
                    label: t("builtinSkills.sourceMarketplace"),
                    icon: ShoppingBag,
                  },
                ] as const
              ).map(({ key, label, icon: Icon }) => {
                const isActive = addSource === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setAddSource(key)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-all ${
                      isActive
                        ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)] shadow-sm"
                        : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
                    }`}
                    aria-pressed={isActive}
                  >
                    <Icon size={14} />
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Allowed roles (applies to both sources) */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-[var(--theme-text)]">
              {t("builtinSkills.allowedRoles")}
            </label>
            <p className="mb-1.5 text-xs text-[var(--theme-text-secondary)]">
              {t("builtinSkills.allowedRolesHint")}
            </p>
            <RoleSelector
              selectedRoles={allowedRoles}
              onChange={setAllowedRoles}
            />
          </div>

          {/* Source-specific UI */}
          {addSource === "zip" ? (
            <div className="space-y-3">
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={handleDrop}
                onClick={(e) => {
                  e.stopPropagation();
                  zipInputRef.current?.click();
                }}
                className={`group relative flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-10 transition-all duration-200 ${
                  isDragging
                    ? "border-[var(--theme-primary)] bg-[var(--theme-primary-light)]/40"
                    : "border-[var(--theme-border)] bg-[var(--theme-bg)]/60 hover:border-[var(--theme-primary)]/50"
                } ${zipPreviewing ? "pointer-events-none opacity-60" : ""}`}
              >
                <input
                  ref={zipInputRef}
                  type="file"
                  accept=".zip"
                  onChange={(e) =>
                    handleZipFileChange(e.target.files?.[0] ?? null)
                  }
                  className="hidden"
                />
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--theme-primary-light)] text-[var(--theme-primary)]">
                  {isDragging ? <FileArchive size={22} /> : <UploadCloud size={22} />}
                </div>
                <div className="text-center">
                  <p className="text-sm font-medium text-[var(--theme-text)]">
                    {isDragging
                      ? t("builtinSkills.dropZoneActive")
                      : t("builtinSkills.dropZoneTitle")}
                  </p>
                  <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                    {t("builtinSkills.dropZoneHint")}
                  </p>
                </div>
                {zipFile && (
                  <div className="flex items-center gap-2 rounded-xl bg-[var(--theme-primary-light)]/60 px-3 py-1.5">
                    <Archive
                      size={14}
                      className="text-[var(--theme-primary)] shrink-0"
                    />
                    <span className="max-w-[200px] truncate text-xs font-medium text-[var(--theme-text)]">
                      {zipFile.name}
                    </span>
                  </div>
                )}
              </div>

              {zipPreviewing && (
                <div className="flex items-center justify-center gap-2 py-2 text-sm text-[var(--theme-text-secondary)]">
                  <LoadingSpinner size="sm" />
                  {t("builtinSkills.preview")}
                </div>
              )}

              {zipPreview.length > 0 && (
                <div className="space-y-1.5 rounded-xl p-1">
                  {zipPreview.map((s) => (
                    <div
                      key={s.name}
                      className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
                        s.already_exists
                          ? "cursor-not-allowed opacity-50"
                          : "bg-[var(--theme-primary)]/5"
                      }`}
                    >
                      <span className="flex-1 truncate font-medium text-[var(--theme-text)]">
                        {s.name}
                      </span>
                      {s.already_exists && (
                        <span className="shrink-0 rounded-full bg-[var(--theme-primary)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--theme-primary)]/70">
                          {t("builtinSkills.alreadyExists")}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-[var(--theme-text)]">
                {t("builtinSkills.marketplaceName")}
              </label>
              {marketplaceLoading ? (
                <div className="flex items-center gap-2 py-2 text-xs text-[var(--theme-text-secondary)]">
                  <LoadingSpinner size="sm" />
                  {t("builtinSkills.loadingMarketplace")}
                </div>
              ) : marketplaceSkills.length === 0 ? (
                <p className="py-2 text-xs text-[var(--theme-text-secondary)]">
                  {t("builtinSkills.noMarketplaceSkills")}
                </p>
              ) : (
                <select
                  value={marketplaceName}
                  onChange={(e) => setMarketplaceName(e.target.value)}
                  className="h-9 w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2 text-sm text-[var(--theme-text)] focus:border-[var(--theme-primary)] focus:outline-none"
                >
                  <option value="">
                    {t("builtinSkills.marketplaceSelectPlaceholder")}
                  </option>
                  {marketplaceSkills.map((m) => (
                    <option key={m.skill_name} value={m.skill_name}>
                      {m.skill_name}
                      {m.description ? ` — ${m.description}` : ""}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}
        </div>
      </EditorSidebar>

      {/* Edit modal */}
      <EditorSidebar
        open={!!editing}
        onClose={() => setEditing(null)}
        title={t("builtinSkills.editTitle", {
          name: editing?.skill_name ?? "",
        })}
        icon={<Pencil size={16} />}
        footer={
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setEditing(null)}
              disabled={editSubmitting}
              className="btn-secondary disabled:opacity-50"
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={handleEditSave}
              disabled={editSubmitting}
              className="btn-primary disabled:opacity-50"
            >
              {editSubmitting ? (
                <LoadingSpinner size="sm" color="text-white" />
              ) : (
                <Upload size={16} />
              )}
              <span className="hidden sm:inline">
                {t("common.save")}
              </span>
            </button>
          </div>
        }
      >
        <div className="es-form space-y-4">
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-[var(--theme-text)]">
              {t("builtinSkills.description")}
            </label>
            <textarea
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
              rows={3}
              className="w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2 text-sm text-[var(--theme-text)] focus:border-[var(--theme-primary)] focus:outline-none"
              placeholder={t("builtinSkills.descriptionPlaceholder")}
            />
          </div>
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-[var(--theme-text)]">
              {t("builtinSkills.allowedRoles")}
            </label>
            <p className="text-xs text-[var(--theme-text-secondary)]">
              {t("builtinSkills.allowedRolesHint")}
            </p>
            <RoleSelector selectedRoles={editRoles} onChange={setEditRoles} />
          </div>
        </div>
      </EditorSidebar>

      {/* Delete confirm */}
      <ConfirmDialog
        isOpen={!!deleteTarget}
        title={t("builtinSkills.confirmDelete", {
          name: deleteTarget?.skill_name ?? "",
        })}
        message={t("builtinSkills.confirmDeleteMessage", {
          name: deleteTarget?.skill_name ?? "",
        })}
        confirmText={t("common.delete")}
        cancelText={t("common.cancel")}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        variant="danger"
        loading={deleteSubmitting}
      />
    </div>
  );
}

// ===== Row =====

interface BuiltinSkillRowProps {
  skill: BuiltinSkill;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function BuiltinSkillRow({
  skill,
  onToggle,
  onEdit,
  onDelete,
}: BuiltinSkillRowProps) {
  const { t } = useTranslation();
  const SourceIcon = skill.source === "marketplace" ? ShoppingBag : Package;

  return (
    <div className="rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold text-[var(--theme-text)]">
              {skill.skill_name}
            </span>
            <span
              className={`inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                skill.source === "marketplace"
                  ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                  : "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
              }`}
            >
              <SourceIcon size={10} />
              {skill.source === "marketplace"
                ? t("builtinSkills.sourceMarketplace")
                : t("builtinSkills.sourceZip")}
            </span>
            <span
              className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                skill.is_active
                  ? "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300"
                  : "bg-stone-100 text-stone-500 dark:bg-stone-700/50 dark:text-stone-400"
              }`}
            >
              {skill.is_active
                ? t("builtinSkills.statusActive")
                : t("builtinSkills.statusInactive")}
            </span>
            <span className="shrink-0 text-[10px] text-[var(--theme-text-secondary)]">
              {t("builtinSkills.fileCount", { count: skill.file_count })}
            </span>
          </div>
          {skill.description && (
            <p className="mt-1 line-clamp-2 text-xs text-[var(--theme-text-secondary)]">
              {skill.description}
            </p>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {skill.allowed_roles.length === 0 ? (
              <span className="rounded bg-stone-100 px-1.5 py-0.5 text-[10px] text-stone-500 dark:bg-stone-700/50 dark:text-stone-400">
                {t("builtinSkills.allRoles")}
              </span>
            ) : (
              skill.allowed_roles.map((role) => (
                <span
                  key={role}
                  className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
                >
                  {role}
                </span>
              ))
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onToggle}
            className="rounded-md px-2 py-1 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-primary-light)] hover:text-[var(--theme-primary)]"
            title={
              skill.is_active
                ? t("builtinSkills.deactivate")
                : t("builtinSkills.activate")
            }
          >
            {skill.is_active
              ? t("builtinSkills.deactivate")
              : t("builtinSkills.activate")}
          </button>
          <button
            type="button"
            onClick={onEdit}
            className="rounded-md p-1.5 text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-primary-light)] hover:text-[var(--theme-primary)]"
            aria-label={t("builtinSkills.edit")}
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded-md p-1.5 text-[var(--theme-text-secondary)] transition-colors hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-900/30"
            aria-label={t("common.delete")}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
