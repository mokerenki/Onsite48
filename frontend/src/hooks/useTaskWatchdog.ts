/**
 * Silence + transport-death detection for an active task stream.
 * Server pings prove the socket is alive; they do NOT count as progress.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export const SILENCE_THRESHOLD_MS = 45_000;
export const RECONNECT_GRACE_MS = 10_000;
export const STATUS_POLL_INTERVAL_MS = 5_000;
export const STATUS_POLL_MAX_ATTEMPTS = 6;

const TRANSPORT_ONLY_TYPES = new Set(["pong", "ping"]);

export type WatchdogPhase = "idle" | "running" | "silent" | "recovering" | "terminal";

export interface UseTaskWatchdogOptions {
  active: boolean;
  lastTaskEventType: string | null;
  taskEventSeq: number;
  onSilent?: () => void;
  onTransportDeath?: () => void;
}

export interface UseTaskWatchdogResult {
  phase: WatchdogPhase;
  silenceNotice: boolean;
  clearSilenceNotice: () => void;
  markTerminal: () => void;
  markRecovering: () => void;
}

export function isTaskProgressEventType(type: string | undefined | null): boolean {
  if (!type) return false;
  return !TRANSPORT_ONLY_TYPES.has(type);
}

export function useTaskWatchdog(opts: UseTaskWatchdogOptions): UseTaskWatchdogResult {
  const { active, taskEventSeq, onSilent, onTransportDeath } = opts;
  const [phase, setPhase] = useState<WatchdogPhase>("idle");
  const [silenceNotice, setSilenceNotice] = useState(false);
  const silenceFiredRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSilentRef = useRef(onSilent);
  const onDeathRef = useRef(onTransportDeath);
  onSilentRef.current = onSilent;
  onDeathRef.current = onTransportDeath;

  const clearSilenceNotice = useCallback(() => {
    setSilenceNotice(false);
    silenceFiredRef.current = false;
  }, []);

  const markTerminal = useCallback(() => {
    setPhase("terminal");
    setSilenceNotice(false);
    silenceFiredRef.current = false;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const markRecovering = useCallback(() => {
    setPhase("recovering");
    onDeathRef.current?.();
  }, []);

  useEffect(() => {
    if (!active) {
      setPhase("idle");
      setSilenceNotice(false);
      silenceFiredRef.current = false;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      return;
    }

    setPhase((p) => (p === "terminal" || p === "recovering" ? p : "running"));
    silenceFiredRef.current = false;
    setSilenceNotice(false);

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      if (silenceFiredRef.current) return;
      silenceFiredRef.current = true;
      setSilenceNotice(true);
      setPhase("silent");
      onSilentRef.current?.();
    }, SILENCE_THRESHOLD_MS);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [active, taskEventSeq]);

  return { phase, silenceNotice, clearSilenceNotice, markTerminal, markRecovering };
}
