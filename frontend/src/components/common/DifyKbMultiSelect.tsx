import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen, Check, ChevronDown, Plus, X } from "lucide-react";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { settingsApi } from "../../services/api";
import type { DifyKbDataset } from "../../services/api/settings";

interface DifyKbMultiSelectProps {
  /** Selected Dify knowledge-base ids. */
  value: string[];
  /** Called with the new id list whenever the selection changes. */
  onChange: (ids: string[]) => void;
  /** Disable interaction (e.g. when the user lacks settings:manage). */
  disabled?: boolean;
}

/**
 * Multi-select picker for Dify knowledge bases.
 *
 * Loads the available datasets from the backend Dify proxy
 * (`settingsApi.listDifyKbDatasets`), shows selected ids as removable chips
 * with their display names, and lets the user toggle datasets in a dropdown.
 * Reused by both the persona editor and the system settings page so the two
 * configuration surfaces stay consistent.
 *
 * Renders nothing when the Dify KB feature is disabled — callers gate this
 * component on `DIFY_KB_ENABLED` themselves (same as the persona editor).
 */
export function DifyKbMultiSelect({
  value,
  onChange,
  disabled = false,
}: DifyKbMultiSelectProps) {
  const { t } = useTranslation();
  const [datasets, setDatasets] = useState<DifyKbDataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Load Dify KB datasets for the picker. Re-runs every time the dropdown
  // opens so newly created knowledge bases show up without a page reload.
  useEffect(() => {
    if (!open) {
      setDatasets([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await settingsApi.listDifyKbDatasets();
        if (!cancelled) setDatasets(result);
      } catch {
        // Feature not configured / network error — leave the picker empty.
        if (!cancelled) setDatasets([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  // Close the dropdown on outside click.
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (open && dropdownRef.current && !dropdownRef.current.contains(target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const toggle = (id: string) => {
    if (disabled) return;
    onChange(value.includes(id) ? value.filter((n) => n !== id) : [...value, id]);
  };

  const remove = (id: string) => {
    if (disabled) return;
    onChange(value.filter((n) => n !== id));
  };

  const nameFor = (id: string) => datasets.find((d) => d.id === id)?.name || id;

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        className={`ppe-skill-trigger ${open ? "ppe-skill-trigger--open" : ""}`}
      >
        {value.length > 0 ? (
          <span className="ppe-skill-trigger__count">
            <BookOpen size={12} />
            {t("personaPresets.difyKbCount", "{{count}} 个知识库已选择", {
              count: value.length,
            })}
          </span>
        ) : (
          <span className="ppe-skill-trigger__placeholder">
            {t("personaPresets.difyKbPlaceholder", "选择知识库...")}
          </span>
        )}
        <ChevronDown
          size={14}
          className={`ppe-skill-trigger__chevron ${open ? "rotate-180" : ""}`}
        />
      </button>

      {value.length > 0 && !open && (
        <div className="ppe-skill-selected-area">
          {value.map((id) => (
            <span key={id} className="ppe-skill-chip">
              {nameFor(id)}
              {!disabled && (
                <X
                  size={11}
                  className="ppe-skill-chip-remove"
                  onClick={() => remove(id)}
                />
              )}
            </span>
          ))}
        </div>
      )}

      {open && (
        <div className="ppe-skill-dropdown">
          {loading ? (
            <div className="flex items-center justify-center py-4">
              <LoadingSpinner size="sm" />
            </div>
          ) : datasets.length > 0 ? (
            <>
              {value.length > 0 && (
                <div className="ppe-skill-selected-bar">
                  {value.map((id) => (
                    <span key={id} className="ppe-skill-chip">
                      {nameFor(id)}
                      <X
                        size={11}
                        className="ppe-skill-chip-remove"
                        onClick={() => remove(id)}
                      />
                    </span>
                  ))}
                </div>
              )}
              <div className="ppe-skill-dropdown__list">
                {datasets.map((ds) => {
                  const isSelected = value.includes(ds.id);
                  return (
                    <button
                      key={ds.id}
                      type="button"
                      onClick={() => toggle(ds.id)}
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
                          {ds.name}
                        </div>
                        {ds.description && (
                          <div className="text-[11px] text-[var(--theme-text-secondary)] truncate mt-0.5">
                            {ds.description}
                          </div>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="ppe-skill-dropdown__empty">
              <BookOpen size={20} className="ppe-skill-dropdown__empty-icon" />
              <span>
                {t(
                  "personaPresets.difyKbEmpty",
                  "未找到知识库，请在系统设置中配置 Dify 连接",
                )}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
