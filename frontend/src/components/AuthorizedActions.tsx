"use client";
/**
 * Authorize browser for this task — My Browser (protocol v3.1).
 * Shown when a run needs local browser and the extension is connected.
 */
import { useState } from "react";
import { Loader2 } from "lucide-react";

type Props = {
  taskTitle?: string;
  origins?: string[];
  connected?: boolean;
  authorized?: boolean;
  onAuthorize?: (origins: string[]) => Promise<void> | void;
  onInstallExtension?: () => void;
  onOpenExtension?: () => void;
};

export default function AuthorizedActions({
  taskTitle = "This task",
  origins = [],
  connected = false,
  authorized = false,
  onAuthorize,
  onInstallExtension,
  onOpenExtension,
}: Props) {
  const [busy, setBusy] = useState(false);
  // The createBrowserSession call takes 5-10s. It needs its own
  // flag so the button always renders an explicit authorizing state
  // while the session is being created, and errors from that call
  // surface inline instead of disappearing silently.
  const [isAuthorizing, setIsAuthorizing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (authorized) {
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
        <p className="font-medium">My Browser authorized for this task</p>
        <p className="mt-1 text-xs text-emerald-800/80">
          Agent works in a task-named tab group. Overlay: Agent working · Take over · Resume · Stop. Closing the group stops browser work.
        </p>
      </div>
    );
  }

  if (!connected) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        <p className="font-medium">Connect My Browser</p>
        <p className="mt-1 text-xs text-amber-900/80">
          Install the Onsite48 Browser extension and click Connect in the popup. No paste-token required.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {onInstallExtension && (
            <button
              type="button"
              onClick={onInstallExtension}
              className="rounded-lg bg-amber-900 px-3 py-1.5 text-xs font-semibold text-white"
            >
              Install extension
            </button>
          )}
          {onOpenExtension && (
            <button
              type="button"
              onClick={onOpenExtension}
              className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-950"
            >
              Open extension
            </button>
          )}
        </div>
      </div>
    );
  }

  const handleAuthorize = async () => {
    if (!onAuthorize) return;
    setBusy(true);
    setIsAuthorizing(true);
    setError(null);
    try {
      await onAuthorize(origins);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Authorization failed");
    } finally {
      setBusy(false);
      setIsAuthorizing(false);
    }
  };

  const authorizing = busy || isAuthorizing;

  return (
    <div className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-950">
      <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">My Browser</p>
      <p className="mt-1 font-medium">Authorize browser for this task</p>
      <p className="mt-1 text-xs text-indigo-900/80">
        {taskTitle}
        {origins.length > 0 ? ` · origins: ${origins.slice(0, 3).join(", ")}${origins.length > 3 ? "…" : ""}` : ""}
      </p>
      <p className="mt-2 text-xs text-indigo-800/70">
        Creates a Chrome tab group named after the task. You can watch, take over, resume, or close the group to stop.
      </p>
      {error && <p className="mt-2 text-xs font-medium text-red-600">{error}</p>}
      <button
        type="button"
        disabled={authorizing || !onAuthorize}
        onClick={handleAuthorize}
        className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
      >
        {authorizing ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
        {isAuthorizing ? "Authorizing…" : busy ? "Authorizing…" : "Authorize browser for this task"}
      </button>
    </div>
  );
}
