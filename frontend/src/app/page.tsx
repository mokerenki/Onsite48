"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, CircleUserRound, LogOut, PlugZap } from "lucide-react";
import { AuthScreen } from "@/components/AuthScreen";
import { authHeaders, useAuth } from "@/components/AuthProvider";
import Sidebar from "@/components/Sidebar";
import ChatInterface, { ChatInterfaceHandle, RecentTaskSummary } from "@/components/Chatinterface";
import { NotificationBell } from "@/components/NotificationBell";
import { useDataResource } from "@/hooks/useDataResource";

function LoadingScreen() {
  return <div className="grid min-h-screen place-items-center bg-[#f8f8f6] text-sm text-[#6a6a67]">Opening your Onsite48 workspace…</div>;
}

type ApiTask = { thread_id: string; title: string; status: RecentTaskSummary["status"]; domain: string; updated_at: string; turn_count?: number; icon_key?: string; icon_color?: string; category?: string; presentation?: { icon_key?: string; icon_color?: string; category?: string } };

export default function Home() {
  const { user, status, logout, accessToken } = useAuth();
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  // Default true so navigating Home ← Automations/Library does not force the
  // latest chat open. Explicit task click or a new run clears this.
  const [suppressTaskAutoSelect, setSuppressTaskAutoSelect] = useState(true);
  const [accountOpen, setAccountOpen] = useState(false);
  const chatRef = useRef<ChatInterfaceHandle>(null);

  const loadTasks = useCallback(async (signal: AbortSignal): Promise<RecentTaskSummary[]> => {
    if (!accessToken) return [];
    const response = await fetch("/api/tasks?limit=50", { signal, headers: authHeaders(accessToken), credentials: "include" });
    if (!response.ok) throw new Error("Task history could not be loaded.");
    const tasks = await response.json() as ApiTask[];
    return tasks.map((task) => ({ id: task.thread_id, title: task.title, status: task.status, domain: task.domain, updatedAt: task.updated_at, turnCount: task.turn_count || 1, iconKey: task.presentation?.icon_key || task.icon_key, iconColor: task.presentation?.icon_color || task.icon_color, category: task.presentation?.category || task.category }));
  }, [accessToken]);

  const tasks = useDataResource(loadTasks, [accessToken], Boolean(accessToken), {
    cacheKey: `tasks:${user?.id || "anonymous"}`,
    staleMs: 20_000,
  });
  const recentTasks = tasks.data ?? [];
  const activeTaskTitle = recentTasks.find((task) => task.id === activeTaskId)?.title || null;

  useEffect(() => {
    // Only restore a task the user already had open this session (localStorage),
    // never auto-pick recentTasks[0] — that forced users back into the latest
    // chat after visiting Automations / Library / etc.
    if (suppressTaskAutoSelect || activeTaskId || !recentTasks.length) return;
    const rememberedThread = window.localStorage.getItem("onsite48_thread_id");
    if (!rememberedThread) return;
    const selected = recentTasks.find((task) => task.id === rememberedThread);
    if (selected) setActiveTaskId(selected.id);
  }, [activeTaskId, recentTasks, suppressTaskAutoSelect]);

  const handleNewTask = useCallback(() => {
    setSuppressTaskAutoSelect(true);
    setActiveTaskId(null);
    window.localStorage.removeItem("onsite48_thread_id");
    chatRef.current?.reset();
  }, []);

  const handleSelectTask = useCallback((threadId: string) => {
    setSuppressTaskAutoSelect(false);
    setActiveTaskId(threadId);
  }, []);

  const handleDeleteTask = useCallback(async (threadId: string) => {
    if (!accessToken) return;
    const task = recentTasks.find((item) => item.id === threadId);
    const label = task?.title || "this task";
    if (!window.confirm(`Permanently delete ${label}? This removes its conversation, steps, approvals, and answer. This cannot be undone.`)) return;

    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(threadId)}`, {
        method: "DELETE",
        headers: authHeaders(accessToken),
        credentials: "include",
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail || "This task could not be deleted.");
      }
      tasks.retry();
      if (activeTaskId === threadId) {
        window.localStorage.removeItem("onsite48_thread_id");
        chatRef.current?.reset();
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "This task could not be deleted.");
    }
  }, [accessToken, activeTaskId, recentTasks, tasks]);

  if (status === "loading") return <LoadingScreen />;
  if (!user) return <AuthScreen />;

  return (
    <div className="flex h-screen overflow-hidden bg-onsite48-background">
      <Sidebar onNewTask={handleNewTask} recentTasks={recentTasks} recentTasksLoading={tasks.status === "loading" && tasks.data === null} recentTasksError={tasks.status === "error" && tasks.data === null ? tasks.error : null} onRetryRecentTasks={tasks.retry} activeTaskId={activeTaskId} onSelectTask={handleSelectTask} onDeleteTask={handleDeleteTask} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-onsite48-border bg-white px-5 sm:px-7">
          <div>
            <p className="text-sm font-semibold text-[#1a1a1e]">Good to see you, {user.display_name.split(" ")[0]}</p>
            <p className="mt-0.5 text-xs text-text-muted">{user.organization_name} · secure workspace</p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/connectors" className="hidden items-center gap-2 rounded-lg border border-onsite48-border px-3 py-2 text-xs font-medium text-text-secondary transition hover:bg-onsite48-surface sm:flex"><PlugZap className="h-3.5 w-3.5" /> Connect tools</Link>
            <NotificationBell />
            <div className="relative">
              <button title="Account menu" onClick={() => setAccountOpen((value) => !value)} className="inline-flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-medium text-text-secondary transition hover:bg-onsite48-surface hover:text-[#1a1a1e]"><CircleUserRound className="h-4 w-4" /><span className="hidden sm:inline">Account</span><ChevronDown className="h-3.5 w-3.5" /></button>
              {accountOpen && <div className="absolute right-0 z-50 mt-2 w-56 overflow-hidden rounded-xl border border-black/10 bg-white py-1 shadow-xl"><div className="border-b border-black/10 px-3 py-2.5"><p className="truncate text-sm font-medium text-[#1a1a1e]">{user.display_name}</p><p className="truncate text-xs text-text-muted">{user.email}</p></div><Link href="/connectors" onClick={() => setAccountOpen(false)} className="block px-3 py-2 text-sm text-text-secondary hover:bg-onsite48-surface">Connected tools</Link><button onClick={() => void logout()} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-rose-700 hover:bg-rose-50"><LogOut className="h-3.5 w-3.5" /> Sign out</button></div>}
            </div>
          </div>
        </header>
        <ChatInterface ref={chatRef} onTasksChange={() => tasks.retry()} onActiveTaskChange={(id) => { if (id) setSuppressTaskAutoSelect(false); setActiveTaskId(id); }} activeTaskId={activeTaskId} activeTaskTitle={activeTaskTitle} />
      </div>
    </div>
  );
}