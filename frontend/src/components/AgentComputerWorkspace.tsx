"use client";

import { FormEvent, MouseEvent, useCallback, useEffect, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ExternalLink,
  Globe2,
  Keyboard,
  Loader2,
  MousePointer2,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { authHeaders, useAuth } from "@/components/AuthProvider";

type Snapshot = {
  url: string;
  title: string;
  screenshot_base64: string;
  scroll_y: number;
  scroll_height: number;
  viewport_height: number;
  viewport_width: number;
  can_scroll: boolean;
};

type ControlMode = "agent" | "user" | "agent_ready";

type ControlState = {
  mode?: ControlMode;
  session_active?: boolean;
  durable_control?: { mode?: ControlMode; changed_at?: string } | null;
};

type ActionResult = Partial<Snapshot> & {
  control?: ControlState;
  durable_control?: { mode?: ControlMode; changed_at?: string } | null;
  task_status?: string;
  resume_required?: boolean;
};

function asSnapshot(result: ActionResult, previous: Snapshot | null): Snapshot | null {
  if (!result.screenshot_base64) return previous;
  return {
    url: result.url || previous?.url || "",
    title: result.title || previous?.title || "",
    screenshot_base64: result.screenshot_base64,
    scroll_y: result.scroll_y ?? previous?.scroll_y ?? 0,
    scroll_height: result.scroll_height ?? previous?.scroll_height ?? 0,
    viewport_height: result.viewport_height ?? previous?.viewport_height ?? 0,
    viewport_width: result.viewport_width ?? previous?.viewport_width ?? 0,
    can_scroll: result.can_scroll ?? previous?.can_scroll ?? false,
  };
}

export function AgentComputerWorkspace({
  threadId,
  onUserControlChange,
}: {
  threadId: string;
  onUserControlChange?: (active: boolean) => void;
}) {
  const { accessToken } = useAuth();
  const [url, setUrl] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [controlMode, setControlMode] = useState<ControlMode>("agent");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualText, setManualText] = useState("");

  const applyResult = useCallback((result: ActionResult): boolean => {
    if (result.screenshot_base64) {
      setSnapshot((previous) => asSnapshot(result, previous));
      if (result.url) setUrl(result.url === "about:blank" ? "" : result.url);
    }
    const mode = result.control?.mode || result.durable_control?.mode;
    if (mode === "agent" || mode === "user" || mode === "agent_ready") {
      setControlMode(mode);
      onUserControlChange?.(mode === "user");
    }
    return Boolean(result.screenshot_base64);
  }, [onUserControlChange]);

  const request = useCallback(async (path: string, body?: Record<string, unknown>) => {
    if (!accessToken) throw new Error("Sign in again to use Agent Computer.");
    const response = await fetch(`/api/computer/${threadId}/${path}`, {
      method: path === "snapshot" || path === "control" ? "GET" : "POST",
      credentials: "include",
      headers: body ? { "Content-Type": "application/json", ...authHeaders(accessToken) } : authHeaders(accessToken),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "The isolated browser action could not be completed.");
    }
    return (await response.json()) as ActionResult;
  }, [accessToken, threadId]);

  const refresh = useCallback(async () => {
    if (!accessToken || !threadId) return;
    setLoading(true);
    try {
      const control = await request("control");
      applyResult(control);
      const next = await request("snapshot");
      applyResult(next);
      if (next.url) setUrl(next.url === "about:blank" ? "" : next.url);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to open the agent computer.");
    } finally {
      setLoading(false);
    }
  }, [accessToken, applyResult, request, threadId]);

  useEffect(() => {
    setSnapshot(null);
    setControlMode("agent");
    setManualText("");
    void refresh();
  }, [refresh, threadId]);

  const runAction = useCallback(async (path: string, body?: Record<string, unknown>) => {
    setLoading(true);
    try {
      const result = await request(path, body);
      const hasFreshScreenshot = applyResult(result);
      if (!hasFreshScreenshot) await refresh();
      setError(null);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : "The isolated browser action could not be completed.");
      return null;
    } finally {
      setLoading(false);
    }
  }, [applyResult, refresh, request]);

  async function navigate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!url.trim()) return;
    const prefix = controlMode === "user" ? "user/" : "";
    await runAction(`${prefix}navigate`, { url: url.trim() });
  }

  async function takeOver() {
    const result = await runAction("takeover");
    if (result) {
      setControlMode("user");
      onUserControlChange?.(true);
    }
  }

  async function returnToOnsite48() {
    const result = await runAction("return");
    if (result) {
      setControlMode("agent_ready");
      onUserControlChange?.(false);
    }
  }

  async function screenshotClick(event: MouseEvent<HTMLImageElement>) {
    if (controlMode !== "user" || loading) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const x = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    const y = Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height));
    await runAction("user/click", { x, y, normalized: true });
  }

  async function typeManualText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!manualText) return;
    // The backend returns only text_length and never stores this value in task
    // events. Clear this client-side field immediately after the request ends.
    const text = manualText;
    setManualText("");
    await runAction("user/type", { text });
  }

  const userAction = useCallback(async (path: "reload" | "back" | "scroll", body?: Record<string, unknown>) => {
    await runAction(`${controlMode === "user" ? "user/" : ""}${path}`, body);
  }, [controlMode, runAction]);

  const positionLabel = snapshot && snapshot.can_scroll
    ? `Viewport ${snapshot.scroll_y.toLocaleString()}–${Math.min(snapshot.scroll_y + snapshot.viewport_height, snapshot.scroll_height).toLocaleString()} of ${snapshot.scroll_height.toLocaleString()} px`
    : "This page fits within the current browser viewport";
  const isUserControlling = controlMode === "user";
  const isAgentReady = controlMode === "agent_ready";

  return (
    <section className="flex h-full min-h-0 flex-col bg-[#f6f7f9] text-[#1b1b1b]">
      <div className="shrink-0 border-b border-black/10 bg-white p-3">
        <div className={`mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-xs ${isUserControlling ? "border-blue-200 bg-blue-50 text-blue-950" : isAgentReady ? "border-emerald-200 bg-emerald-50 text-emerald-950" : "border-slate-200 bg-slate-50 text-slate-700"}`}>
          <div className="flex min-w-0 items-start gap-2">
            {isUserControlling ? <UserRound className="mt-0.5 h-4 w-4 shrink-0 text-blue-700" /> : <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" />}
            <div>
              <p className="font-semibold">{isUserControlling ? "You are controlling Agent Computer" : isAgentReady ? "Ready to return to Onsite48" : "Agent-controlled isolated browser"}</p>
              <p className="mt-0.5 leading-5">{isUserControlling ? "Click the page image, type using the secure input below, or use the keyboard shortcuts. Credentials and verification responses are not saved in task history." : isAgentReady ? "The browser state is preserved. Close this panel and select Resume in the task workspace when you want Onsite48 to continue." : "Take over to personally sign in, complete CAPTCHA or MFA, and use the page. Onsite48 cannot act while you are in control."}</p>
            </div>
          </div>
          {isUserControlling ? (
            <button type="button" onClick={() => void returnToOnsite48()} disabled={loading} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-blue-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-800 disabled:opacity-50"><RotateCcw className="h-3.5 w-3.5" /> Return to Onsite48</button>
          ) : (
            <button type="button" onClick={() => void takeOver()} disabled={loading || !snapshot} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-black disabled:opacity-50"><MousePointer2 className="h-3.5 w-3.5" /> {isAgentReady ? "Take over again" : "Take over"}</button>
          )}
        </div>
        <form onSubmit={navigate} className="flex gap-2">
          <div className="flex h-9 flex-1 items-center gap-2 rounded-lg border border-black/10 bg-[#fafafa] px-2.5">
            <Globe2 className="h-4 w-4 text-[#777]" />
            <input value={url} onChange={(event) => setUrl(event.target.value)} disabled={loading || isAgentReady} className="min-w-0 flex-1 bg-transparent text-xs outline-none disabled:opacity-50" placeholder={isUserControlling ? "Open a public website while you control the browser" : "Open a public website in the agent computer"} />
          </div>
          <button disabled={loading || !url.trim() || isAgentReady} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-[#171717] px-3 text-xs font-medium text-white disabled:opacity-50"><ExternalLink className="h-3.5 w-3.5" /> Open</button>
          <button type="button" title="Reload current page" disabled={loading || !snapshot || isAgentReady} onClick={() => void userAction("reload")} className="grid h-9 w-9 place-items-center rounded-lg border border-black/10 bg-white text-[#555] hover:bg-[#f5f5f3] disabled:opacity-40"><RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /></button>
        </form>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="flex items-center gap-1.5 text-[11px] text-[#70706c]"><ShieldCheck className="h-3.5 w-3.5 text-green-600" /> Task-isolated browser. Private and local network addresses are blocked; the task context closes on cancel, failure, timeout, and task deletion.</p>
          {snapshot && <p className="text-[11px] text-[#70706c]">{positionLabel}</p>}
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col p-4">
        {/* A gray pulse placeholder covers the panel until the first
            snapshot arrives, so the workspace never flashes blank white
            while the isolated browser mounts. */}
        {loading && !snapshot ? <div className="relative grid h-full place-items-center overflow-hidden rounded-xl border border-black/10 bg-white"><div className="absolute inset-0 animate-pulse bg-[#e7e7ec]" /><div className="relative flex items-center gap-2 text-xs text-[#777]"><Loader2 className="h-3.5 w-3.5 animate-spin" />Opening the isolated browser…</div></div> : error ? <div className="mx-auto mt-12 max-w-md rounded-xl border border-amber-200 bg-amber-50 p-4 text-center text-sm leading-6 text-amber-800">{error}</div> : snapshot ? <>
          <div className="mb-3 flex shrink-0 items-center justify-between gap-2">
            <button type="button" onClick={() => void userAction("back")} disabled={loading || isAgentReady} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-black/10 bg-white px-2.5 text-xs font-medium text-[#444] hover:bg-[#f5f5f3] disabled:opacity-50"><ArrowLeft className="h-3.5 w-3.5" /> Back</button>
            <div className="flex items-center gap-1.5">
              <button type="button" title="Scroll page up" onClick={() => void userAction("scroll", { direction: "up" })} disabled={loading || isAgentReady || !snapshot.can_scroll || snapshot.scroll_y <= 0} className="grid h-8 w-8 place-items-center rounded-lg border border-black/10 bg-white text-[#444] hover:bg-[#f5f5f3] disabled:opacity-40"><ArrowUp className="h-3.5 w-3.5" /></button>
              <button type="button" title="Scroll page down" onClick={() => void userAction("scroll", { direction: "down" })} disabled={loading || isAgentReady || !snapshot.can_scroll || snapshot.scroll_y + snapshot.viewport_height >= snapshot.scroll_height} className="grid h-8 w-8 place-items-center rounded-lg border border-black/10 bg-white text-[#444] hover:bg-[#f5f5f3] disabled:opacity-40"><ArrowDown className="h-3.5 w-3.5" /></button>
            </div>
          </div>
          {isUserControlling && (
            <div className="mb-3 rounded-xl border border-blue-200 bg-white p-3 shadow-sm">
              <form onSubmit={typeManualText} className="flex flex-col gap-2 sm:flex-row">
                <label className="min-w-0 flex-1 text-xs font-medium text-blue-950"><span className="mb-1 block">Type into the focused field</span><input type="text" value={manualText} onChange={(event) => setManualText(event.target.value)} autoComplete="off" maxLength={512} placeholder="Click a field on the page first; password and MFA values are never recorded." className="h-9 w-full rounded-lg border border-blue-200 bg-blue-50/40 px-2.5 text-sm outline-none focus:border-blue-500" /></label>
                <button disabled={loading || !manualText} className="mt-auto inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-blue-700 px-3 text-xs font-semibold text-white hover:bg-blue-800 disabled:opacity-50"><Keyboard className="h-3.5 w-3.5" /> Type</button>
              </form>
              <div className="mt-2 flex flex-wrap gap-1.5"><span className="mr-1 self-center text-[11px] text-blue-800">Keys</span>{["Tab", "Enter", "Escape", "Backspace", "ArrowUp", "ArrowDown"].map((key) => <button type="button" key={key} disabled={loading} onClick={() => void runAction("user/key", { key })} className="rounded-md border border-blue-200 bg-white px-2 py-1 text-[11px] font-medium text-blue-800 hover:bg-blue-50 disabled:opacity-50">{key}</button>)}</div>
            </div>
          )}
          <div className={`min-h-0 flex-1 overflow-auto rounded-xl border bg-white shadow-sm ${isUserControlling ? "border-blue-300" : "border-black/10"}`}>
            <div className="sticky top-0 z-10 border-b border-black/10 bg-white/95 px-3 py-2 text-xs text-[#70706c] backdrop-blur">{snapshot.title || snapshot.url}</div>
            <img onClick={(event) => void screenshotClick(event)} src={`data:image/png;base64,${snapshot.screenshot_base64}`} alt="Current viewport in the isolated agent browser" className={`block h-auto w-full ${isUserControlling ? "cursor-crosshair" : ""}`} />
          </div>
        </> : <div className="grid h-full place-items-center text-center text-sm text-[#777]">The agent computer is ready. Open a public URL here or ask Onsite48 to research the web.</div>}
      </div>
    </section>
  );
}