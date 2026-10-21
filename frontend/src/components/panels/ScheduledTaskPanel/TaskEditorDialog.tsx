import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { ArrowLeft, Sparkles, X } from "lucide-react";
import { scheduledTaskApi } from "../../../services/api/scheduledTask";
import { useAuth } from "../../../hooks/useAuth";
import { usePersonaPresets } from "../../../hooks/usePersonaPresets";
import { Permission, type PersonaPreset } from "../../../types";
import type {
  ScheduleSpec,
  ScheduledTask,
  ScheduledTaskTemplate,
} from "../../../types/scheduledTask";
import { PersonaPresetSelector } from "../../persona/PersonaPresetSelector";
import { PersonaAvatarBadge } from "./PersonaAvatarBadge";
import { ScheduleEditor } from "./ScheduleEditor";
import { specForKind, runAtForInput } from "./scheduleUtils";
import {
  buildTaskSaveBody,
  personaChoiceFromPreset,
  personaChoiceFromTask,
  presetToSnapshot,
  type PersonaChoice,
} from "./taskEditorPayload";

const PERSONA_PAGE_SIZE = 12;

interface TaskEditorDialogProps {
  task: ScheduledTask | null;
  templates: ScheduledTaskTemplate[];
  initialTemplate?: ScheduledTaskTemplate | null;
  onClose: () => void;
  onSaved: (task: ScheduledTask) => void;
}

type Step = "template" | "form";

