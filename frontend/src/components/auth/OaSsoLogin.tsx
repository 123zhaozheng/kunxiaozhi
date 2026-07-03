/**
 * OA portal deep-link silent login with staged progress UI.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertCircle } from "lucide-react";
import { useAuth } from "../../hooks/useAuth";
import { authApi, getRedirectPath, clearRedirectPath } from "../../services/api";
import {
  AUTH_REDIRECT_ANIMATION_MS,
  resolvePostAuthRedirectPath,
} from "./authRedirectTransition";
import { OaSsoProgress } from "./OaSsoProgress";
import { BrandWordmark } from "../common/BrandWordmark";
import { LanguageToggle } from "../common/LanguageToggle";
import { ThemeToggle } from "../common/ThemeToggle";
import { APP_NAME } from "../../constants";

function readOaToken(params: URLSearchParams): string | null {
  return params.get("token") || params.get("oa_token");
}

function stripTokenFromUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("token");
  url.searchParams.delete("oa_token");
  const next = `${url.pathname}${url.search}${url.hash}`;
  window.history.replaceState({}, "", next);
}

function mapErrorToQuery(detail: string): string {
  if (detail.includes("尚未开通") || detail.includes("管理员")) {
    return "oa_sso_no_account";
  }
  if (detail.includes("过期") || detail.includes("凭证")) {
    return "oa_sso_expired";
  }
  return "oa_sso_failed";
}

export function OaSsoLogin() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { refreshUser } = useAuth();
  const [missingToken, setMissingToken] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [statusLine, setStatusLine] = useState("");
  const [fatalMessage, setFatalMessage] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.classList.add("allow-scroll");
    return () => document.documentElement.classList.remove("allow-scroll");
  }, []);

  useEffect(() => {
    const token = readOaToken(searchParams);
    if (!token?.trim()) {
      setMissingToken(true);
      setActiveStep(0);
      return;
    }

    setMissingToken(false);
    stripTokenFromUrl();
    setStatusLine(t("auth.oaSso.signingInHint"));

    const run = async () => {
      try {
        setActiveStep(0);
        await new Promise((r) => window.setTimeout(r, 400));
        setActiveStep(1);
        setStatusLine(t("auth.oaSso.stepVerify"));

        const loginPromise = authApi.loginWithOaSso(token.trim());
        await new Promise((r) => window.setTimeout(r, 450));
        setActiveStep(2);
        setStatusLine(t("auth.oaSso.stepAccount"));

        await loginPromise;

        setActiveStep(3);
        setStatusLine(t("auth.oaSso.stepEnter"));
        await new Promise((r) => window.setTimeout(r, 500));

        setActiveStep(4);
        await refreshUser();

        const redirectPath = resolvePostAuthRedirectPath(
          getRedirectPath() ?? null,
        );
        clearRedirectPath();
        window.setTimeout(() => {
          navigate(redirectPath, { replace: true });
        }, AUTH_REDIRECT_ANIMATION_MS);
      } catch (err) {
        const detail =
          (err as Error).message || t("auth.oaSso.failedGeneric");
        const code = mapErrorToQuery(detail);
        if (code === "oa_sso_no_account") {
          setFatalMessage(detail);
          return;
        }
        navigate(`/auth/login?error=${code}`, { replace: true });
      }
    };

    void run();
  }, [navigate, refreshUser, searchParams, t]);

  return (
    <div className="auth-shell safe-area-top safe-area-bottom min-h-[100dvh] overflow-hidden">
      <div className="auth-crosshatch" aria-hidden />
      <div className="auth-atmosphere" aria-hidden>
        <div className="auth-glow-main absolute -top-24 left-1/2 h-[520px] w-[720px] -translate-x-1/2 bg-[radial-gradient(ellipse_at_center,rgba(20,184,166,0.08)_0%,transparent_70%)]" />
      </div>

      <nav className="safe-area-top fixed inset-x-0 top-0 z-50 border-b border-stone-100/60 bg-white/90 dark:border-stone-800/40 dark:bg-stone-950/90">
        <div className="mx-auto flex h-14 items-center justify-between px-4 sm:px-8">
          <Link to="/" className="flex items-center gap-1.5">
            <img src="/images/lamb.webp" alt={APP_NAME} className="h-8" />
            <BrandWordmark decorative className="h-8 w-auto text-stone-900 dark:text-stone-100" />
          </Link>
          <div className="flex items-center gap-1.5">
            <LanguageToggle />
            <ThemeToggle />
          </div>
        </div>
      </nav>

      <div className="relative z-10 flex min-h-[100dvh] flex-col items-center justify-center px-6 pb-16 pt-20">
        {missingToken ? (
          <div className="auth-panel max-w-sm rounded-2xl p-8 text-center">
            <AlertCircle className="mx-auto mb-4 text-amber-500" size={32} />
            <h1 className="mb-2 text-lg font-semibold text-stone-900 dark:text-stone-50">
              {t("auth.oaSso.portalOnly")}
            </h1>
            <p className="mb-6 text-sm text-stone-500 dark:text-stone-400">
              {t("auth.oaSso.portalOnlyHint")}
            </p>
            <Link
              to="/auth/login"
              className="inline-flex rounded-xl bg-stone-900 px-5 py-2.5 text-sm font-medium text-white dark:bg-stone-100 dark:text-stone-900"
            >
              {t("auth.goToLogin")}
            </Link>
          </div>
        ) : fatalMessage ? (
          <div className="auth-panel max-w-sm rounded-2xl p-8 text-center">
            <p className="mb-6 text-sm text-amber-800 dark:text-amber-200">
              {fatalMessage}
            </p>
            <Link
              to="/auth/login?error=oa_sso_no_account"
              className="text-sm font-medium text-teal-600 dark:text-teal-400"
            >
              {t("auth.goToLogin")}
            </Link>
          </div>
        ) : (
          <>
            <OaSsoProgress activeIndex={Math.min(activeStep, 3)} />
            <p className="mt-6 text-center text-sm text-stone-600 dark:text-stone-400">
              {statusLine || t("auth.oaSso.signingIn")}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

export default OaSsoLogin;