import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Network, Save, Wifi } from "lucide-react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";

import { settingsApi } from "../../services/api";
import type {
  WeComNetworkConfig,
  WeComNetworkConfigUpdate,
  WeComNetworkMode,
  WeComNetworkOperationResponse,
} from "../../types";
import { GlassSelect } from "../common/GlassSelect";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { PanelLoadingState } from "../common/PanelLoadingState";

interface Props {
  canManage: boolean;
}

const OFFICIAL_WEBSOCKET_URL = "wss://openws.work.weixin.qq.com";

function toForm(config: WeComNetworkConfig): WeComNetworkConfigUpdate {
  return {
    mode: config.mode,
    websocket_url: config.websocket_url,
    media_gateway_url: config.media_gateway_url,
    forward_proxy_url: config.forward_proxy_url,
    forward_proxy_username: config.forward_proxy_username,
    forward_proxy_password: "",
    clear_forward_proxy_password: false,
    expected_revision: config.revision,
    ca_bundle_path: config.ca_bundle_path,
    connect_timeout_seconds: config.connect_timeout_seconds,
    media_download_timeout_seconds: config.media_download_timeout_seconds,
    media_max_bytes: config.media_max_bytes,
  };
}

export function WeComNetworkSettings({ canManage }: Props) {
  const { t } = useTranslation();
  const [config, setConfig] = useState<WeComNetworkConfig | null>(null);
  const [form, setForm] = useState<WeComNetworkConfigUpdate | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<"test" | "save" | null>(null);
  const [result, setResult] = useState<WeComNetworkOperationResponse | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const current = await settingsApi.getWeComNetwork();
      setConfig(current);
      setForm(toForm(current));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = <K extends keyof WeComNetworkConfigUpdate>(
    key: K,
    value: WeComNetworkConfigUpdate[K],
  ) => {
    setForm((current) => (current ? { ...current, [key]: value } : current));
  };

  const runAction = async (kind: "test" | "save") => {
    if (!form) return;
    setAction(kind);
    setResult(null);
    try {
      const response =
        kind === "test"
          ? await settingsApi.testWeComNetwork(form)
          : await settingsApi.updateWeComNetwork(form);
      setResult(response);
      if (kind === "save") {
        setConfig(response.config);
        setForm(toForm(response.config));
      }
      const failed =
        response.status === "test_failed" ||
        response.status === "rolled_back";
      (failed ? toast.error : toast.success)(
        t(`wecomNetwork.status.${response.status}`),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.error"));
    } finally {
      setAction(null);
    }
  };

  if (loading || !form || !config) {
    return <PanelLoadingState text={t("settings.loading")} />;
  }

  const inputClass =
    "mt-1 w-full rounded-lg border border-[var(--glass-border)] bg-[var(--theme-bg-card)] px-3 py-2 text-sm text-stone-900 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60 dark:text-stone-100";
  const fieldDisabled = !canManage || action !== null;
  const resultIsWarning =
    result?.status === "partial_failure" ||
    result?.status === "rolled_back" ||
    result?.status === "test_failed";

  return (
    <div className="space-y-4">
      <div className="glass-card rounded-xl p-4">
        <div className="flex items-start gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
            <Network size={20} />
          </div>
          <div>
            <h3 className="font-semibold text-stone-900 dark:text-stone-100">
              {t("wecomNetwork.title")}
            </h3>
            <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
              {t("wecomNetwork.description")}
            </p>
          </div>
        </div>
      </div>

      <div className="glass-card space-y-4 rounded-xl p-4">
        <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
          {t("wecomNetwork.mode")}
          <div className="mt-1">
            <GlassSelect
              value={form.mode}
              disabled={fieldDisabled}
              onChange={(value) =>
                update("mode", value as WeComNetworkMode)
              }
              options={[
                { value: "direct", label: t("wecomNetwork.modes.direct") },
                {
                  value: "reverse_gateway",
                  label: t("wecomNetwork.modes.reverse_gateway"),
                },
                {
                  value: "forward_proxy",
                  label: t("wecomNetwork.modes.forward_proxy"),
                },
              ]}
            />
          </div>
        </label>

        {form.mode === "direct" && (
          <div className="rounded-lg bg-[var(--glass-bg-subtle)] px-3 py-2 text-sm text-stone-500 dark:text-stone-400">
            {t("wecomNetwork.directHint", {
              url: OFFICIAL_WEBSOCKET_URL,
            })}
          </div>
        )}

        {form.mode === "reverse_gateway" && (
          <>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
              {t("wecomNetwork.websocketUrl")}
              <input
                className={inputClass}
                value={form.websocket_url}
                disabled={fieldDisabled}
                placeholder="wss://dmz.example.com/wecom/ws"
                onChange={(event) =>
                  update("websocket_url", event.target.value)
                }
              />
            </label>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
              {t("wecomNetwork.mediaGatewayUrl")}
              <input
                className={inputClass}
                value={form.media_gateway_url}
                disabled={fieldDisabled}
                placeholder="https://dmz.example.com/wecom/media"
                onChange={(event) =>
                  update("media_gateway_url", event.target.value)
                }
              />
              <span className="mt-1 block text-xs font-normal text-stone-400">
                {t("wecomNetwork.mediaGatewayHint")}
              </span>
            </label>
          </>
        )}

        {form.mode === "forward_proxy" && (
          <>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
              {t("wecomNetwork.proxyUrl")}
              <input
                className={inputClass}
                value={form.forward_proxy_url}
                disabled={fieldDisabled}
                placeholder="http://dmz-proxy.example.com:3128"
                onChange={(event) =>
                  update("forward_proxy_url", event.target.value)
                }
              />
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                {t("wecomNetwork.proxyUsername")}
                <input
                  className={inputClass}
                  value={form.forward_proxy_username}
                  disabled={fieldDisabled}
                  onChange={(event) =>
                    update("forward_proxy_username", event.target.value)
                  }
                />
              </label>
              <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                {t("wecomNetwork.proxyPassword")}
                <input
                  type="password"
                  className={inputClass}
                  value={form.forward_proxy_password ?? ""}
                  disabled={fieldDisabled}
                  placeholder={
                    config.has_forward_proxy_password
                      ? t("wecomNetwork.passwordPreserved")
                      : ""
                  }
                  onChange={(event) =>
                    update("forward_proxy_password", event.target.value)
                  }
                />
              </label>
            </div>
            {config.has_forward_proxy_password && (
              <label className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-400">
                <input
                  type="checkbox"
                  checked={form.clear_forward_proxy_password}
                  disabled={fieldDisabled}
                  onChange={(event) =>
                    update(
                      "clear_forward_proxy_password",
                      event.target.checked,
                    )
                  }
                />
                {t("wecomNetwork.clearPassword")}
              </label>
            )}
          </>
        )}

        <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
          {t("wecomNetwork.caBundlePath")}
          <input
            className={inputClass}
            value={form.ca_bundle_path}
            disabled={fieldDisabled}
            placeholder="/etc/kunxiaozhi/certs/bank-ca.pem"
            onChange={(event) => update("ca_bundle_path", event.target.value)}
          />
          <span className="mt-1 block text-xs font-normal text-stone-400">
            {t("wecomNetwork.caBundleHint")}
          </span>
        </label>

        <div className="grid gap-4 sm:grid-cols-3">
          {(
            [
              ["connect_timeout_seconds", "connectTimeout"],
              ["media_download_timeout_seconds", "mediaTimeout"],
              ["media_max_bytes", "mediaMaxBytes"],
            ] as const
          ).map(([key, label]) => (
            <label
              key={key}
              className="block text-sm font-medium text-stone-700 dark:text-stone-300"
            >
              {t(`wecomNetwork.${label}`)}
              <input
                type="number"
                min={key === "media_max_bytes" ? 1024 : 1}
                className={inputClass}
                value={form[key]}
                disabled={fieldDisabled}
                onChange={(event) => update(key, Number(event.target.value))}
              />
            </label>
          ))}
        </div>

        {canManage && (
          <div className="flex flex-wrap gap-2 border-t border-[var(--glass-border)] pt-4">
            <button
              className="btn-secondary flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50"
              disabled={action !== null}
              onClick={() => void runAction("test")}
            >
              {action === "test" ? <LoadingSpinner size="sm" /> : <Wifi size={16} />}
              {t("wecomNetwork.test")}
            </button>
            <button
              className="btn-primary flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50"
              disabled={action !== null}
              onClick={() => void runAction("save")}
            >
              {action === "save" ? <LoadingSpinner size="sm" /> : <Save size={16} />}
              {t("wecomNetwork.saveAndReconnect")}
            </button>
          </div>
        )}
      </div>

      {result && (
        <div
          className={`rounded-xl border p-4 ${
            resultIsWarning
              ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
              : "border-green-200 bg-green-50 text-green-900 dark:border-green-900/60 dark:bg-green-950/30 dark:text-green-200"
          }`}
        >
          <div className="flex items-center gap-2 font-medium">
            {resultIsWarning ? (
              <AlertTriangle size={18} />
            ) : (
              <CheckCircle2 size={18} />
            )}
            {t(`wecomNetwork.status.${result.status}`)}
          </div>
          {result.detail && <p className="mt-2 text-sm">{result.detail}</p>}
          {result.results.length > 0 && (
            <div className="mt-3 space-y-2">
              {result.results.map((bot) => (
                <div
                  key={`${bot.node_id ?? ""}:${bot.preset_id}`}
                  className="rounded-lg bg-white/60 px-3 py-2 text-sm dark:bg-black/20"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{bot.aibotid}</span>
                    <span>{bot.state}</span>
                  </div>
                  {(bot.reason_code || bot.reason_detail) && (
                    <p className="mt-1 break-words text-xs opacity-80">
                      {[bot.reason_code, bot.reason_detail]
                        .filter(Boolean)
                        .join(": ")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
