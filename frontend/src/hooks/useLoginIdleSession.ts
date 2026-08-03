import { useEffect, useRef } from "react";
import { activityApi } from "../services/api/activity";
import { getAccessToken } from "../services/api/token";

const ACTIVITY_THROTTLE_MS = 60_000;
const STATUS_INTERVAL_MS = 60_000;

/** Synchronize explicit browser activity with the server idle session. */
export function useLoginIdleSession(enabled: boolean): void {
  const lastSent = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    if (!enabled || !getAccessToken()) {
      lastSent.current = 0;
      return () => {
        mounted.current = false;
        lastSent.current = 0;
      };
    }

    const sendActivity = () => {
      if (!mounted.current || document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastSent.current < ACTIVITY_THROTTLE_MS) return;
      lastSent.current = now;
      void activityApi.touch().catch(() => {
        // 503/network errors are retryable; authFetch handles 401 relogin.
      });
    };
    const checkStatus = () => {
      if (document.visibilityState !== "visible" || !mounted.current) return;
      void activityApi.get().catch(() => undefined);
    };
    const events = ["pointerdown", "keydown", "touchstart", "wheel"];
    events.forEach((event) => window.addEventListener(event, sendActivity, { passive: true }));
    const interval = window.setInterval(checkStatus, STATUS_INTERVAL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") checkStatus(); };
    document.addEventListener("visibilitychange", onVisible);
    const onStorage = (event: StorageEvent) => {
      if (event.key !== "access_token" && event.key !== "refresh_token") return;
      if (!event.newValue && !getAccessToken()) {
        window.dispatchEvent(new CustomEvent("auth:logout"));
        return;
      }
      // A login or refresh in another tab starts a fresh throttle window.
      if (event.key === "access_token" && event.newValue) lastSent.current = 0;
    };
    window.addEventListener("storage", onStorage);
    checkStatus();
    return () => {
      mounted.current = false;
      lastSent.current = 0;
      events.forEach((event) => window.removeEventListener(event, sendActivity));
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("storage", onStorage);
    };
  }, [enabled]);
}

export default useLoginIdleSession;
