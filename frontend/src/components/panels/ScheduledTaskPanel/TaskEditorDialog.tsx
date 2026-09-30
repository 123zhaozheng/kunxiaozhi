import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { ArrowLeft, Sparkles, X } from "lucide-react";
import { scheduledTaskApi } from "../../../services/api/scheduledTask";
import type {
  ScheduleSpec,
  ScheduledTask,
  ScheduledTaskTemplate,
} from "../../../types/scheduledTask";
import { ScheduleEditor } from "./ScheduleEditor";
import { DEFAULT_TIMEZONE, cleanSpec, runAtForInput, specForKind } from "./scheduleUtils";

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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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
      const body = {
        name: name.trim(),
        prompt: prompt.trim(),
        schedule: cleanSpec(schedule),
        timezone: DEFAULT_TIMEZONE,
      };
      const saved = isEdit
        ? await scheduledTaskApi.update(task.id, body)
        : await scheduledTaskApi.create({ ...body, template_id: templateId });
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
      </div>
    </div>
  );
}
