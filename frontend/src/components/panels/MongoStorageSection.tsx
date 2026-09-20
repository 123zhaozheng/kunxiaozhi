import { useCallback, useEffect, useState } from "react";
import type { TFunction } from "i18next";
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Database,
  HardDrive,
  Layers,
  RefreshCw,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LoadingSpinner } from "../common/LoadingSpinner";
import {
  healthApi,
  type MongoStorageCollection,
  type MongoStorageDiagnostics,
} from "../../services/api/health";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { formatBytes } from "./mongoStorageFormat";

const CHECKPOINT_COLLECTION_NAME = "checkpoints";
const CHECKPOINT_WRITES_COLLECTION_NAME = "checkpoint_writes";

type MongoStorageStatus = "stable" | "degraded" | "unavailable";

function StatusBadge({ status }: { status: MongoStorageStatus }) {
  const { t } = useTranslation();

  if (status === "stable") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-700 dark:bg-green-900/40 dark:text-green-400">
        <Activity size={12} />
        {t("mongoStorage.available", "Available")}
      </span>
    );
  }

  if (status === "degraded") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">
        <AlertTriangle size={12} />
        {t("mongoStorage.degraded", "Partially unavailable")}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-stone-100 px-2.5 py-0.5 text-xs font-medium text-stone-500 dark:bg-stone-800 dark:text-stone-400">
      {t("mongoStorage.unavailable", "Unavailable")}
    </span>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-2.5 rounded-lg bg-[var(--glass-bg-subtle)] px-3 py-2">
      <Icon size={16} className="shrink-0 text-stone-400 dark:text-stone-500" />
      <div className="min-w-0">
        <p className="text-[11px] text-stone-400 dark:text-stone-500">{label}</p>
        <p className="text-sm font-medium tabular-nums text-stone-700 dark:text-stone-200">
          {value}
        </p>
      </div>
    </div>
  );
}

function formatCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString()
    : "-";
}

function formatDays(value: number | null | undefined, t: TFunction): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return t("mongoStorage.retentionDays", "{{count}} days", { count: value });
}

function formatHours(value: number | null | undefined, t: TFunction): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return t("mongoStorage.intervalHours", "{{count}} hours", { count: value });
}

function unavailableReason(reason: string | null | undefined, t: TFunction): string {
  return reason?.trim() || t("mongoStorage.notAvailable", "Not available");
}

function getCollection(
  collections: MongoStorageCollection[] | undefined,
  name: string,
): MongoStorageCollection | null {
  return collections?.find((collection) => collection.name === name) ?? null;
}

function getStorageStatus(
  diagnostics: MongoStorageDiagnostics,
): MongoStorageStatus {
  if (!diagnostics.available) return "unavailable";

  // Degraded means a metric could not be read. A disabled checkpoint backend is a
  // deliberate configuration, not a failure, so it must not raise the warning badge.
  const hasUnreadableBlock =
    !diagnostics.database ||
    diagnostics.database.available === false ||
    !diagnostics.cleanup ||
    diagnostics.cleanup.available === false ||
    diagnostics.collections.some((collection) => collection.available === false);

  return hasUnreadableBlock ? "degraded" : "stable";
}

function UnavailablePlaceholder({
  reason,
  t,
}: {
  reason?: string | null;
  t: TFunction;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-[var(--glass-bg-subtle)] px-3 py-2 text-xs text-stone-500 dark:text-stone-400">
      <StatusBadge status="unavailable" />
      <span>{unavailableReason(reason, t)}</span>
    </div>
  );
}

