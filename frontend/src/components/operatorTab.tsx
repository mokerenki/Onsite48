"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Pause, Play, RotateCcw, X } from "lucide-react";
import { authHeaders, useAuth } from "@/components/AuthProvider";
import { AgentComputerWorkspace } from "@/components/AgentComputerWorkspace";

type FeedEvent = { id: string; sequence: number; type: string; data: Record<string, unknown>; timestamp: string };
type Feed = { task_title: string; task_status: string; events: FeedEvent[] };

function approvalDetails(event: FeedEvent) {
  const tool = String(event.data.tool_name || "requested action");
  const args = (event.data.tool_args as Record<string, unknown>) || {};
  const target = String(args.to || args.url || args.recipient || args.path || args.subject || "the connected service");
  const irreversible = /send|delete|remove|trash|publish|create_event|update|upload/i.test(tool);
  return { tool, target, impact: irreversible ? "This may change external state." : "This is a read or reversible action." };
}

export default function OperatorTab({ threadId }: { threadId: string }) {
  const { accessToken } = useAuth();
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!accessToken || !threadId) return;
    const response = await fetch(`/api/operator/tasks/${encodeURIComponent(threadId)}/feed?limit=100`, { credentials: "include", headers: authHeaders(accessToken) });
    if (!response.ok) throw new Error("The operator feed could not be loaded.");
    setFeed(await response.json() as Feed);
  }, [accessToken, threadId]);

  useEffect(() => {
    void refresh().catch((err) => setError(err instanceof Error ? err.message : "Unable to load operator feed."));
    const timer = window.setInterval(() => { void refresh().catch(() => undefined); }, 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

async function control(action: "pause" | "resume" | "approve" | "reject", event?: FeedEvent) {
    if (!accessToken) return;
    const data = event
        ? {
            action,
            tool_name: String(event.data.tool_name || ""),
            step_index: Number(event.data.step_index || 0),
            tool_args: (event.data.tool_args as Record<string, unknown>) || undefined,
        }
        : { action };
    const response = await fetch(`/api/operator/tasks/${encodeURIComponent(threadId)}/control`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, body: JSON.stringify(data) });
    if (!response.ok) { const body = await response.json().catch(() => null); setError(body?.detail || "The operator action was rejected."); return; }
    await refresh();
}

  const pending = feed?.events.filter((event) => event.type === "needs_confirmation") || [];
  return <div className="flex h-full min-h-0 flex-col bg-[#f8f8f6]">
    <header className="flex shrink-0 items-center justify-between border-b border-black/10 bg-white px-5 py-3">
      <div><h1 className="text-sm font-semibold">Operator workspace</h1><p className="text-xs text-text-muted">{feed?.task_title || "Loading task…"} · {feed?.task_status || "unknown"}</p></div>
      <div className="flex gap-2">
        {feed?.task_status === "paused" ? <button onClick={() => void control("resume")} className="inline-flex items-center gap-1 rounded-lg bg-emerald-700 px-3 py-2 text-xs font-semibold text-white"><Play className="h-3.5 w-3.5" /> Resume</button> : <button onClick={() => void control("pause")} className="inline-flex items-center gap-1 rounded-lg bg-slate-800 px-3 py-2 text-xs font-semibold text-white"><Pause className="h-3.5 w-3.5" /> Pause</button>}
        <button onClick={() => void refresh()} className="rounded-lg border border-black/10 bg-white p-2"><RotateCcw className="h-3.5 w-3.5" /></button>
      </div>
    </header>
    {error && <div className="m-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">{error}</div>}
    <div className="grid min-h-0 flex-1 gap-4 overflow-auto p-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.9fr)]">
      <section className="min-h-[420px] rounded-xl border border-black/10 bg-white shadow-sm"><div className="border-b border-black/10 px-4 py-3 text-xs font-semibold">Live progress and task feed</div><div className="divide-y divide-black/5">{feed?.events.map((event) => <div key={event.id} className="px-4 py-3 text-xs"><div className="flex items-center justify-between gap-3"><span className="font-semibold">{event.type}</span><time className="text-text-muted">{new Date(event.timestamp).toLocaleTimeString()}</time></div>{event.type === "needs_confirmation" ? (() => { const details = approvalDetails(event); return <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-950"><p className="font-semibold">{details.tool}</p><p className="mt-1">Target: {details.target}</p><p className="mt-1 text-amber-800">{details.impact}</p><div className="mt-2 flex gap-2"><button onClick={() => void control("approve", event)} className="inline-flex items-center gap-1 rounded bg-emerald-700 px-2 py-1 font-semibold text-white"><Check className="h-3 w-3" /> Approve</button><button onClick={() => void control("reject", event)} className="inline-flex items-center gap-1 rounded border border-rose-200 px-2 py-1 font-semibold text-rose-700"><X className="h-3 w-3" /> Reject</button></div></div>; })() : <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap text-[11px] text-text-secondary">{JSON.stringify(event.data, null, 2)}</pre>}</div>)}</div></section>
      <section className="min-h-[420px] overflow-hidden rounded-xl border border-black/10 bg-white shadow-sm"><AgentComputerWorkspace threadId={threadId} /></section>
    </div>
    {pending.length > 0 && <div className="fixed bottom-4 right-4 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900 shadow-lg">{pending.length} approval request{pending.length === 1 ? "" : "s"} waiting</div>}
  </div>;
}