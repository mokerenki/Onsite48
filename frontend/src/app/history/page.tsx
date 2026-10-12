"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Check, Clock3, Copy, Loader2, MessageSquareText, PlayCircle, Trash2 } from "lucide-react";
import { authHeaders, useAuth } from "@/components/AuthProvider";
import { AuthScreen } from "@/components/AuthScreen";

type Task = {
  thread_id: string;
  title: string;
  prompt: string;
  domain: string;
  status: string;
  final_answer?: string | null;
  cost_metrics: { total_cost_usd?: number };
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
};

function statusClass(status: string) {
  if (status === "completed") return "bg-green-50 text-green-700";
  if (status === "failed") return "bg-red-50 text-red-700";
  if (status === "cancelled") return "bg-gray-100 text-gray-600";
  if (status === "paused" || status === "waiting_for_approval") return "bg-amber-50 text-amber-700";
  return "bg-blue-50 text-blue-700";
}

function taskCanBeDeleted(task: Task) {
  return ["completed", "failed", "cancelled"].includes(task.status);
}

export default function HistoryPage() {
  const { accessToken, user } = useAuth();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Task | null>(null);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    if (!accessToken) return;
    setLoading(true);
    try {
      const response = await fetch("/api/tasks?limit=100", { headers: authHeaders(accessToken), credentials: "include" });
      if (!response.ok) throw new Error("Unable to load task history.");
      const payload = (await response.json()) as Task[];
      setTasks(payload);
      setSelected((current) => current ? payload.find((task) => task.thread_id === current.thread_id) ?? null : payload[0] ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load history.");
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  const deleteTask = useCallback(async (task: Task) => {
    if (!accessToken || !taskCanBeDeleted(task)) return;
    if (!window.confirm(`Permanently delete ${task.title}? This removes its conversation, steps, approvals, and answer. This cannot be undone.`)) return;

    setError(null);
    setDeletingThreadId(task.thread_id);
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(task.thread_id)}`, {
        method: "DELETE",
        headers: authHeaders(accessToken),
        credentials: "include",
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail || "Unable to delete this task.");
      }
      setTasks((previous) => {
        const remaining = previous.filter((item) => item.thread_id !== task.thread_id);
        setSelected((current) => current?.thread_id === task.thread_id ? remaining[0] ?? null : current);
        return remaining;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to delete this task.");
    } finally {
      setDeletingThreadId(null);
    }
  }, [accessToken]);

  const copyFinalAnswer = useCallback(async () => {
    const answer = selected?.final_answer;
    if (!answer?.trim()) return;
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_600);
    } catch {
      setError("Onsite48 could not copy this response. Check that your browser allows clipboard access and try again.");
    }
  }, [selected?.final_answer]);

  useEffect(() => { void load(); }, [load]);
  if (!user) return <AuthScreen />;

  return (
    <main className="min-h-screen bg-[#f8f8f6] px-5 py-7 text-[#181817] sm:px-8">
      <div className="mx-auto max-w-6xl">
        <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-[#6c6c68] hover:text-black">
          <ArrowLeft className="h-4 w-4" /> Workspace
        </Link>
        <div className="mt-5">
          <p className="text-sm text-[#6a6a67]">{user.organization_name}</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-[-0.045em]">Task history</h1>
        </div>
        {error && <p className="mt-5 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}

        <div className="mt-7 grid gap-5 lg:grid-cols-[0.9fr_1.1fr]">
          <section className="overflow-hidden rounded-2xl border border-black/10 bg-white">
            <div className="flex items-center justify-between border-b border-black/10 px-5 py-4">
              <p className="text-sm font-semibold">Recent work</p>
              <button onClick={() => void load()} className="text-xs font-medium text-[#4c62d2]">Refresh</button>
            </div>
            {loading ? (
              <div className="grid h-64 place-items-center text-sm text-[#777]"><Loader2 className="h-4 w-4 animate-spin" /></div>
            ) : tasks.length === 0 ? (
              <div className="p-8 text-center text-sm text-[#777]">Your completed and in-progress tasks will appear here.</div>
            ) : (
              <div className="max-h-[calc(100vh-16rem)] overflow-y-auto">
                {tasks.map((task) => (
                  <div key={task.thread_id} className={`group flex items-stretch border-b border-black/[0.06] transition hover:bg-[#fafaf8] ${selected?.thread_id === task.thread_id ? "bg-[#f5f6ff]" : ""}`}>
                    <button onClick={() => setSelected(task)} className="min-w-0 flex-1 px-5 py-4 text-left">
                      <div className="flex items-start justify-between gap-3">
                        <p className="line-clamp-2 text-sm font-medium">{task.title}</p>
                        <span className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-medium ${statusClass(task.status)}`}>{task.status.replaceAll("_", " ")}</span>
                      </div>
                      <p className="mt-2 flex items-center gap-1 text-xs text-[#81817c]"><Clock3 className="h-3 w-3" /> {new Date(task.created_at).toLocaleString()}</p>
                    </button>
                    {taskCanBeDeleted(task) && (
                      <button
                        type="button"
                        onClick={() => void deleteTask(task)}
                        disabled={deletingThreadId === task.thread_id}
                        aria-label={`Delete task: ${task.title}`}
                        title="Delete task"
                        className="mr-3 self-center rounded-lg p-2 text-[#999] opacity-0 transition hover:bg-red-50 hover:text-red-600 focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-red-300 disabled:cursor-wait group-hover:opacity-100 group-focus-within:opacity-100"
                      >
                        {deletingThreadId === task.thread_id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="min-h-80 rounded-2xl border border-black/10 bg-white p-6">
            {selected ? (
              <>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <span className={`inline-block rounded-full px-2 py-1 text-[11px] font-medium ${statusClass(selected.status)}`}>{selected.status.replaceAll("_", " ")}</span>
                    <h2 className="mt-3 text-xl font-semibold tracking-[-0.03em]">{selected.title}</h2>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {taskCanBeDeleted(selected) && (
                      <button
                        type="button"
                        onClick={() => void deleteTask(selected)}
                        disabled={deletingThreadId === selected.thread_id}
                        className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-3 py-2 text-xs font-medium text-red-700 hover:bg-red-50 disabled:cursor-wait"
                      >
                        {deletingThreadId === selected.thread_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />} Delete
                      </button>
                    )}
                    <Link href="/" className="inline-flex items-center gap-1 rounded-lg border border-black/10 px-3 py-2 text-xs font-medium hover:bg-[#fafaf8]">
                      <PlayCircle className="h-3.5 w-3.5" /> New task
                    </Link>
                  </div>
                </div>
                <div className="mt-6">
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#888883]">Original request</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[#4d4d49]">{selected.prompt}</p>
                </div>
                <div className="group relative mt-7 rounded-xl px-2 py-2 transition hover:bg-[#fafaf8]">
                  <div className="flex items-center justify-between gap-3">
                    <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-[#888883]"><MessageSquareText className="h-3.5 w-3.5" /> Final answer</p>
                    {selected.final_answer && (
                      <button
                        type="button"
                        onClick={() => void copyFinalAnswer()}
                        aria-label="Copy Onsite48 response"
                        title="Copy response"
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-[#6c6c68] opacity-0 transition hover:bg-white hover:text-black focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-[#4c62d2] group-hover:opacity-100 group-focus-within:opacity-100"
                      >
                        {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
                        {copied ? "Copied" : "Copy"}
                      </button>
                    )}
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[#30302e]">{selected.final_answer || "This task is still running, paused for approval, or did not produce a final answer."}</p>
                </div>
                {selected.cost_metrics?.total_cost_usd !== undefined && <p className="mt-7 text-xs text-[#898984]">Recorded model cost: ${selected.cost_metrics.total_cost_usd.toFixed(4)}</p>}
              </>
            ) : (
              <div className="grid h-full place-items-center text-center text-sm text-[#777]">Select a task to inspect its result.</div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}