function CollectionCard({
  collection,
  label,
  t,
  primary = false,
}: {
  collection: MongoStorageCollection | null;
  label: string;
  t: TFunction;
  primary?: boolean;
}) {
  const isAvailable = collection?.available === true;

  return (
    <div
      className={`rounded-lg border bg-[var(--glass-bg)] p-3 ${
        primary
          ? "border-blue-200 dark:border-blue-900/60"
          : "border-[var(--glass-border)]"
      }`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Database size={14} className="shrink-0 text-blue-500 dark:text-blue-400" />
          <span className="truncate text-xs font-semibold text-stone-700 dark:text-stone-200">
            {label}
          </span>
        </div>
        <StatusBadge status={isAvailable ? "stable" : "unavailable"} />
      </div>

      {isAvailable && collection ? (
        <div className="grid grid-cols-2 gap-2">
          <MetricCard
            icon={Database}
            label={t("mongoStorage.logicalSize", "Logical size")}
            value={formatBytes(collection.size)}
          />
          <MetricCard
            icon={HardDrive}
            label={t("mongoStorage.storageSize", "Storage size")}
            value={formatBytes(collection.storage_size)}
          />
          <MetricCard
            icon={Layers}
            label={t("mongoStorage.indexSize", "Index size")}
            value={formatBytes(collection.total_index_size)}
          />
          <MetricCard
            icon={Activity}
            label={t("mongoStorage.documents", "Documents")}
            value={formatCount(collection.count)}
          />
        </div>
      ) : (
        <UnavailablePlaceholder reason={collection?.error} t={t} />
      )}
    </div>
  );
}

export function MongoStorageSection() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const [diagnostics, setDiagnostics] =
    useState<MongoStorageDiagnostics | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const canView = hasPermission(Permission.SETTINGS_MANAGE);

  const fetchStorage = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await healthApi.mongodbStorage();
      setDiagnostics(data);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : t("mongoStorage.fetchFailed", "Failed to fetch MongoDB storage data"),
      );
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (canView) {
      fetchStorage();
    }
  }, [canView, fetchStorage]);

  if (!canView) return null;

  const status: MongoStorageStatus | null = error
    ? "unavailable"
    : diagnostics
      ? getStorageStatus(diagnostics)
      : null;
  const database = diagnostics?.database;
  const cleanup = diagnostics?.cleanup;
  const checkpoints = getCollection(
    diagnostics?.collections,
    CHECKPOINT_COLLECTION_NAME,
  );
  const checkpointWrites = getCollection(
    diagnostics?.collections,
    CHECKPOINT_WRITES_COLLECTION_NAME,
  );
  const otherCollections =
    diagnostics?.collections.filter(
      (collection) =>
        collection.name !== CHECKPOINT_COLLECTION_NAME &&
        collection.name !== CHECKPOINT_WRITES_COLLECTION_NAME,
    ) ?? [];

  const databaseStorage =
    database?.available === false ? "-" : formatBytes(database?.storage_size);
  const checkpointStorage =
    checkpoints?.available === false
      ? "-"
      : formatBytes(checkpoints?.storage_size);
  const checkpointWritesStorage =
    checkpointWrites?.available === false
      ? "-"
      : formatBytes(checkpointWrites?.storage_size);

  return (
    <div className="mb-4 rounded-xl border border-[var(--glass-border)] bg-[var(--glass-bg-subtle)]">
      <div
        onClick={() => setExpanded((previous) => !previous)}
        className="flex w-full cursor-pointer items-center justify-between px-4 py-3 select-none"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setExpanded((previous) => !previous);
          }
        }}
      >
        <div className="flex items-center gap-3">
          <div className="flex size-8 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-100 to-teal-50 text-emerald-600 dark:from-emerald-900/50 dark:to-teal-900/30 dark:text-emerald-400">
            <Database size={16} />
          </div>
          <div>
            <span className="text-sm font-semibold text-stone-800 dark:text-stone-100">
              {t("mongoStorage.title", "MongoDB Storage")}
            </span>
            {status && (
              <div className="mt-0.5">
                <StatusBadge status={status} />
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label={t("mongoStorage.refresh", "Refresh MongoDB storage")}
            onClick={(event) => {
              event.stopPropagation();
              fetchStorage();
            }}
            disabled={isLoading}
            className="rounded-lg p-1.5 text-stone-400 transition-colors hover:bg-[var(--glass-bg)] hover:text-stone-600 disabled:opacity-50 dark:text-stone-500 dark:hover:text-stone-300"
          >
            <RefreshCw size={14} className={isLoading ? "animate-spin" : ""} />
          </button>
          {expanded ? (
            <ChevronUp size={16} className="text-stone-400" />
          ) : (
            <ChevronDown size={16} className="text-stone-400" />
          )}
        </div>
      </div>

      {!expanded && (diagnostics || error) && (
        <div className="border-t border-[var(--glass-border)] px-4 py-2">
          {error ? (
            <p className="text-xs text-stone-500 dark:text-stone-400">
              {t(
                "mongoStorage.fetchFailed",
                "Failed to fetch MongoDB storage data",
              )}
            </p>
          ) : (
            <p className="text-xs text-stone-500 dark:text-stone-400">
              {t("mongoStorage.databaseStorageSize", "Database storage")}: {databaseStorage} &middot; {t("mongoStorage.checkpoints", "Checkpoints")}: {checkpointStorage} &middot; {t("mongoStorage.checkpointWrites", "Checkpoint writes")}: {checkpointWritesStorage}
            </p>
          )}
        </div>
      )}

      {expanded && (
        <div className="border-t border-[var(--glass-border)] px-4 py-3">
          {isLoading && !diagnostics && (
            <div className="flex items-center justify-center py-6">
              <LoadingSpinner size="sm" />
            </div>
          )}

          {error && (
            <div className="mb-4 flex items-center gap-2 rounded-lg bg-[var(--glass-bg)] px-3 py-2 text-sm text-stone-500 dark:text-stone-400">
              <StatusBadge status="unavailable" />
              <span>{error}</span>
            </div>
          )}

          {diagnostics && (
            <div className="space-y-4">
              {!diagnostics.available && (
                <UnavailablePlaceholder
                  reason={database?.error ?? cleanup?.error}
                  t={t}
                />
              )}

              {diagnostics.checkpoint_backend?.enabled === false && (
                <UnavailablePlaceholder
                  reason={t(
                    "mongoStorage.checkpointBackendUnavailable",
                    "Checkpoint backend is disabled",
                  )}
                  t={t}
                />
              )}

              <section>
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-stone-400">
                  {t("mongoStorage.database", "Database")}
                  {database?.name ? ` · ${database.name}` : ""}
                </h4>
                {database && database.available !== false ? (
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                    <MetricCard
                      icon={Database}
                      label={t("mongoStorage.databaseDataSize", "Data size")}
                      value={formatBytes(database.data_size)}
                    />
                    <MetricCard
                      icon={HardDrive}
                      label={t(
                        "mongoStorage.databaseStorageSize",
                        "Storage size",
                      )}
                      value={formatBytes(database.storage_size)}
                    />
                    <MetricCard
                      icon={Layers}
                      label={t("mongoStorage.databaseIndexSize", "Index size")}
                      value={formatBytes(database.index_size)}
                    />
                    <MetricCard
                      icon={Activity}
                      label={t("mongoStorage.objects", "Objects")}
                      value={formatCount(database.objects)}
                    />
                    <MetricCard
                      icon={Layers}
                      label={t(
                        "mongoStorage.databaseCollections",
                        "Collections",
                      )}
                      value={formatCount(database.collections)}
                    />
                  </div>
                ) : (
                  <UnavailablePlaceholder reason={database?.error} t={t} />
                )}
              </section>

              <section>
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-blue-500 dark:text-blue-400">
                  {t("mongoStorage.checkpointStorage", "Checkpoint storage")}
                </h4>
                <div className="grid gap-2 sm:grid-cols-2">
                  <CollectionCard
                    collection={checkpoints}
                    label={t("mongoStorage.checkpoints", "Checkpoints")}
                    primary
                    t={t}
                  />
                  <CollectionCard
                    collection={checkpointWrites}
                    label={t(
                      "mongoStorage.checkpointWrites",
                      "Checkpoint writes",
                    )}
                    primary
                    t={t}
                  />
                </div>
              </section>

              {otherCollections.length > 0 && (
                <section>
                  <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-stone-400">
                    {t("mongoStorage.otherCollections", "Other collections")}
                  </h4>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {otherCollections.map((collection) => (
                      <CollectionCard
                        key={collection.name}
                        collection={collection}
                        label={collection.name}
                        t={t}
                      />
                    ))}
                  </div>
                </section>
              )}

              <section>
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-stone-400">
                  {t("mongoStorage.cleanup", "Checkpoint cleanup")}
                </h4>
                {cleanup && cleanup.available !== false ? (
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <MetricCard
                      icon={Activity}
                      label={t("mongoStorage.cleanupStatus", "Status")}
                      value={cleanup.enabled
                        ? t("mongoStorage.enabled", "Enabled")
                        : t("mongoStorage.disabled", "Disabled")}
                    />
                    <MetricCard
                      icon={Layers}
                      label={t("mongoStorage.retentionLabel", "Retention")}
                      value={formatDays(cleanup.retention_days, t)}
                    />
                    <MetricCard
                      icon={RefreshCw}
                      label={t("mongoStorage.intervalLabel", "Interval")}
                      value={formatHours(cleanup.interval_hours, t)}
                    />
                    <MetricCard
                      icon={HardDrive}
                      label={t("mongoStorage.backlogSessions", "Backlog sessions")}
                      value={
                        cleanup.approximate &&
                        typeof cleanup.backlog_sessions === "number" &&
                        Number.isFinite(cleanup.backlog_sessions)
                          ? t("mongoStorage.estimated", "~{{value}}", {
                              value: formatCount(cleanup.backlog_sessions),
                            })
                          : formatCount(cleanup.backlog_sessions)
                      }
                    />
                  </div>
                ) : (
                  <UnavailablePlaceholder reason={cleanup?.error} t={t} />
                )}
              </section>
            </div>
          )}

          {!isLoading && !diagnostics && !error && (
            <UnavailablePlaceholder t={t} />
          )}
        </div>
      )}
    </div>
  );
}
