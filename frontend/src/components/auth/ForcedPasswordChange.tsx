import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogOut } from "lucide-react";
import { AuthLayout } from "./AuthLayout";
import { PasswordInput } from "./PasswordInput";
import { PasswordRequirementsHelp } from "./PasswordRequirementsHelp";
import { authApi } from "../../services/api";
import { useAuth } from "../../hooks/useAuth";
import { passwordPolicyError } from "./passwordPolicy";

export function ForcedPasswordChange() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function handleLogout() {
    logout();
    navigate("/auth/login", { replace: true });
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (passwordPolicyError(password)) {
      setError(t("auth.validation.passwordPolicy", "Use 12-64 characters with at least three character types."));
      return;
    }
    if (confirm !== password) {
      setError(t("auth.validation.passwordMismatch"));
      return;
    }
    setBusy(true);
    try {
      await authApi.changePassword("", password);
      logout();
      navigate("/auth/login", { replace: true });
    } catch (err) {
      setError((err as Error).message || t("auth.operationFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <form onSubmit={submit} className="auth-panel space-y-5 p-6 sm:p-8">
        <div>
          <h1 className="text-xl font-semibold text-stone-900 dark:text-stone-100">
            {t("auth.changePasswordRequired", "Set a new password")}
          </h1>
          <p className="mt-2 text-sm text-stone-500 dark:text-stone-400">
            {t("auth.changePasswordRequiredHint", "Choose a strong password to continue.")}
          </p>
        </div>
        <div>
          <div className="mb-1.5 flex flex-wrap items-center justify-between text-sm font-medium text-stone-700 dark:text-stone-300">
            <span>{t("auth.newPassword")}</span>
            <PasswordRequirementsHelp context="forced" />
          </div>
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" placeholder={t("auth.passwordPlaceholder")} />
        </div>
        <PasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" placeholder={t("auth.confirmPasswordPlaceholder")} />
        {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
        <div className="flex flex-col gap-2 sm:flex-row">
          <button
            disabled={busy}
            className="auth-primary-button min-h-12 w-full flex-1 rounded-xl py-3 text-sm font-medium transition-all disabled:cursor-not-allowed disabled:opacity-50"
            type="submit"
          >
            {busy ? t("common.loading") : t("auth.changePassword", "Change password")}
          </button>
          <button
            className="auth-secondary-button min-h-12 w-full flex-1 rounded-xl py-3 text-sm font-medium"
            type="button"
            onClick={handleLogout}
          >
            <span className="inline-flex items-center justify-center gap-2">
              <LogOut size={16} aria-hidden="true" />
              {t("auth.logout")}
            </span>
          </button>
        </div>
      </form>
    </AuthLayout>
  );
}
