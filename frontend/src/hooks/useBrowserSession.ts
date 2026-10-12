"use client";
import { useCallback, useEffect, useState } from "react";
import type { BrowserSession, BrowserSessionEvent } from "../types/browser-session";

export function useBrowserSession(accessToken: string | null) {
  const [session, setSession] = useState<BrowserSession | null>(null); const [events, setEvents] = useState<BrowserSessionEvent[]>([]); const [error, setError] = useState<string | null>(null);
  const api = async (path: string, init?: RequestInit) => { const headers: Record<string, string> = { "Content-Type": "application/json" }; if (init?.headers) { if (Array.isArray(init.headers)) { for (const [k, v] of init.headers) headers[k] = v; } else { for (const [k, v] of Object.entries(init.headers)) headers[k] = v; } } if (accessToken) { headers["Authorization"] = `Bearer ${accessToken}`; } const response = await fetch(path, { credentials: "include", headers, ...init }); if (!response.ok) { const body: { detail?: unknown } = await response.json().catch(() => ({})); const detail = body?.detail; const message = Array.isArray(detail) ? detail.map((item) => (item && typeof item === "object" && !Array.isArray(item) ? String((item as { msg?: unknown }).msg ?? (item as { message?: unknown }).message ?? JSON.stringify(item)) : String(item))).join("; ") : typeof detail === "string" ? detail : null; throw new Error(message || `Browser session request failed (${response.status})`); } return response.json(); };
  const run = useCallback(async (path: string, init?: RequestInit) => { try { setError(null); const data = await api(path, init); if (data.session) setSession(data.session); if (data.events) setEvents(data.events); return data; } catch (e) { const message = e instanceof Error ? e.message : "Browser session request failed"; setError(message); throw e; } }, [accessToken]);
  const create = useCallback((origins: string[], capabilities: string[], taskId?: string, mode?: string) => run("/api/browser-sessions", { method: "POST", body: JSON.stringify({ requested_origins: origins, requested_capabilities: capabilities, task_id: taskId || null, mode: mode || "task_workspace" }) }), [run]);
  const control = useCallback((action: "pause" | "takeover" | "resume" | "stop") => session ? run(`/api/browser-sessions/${session.id}/${action}`, { method: "POST" }) : Promise.reject(new Error("No browser session")), [run, session]);
  const heartbeat = useCallback((observation: Record<string, unknown>, stepId?: string, url?: string) => session ? run(`/api/browser-sessions/${session.id}/heartbeat`, { method: "POST", body: JSON.stringify({ observation, step_id: stepId, url }) }) : Promise.reject(new Error("No browser session")), [run, session]);
  const loadEvents = useCallback(() => session ? run(`/api/browser-sessions/${session.id}/events`) : Promise.resolve(null), [run, session]);
  const refresh = useCallback(async () => {
    if (!session) return null;
    const [sessionData, eventData] = await Promise.all([
      api(`/api/browser-sessions/${session.id}`),
      api(`/api/browser-sessions/${session.id}/events`),
    ]);
    if (sessionData.session) setSession(sessionData.session);
    if (eventData.events) setEvents(eventData.events);
    return { ...sessionData, ...eventData };
  }, [session]);
  useEffect(() => {
    if (!session || ["completed", "failed", "cancelled", "expired"].includes(session.status)) return;
    const timer = window.setInterval(() => { void refresh().catch(() => undefined); }, 1500);
    return () => window.clearInterval(timer);
  }, [refresh, session]);
  return { session, events, error, create, control, heartbeat, loadEvents, refresh, setSession };
}