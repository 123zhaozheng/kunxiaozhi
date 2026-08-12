import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AuthLayout } from "./AuthLayout";
import { PasswordInput } from "./PasswordInput";
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

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (passwordPolicyError(password)) {
      setError(t("auth.validation.passwordPolicy", "Use 12-64 characters without surrounding spaces."));
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
        <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" placeholder={t("auth.passwordPlaceholder")} />
        <PasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" placeholder={t("auth.confirmPasswordPlaceholder")} />
        {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
        <button disabled={busy} className="auth-button w-full" type="submit">{busy ? t("common.loading") : t("auth.changePassword", "Change password")}</button>
      </form>
    </AuthLayout>
  );
}