/** Two-step wizard: pick a template (create only) -> fill in what + when. */
export function TaskEditorDialog({
  task,
  templates,
  initialTemplate,
  onClose,
  onSaved,
}: TaskEditorDialogProps) {
  const { t } = useTranslation();
  const isEdit = !!task;
  const [step, setStep] = useState<Step>(isEdit || initialTemplate ? "form" : "template");
  const [name, setName] = useState(task?.name ?? initialTemplate?.name ?? "");
  const [prompt, setPrompt] = useState(task?.prompt ?? initialTemplate?.prompt ?? "");
  const [templateId, setTemplateId] = useState<string | null>(
    task?.template_id ?? initialTemplate?.id ?? null,
  );
  const [schedule, setSchedule] = useState<ScheduleSpec>(() => {
    const src = task?.schedule ?? initialTemplate?.schedule;
    if (!src) return specForKind("weekdays");
    if (src.kind === "once") return { ...src, run_at: runAtForInput(src.run_at) };
    return src;
  });
  const [scheduleValid, setScheduleValid] = useState(false);
  const [saving, setSaving] = useState(false);

  const { hasPermission } = useAuth();
  const canReadPersona = hasPermission(Permission.PERSONA_PRESET_READ);
  const [persona, setPersona] = useState<PersonaChoice | null>(() =>
    personaChoiceFromTask(task),
  );
  const [personaOpen, setPersonaOpen] = useState(false);
  const [personaPage, setPersonaPage] = useState(1);
  const [personaQuery, setPersonaQuery] = useState("");
  const [personaTag, setPersonaTag] = useState<string | null>(null);
  const personaListParams = useMemo(
    () => ({
      skip: (personaPage - 1) * PERSONA_PAGE_SIZE,
      limit: PERSONA_PAGE_SIZE,
      q: personaQuery.trim() || undefined,
      tag: personaTag || undefined,
    }),
    [personaPage, personaQuery, personaTag],
  );
  const {
    presets: personaPresets,
    total: personaTotal,
    isLoading: personaLoading,
    isMutating: personaMutating,
    copyPreset: copyPersonaPreset,
  } = usePersonaPresets({ enabled: canReadPersona, listParams: personaListParams });

  const handlePersonaSearch = useCallback((q: string) => setPersonaQuery(q), []);
  const handlePersonaTag = useCallback((tag: string | null) => setPersonaTag(tag), []);
  // Only the stored expert can be flagged as gone; a freshly picked one is visible by definition.
  const personaUnavailable =
    !!persona && persona.id === task?.persona_preset_id && task?.persona_available === false;

  useEffect(() => {
    // Escape belongs to the expert picker while it is open.
    if (personaOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, personaOpen]);

  const applyTemplate = (tpl: ScheduledTaskTemplate | null) => {
    if (tpl) {
      setName(tpl.name);
      setPrompt(tpl.prompt);
      setTemplateId(tpl.id);
      setSchedule(
        tpl.schedule.kind === "once" ? specForKind("once") : tpl.schedule,
      );
    }
    setStep("form");
  };

  const canSave =
    name.trim().length > 0 && prompt.trim().length > 0 && scheduleValid && !saving;

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      const body = buildTaskSaveBody({
        name,
        prompt,
        schedule,
        persona,
        templateId,
        isEdit,
      });
      const saved = isEdit
        ? await scheduledTaskApi.update(task.id, body)
        : await scheduledTaskApi.create(body);
      toast.success(
        isEdit
          ? t("scheduledTask.toast.updated", "已保存修改")
          : t("scheduledTask.toast.created", "定时任务已创建，到点会自动执行"),
      );
      onSaved(saved);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl dark:bg-stone-900 sm:max-w-2xl sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-stone-100 px-5 py-4 dark:border-stone-800">
          <div className="flex items-center gap-2">
            {step === "form" && !isEdit && !initialTemplate && (
              <button
                type="button"
                onClick={() => setStep("template")}
                className="rounded-lg p-1 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
                aria-label={t("common.back", "返回")}
              >
                <ArrowLeft size={18} />
              </button>
            )}
            <h2 className="text-base font-semibold text-stone-900 dark:text-stone-100">
              {isEdit
                ? t("scheduledTask.editTitle", "编辑定时任务")
                : step === "template"
                  ? t("scheduledTask.pickTemplate", "选择一个场景开始")
                  : t("scheduledTask.createTitle", "新建定时任务")}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
            aria-label={t("common.close", "关闭")}
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {step === "template" ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {templates.map((tpl) => (
                <button
                  key={tpl.id}
                  type="button"
                  onClick={() => applyTemplate(tpl)}
                  className="rounded-2xl border border-stone-200 p-4 text-left transition hover:border-stone-400 hover:shadow-sm dark:border-stone-700 dark:hover:border-stone-500"
                >
                  <div className="font-medium text-stone-900 dark:text-stone-100">{tpl.title}</div>
                  <div className="mt-1 text-sm text-stone-500 dark:text-stone-400">
                    {tpl.description}
                  </div>
                </button>
              ))}
              <button
                type="button"
                onClick={() => applyTemplate(null)}
                className="flex items-center gap-2 rounded-2xl border border-dashed border-stone-300 p-4 text-left text-stone-600 hover:border-stone-500 dark:border-stone-600 dark:text-stone-300"
              >
                <Sparkles size={18} />
                <div>
                  <div className="font-medium">{t("scheduledTask.blank", "从空白开始")}</div>
                  <div className="text-sm text-stone-500 dark:text-stone-400">
                    {t("scheduledTask.blankHint", "自己写要让昆小智做的事")}
                  </div>
                </div>
              </button>
            </div>
          ) : (
            <div className="space-y-6">
              <section className="space-y-3">
                <div className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                  {t("scheduledTask.step1", "第 1 步：要让昆小智做什么？")}
                </div>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={100}
                  placeholder={t("scheduledTask.namePlaceholder", "给任务起个名字，例如：每日晨报")}
                  className="w-full rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-sm dark:border-stone-700 dark:bg-stone-900"
                />
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  maxLength={8000}
                  rows={6}
                  placeholder={t(
                    "scheduledTask.promptPlaceholder",
                    "像平时聊天一样写下要求，例如：帮我整理今天需要跟进的客户事项，分点列出。",
                  )}
                  className="w-full resize-y rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-sm leading-relaxed dark:border-stone-700 dark:bg-stone-900"
                />
                <p className="text-xs text-stone-500 dark:text-stone-400">
                  {t(
                    "scheduledTask.promptHint",
                    "到点后昆小智会像你亲自发送这段话一样执行，结果会通知你，也可以在“运行记录”中查看。",
                  )}
                </p>
                {canReadPersona && (
                  <div
                    data-testid="task-persona-row"
                    className="rounded-xl border border-stone-200 px-3 py-2.5 dark:border-stone-700"
                  >
                    <div className="mb-1.5 text-xs font-medium text-stone-600 dark:text-stone-300">
                      {t("scheduledTask.persona.label", "执行专家（可选）")}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {persona ? (
                        <span className="inline-flex min-w-0 items-center gap-2 text-sm text-stone-900 dark:text-stone-100">
                          <PersonaAvatarBadge avatar={persona.avatar} size={24} />
                          <span className="truncate">
                            {persona.name ||
                              t("scheduledTask.persona.unavailable", "专家已不可用")}
                          </span>
                        </span>
                      ) : (
                        <span className="text-sm text-stone-500 dark:text-stone-400">
                          {t(
                            "scheduledTask.persona.none",
                            "不指定，由昆小智默认助手执行",
                          )}
                        </span>
                      )}
                      <div className="ml-auto flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => setPersonaOpen(true)}
                          className="rounded-lg border border-stone-200 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-800"
                        >
                          {persona
                            ? t("scheduledTask.persona.change", "更换专家")
                            : t("scheduledTask.persona.choose", "选择专家")}
                        </button>
                        {persona && (
                          <button
                            type="button"
                            onClick={() => setPersona(null)}
                            className="rounded-lg px-3 py-1.5 text-xs text-stone-500 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800"
                          >
                            {t("scheduledTask.persona.clear", "清除")}
                          </button>
                        )}
                      </div>
                    </div>
                    {personaUnavailable && (
                      <p
                        role="alert"
                        className="mt-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 dark:bg-amber-900/30 dark:text-amber-200"
                      >
                        {t(
                          "scheduledTask.persona.unavailableHint",
                          "所选专家已被删除、下架或你已无权使用，请编辑任务更换专家",
                        )}
                      </p>
                    )}
                    <p className="mt-1.5 text-xs text-stone-500 dark:text-stone-400">
                      {t(
                        "scheduledTask.persona.hint",
                        "选择后，到点会按该专家的角色设定、技能和知识库来执行。",
                      )}
                    </p>
                  </div>
                )}
              </section>
              <section className="space-y-3">
                <div className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                  {t("scheduledTask.step2", "第 2 步：什么时候做？")}
                </div>
                <ScheduleEditor
                  value={schedule}
                  onChange={setSchedule}
                  onValidityChange={setScheduleValid}
                />
              </section>
            </div>
          )}
        </div>

        {step === "form" && (
          <div className="flex justify-end gap-2 border-t border-stone-100 px-5 py-4 dark:border-stone-800">
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl px-4 py-2 text-sm text-stone-600 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800"
            >
              {t("common.cancel", "取消")}
            </button>
            <button
              type="button"
              disabled={!canSave}
              onClick={save}
              className="rounded-xl bg-stone-900 px-5 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-stone-100 dark:text-stone-900"
            >
              {saving
                ? t("common.saving", "保存中...")
                : isEdit
                  ? t("common.save", "保存")
                  : t("scheduledTask.createSubmit", "创建定时任务")}
            </button>
          </div>
        )}
        {canReadPersona && (
          <PersonaPresetSelector
            presets={personaPresets}
            total={personaTotal}
            page={personaPage}
            selectedPresetId={persona?.id ?? null}
            isOpen={personaOpen}
            isLoading={personaLoading}
            isMutating={personaMutating}
            canManagePresets={false}
            onOpenChange={setPersonaOpen}
            onPageChange={setPersonaPage}
            onSearchChange={handlePersonaSearch}
            onTagChange={handlePersonaTag}
            onUsePreset={async (preset: PersonaPreset) => {
              // Selection only: the run itself counts the usage, not the editor.
              setPersona(personaChoiceFromPreset(preset));
              return presetToSnapshot(preset);
            }}
            onCopyPreset={async (preset: PersonaPreset) => {
              await copyPersonaPreset(preset.id);
            }}
            onClearPreset={() => {
              setPersona(null);
              setPersonaOpen(false);
            }}
          />
        )}
      </div>
    </div>
  );
}
