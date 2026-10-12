"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, CalendarClock, Check, Clock3, Loader2, Megaphone, Play, Plus, ShieldCheck, Sparkles, Trash2, Users, X } from "lucide-react";
import { AuthScreen } from "@/components/AuthScreen";
import { authHeaders, useAuth } from "@/components/AuthProvider";

type Signal = { id: string; title: string; summary: string; severity: string; source: string; detected_at: string };
type Proposal = { id: string; title: string; rationale: string; action_type: string; connector_id?: string | null; created_at: string };
type Schedule = { enabled: boolean; timezone: string; hour: number; minute: number; last_run_at?: string | null; next_retry_at?: string | null; failure_count?: number; last_error?: string | null; last_error_at?: string | null; dead_lettered_at?: string | null };
type ScheduledTask = { id: string; title: string; prompt: string; schedule_type: "once" | "daily"; timezone: string; run_at: string; next_run_at?: string | null; enabled: boolean; last_run_at?: string | null; last_task_thread_id?: string | null; failure_count: number; last_error?: string | null };
type Member = { id: string; display_name: string; email: string; role: string };
type Campaign = { id: string; title: string; objective: string; channel?: string | null; status: "backlog" | "researching" | "in_review" | "approved" | "live" | "complete"; owner_user_id?: string | null; owner?: Member | null; due_at?: string | null };
type Overview = { signals: Signal[]; proposals: Proposal[]; schedule: Schedule | null; scheduled_tasks: ScheduledTask[]; campaigns: Campaign[]; members: Member[] };

const severityStyle: Record<string, string> = { critical: "bg-red-100 text-red-700", high: "bg-orange-100 text-orange-700", medium: "bg-amber-100 text-amber-700", low: "bg-blue-100 text-blue-700" };
const localDate = (value: Date) => value.toISOString().slice(0, 10);

export default function MarketingOperationsPage() {
  const { accessToken, user, status } = useAuth();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [briefing, setBriefing] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = useMemo(() => new Date(), []);
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [date, setDate] = useState(localDate(new Date(now.getTime() + 60 * 60 * 1000)));
  const [time, setTime] = useState("09:00");
  const [recurrence, setRecurrence] = useState<"once" | "daily">("once");
  const [campaignOpen, setCampaignOpen] = useState(false);
  const [campaignTitle, setCampaignTitle] = useState("");
  const [campaignObjective, setCampaignObjective] = useState("");
  const [campaignChannel, setCampaignChannel] = useState("");
  const [campaignOwner, setCampaignOwner] = useState("");
  const [creatingCampaign, setCreatingCampaign] = useState(false);

  const load = useCallback(async () => {
    if (!accessToken) return;
    setLoading(true);
    try {
      const response = await fetch("/api/proactive/overview", { headers: authHeaders(accessToken), credentials: "include" });
      if (!response.ok) throw new Error("Could not load Marketing Operations.");
      setOverview(await response.json() as Overview);
      setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not load Marketing Operations."); }
    finally { setLoading(false); }
  }, [accessToken]);

  useEffect(() => { void load(); }, [load]);

  async function runBriefing() {
    if (!accessToken) return;
    setRunning(true);
    try {
      const response = await fetch("/api/proactive/briefings/run", { method: "POST", headers: authHeaders(accessToken), credentials: "include" });
      if (!response.ok) throw new Error("Could not generate the briefing.");
      const result = await response.json() as { summary: string };
      setBriefing(result.summary);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not generate the briefing."); }
    finally { setRunning(false); }
  }

  async function decide(id: string, approved: boolean) {
    if (!accessToken) return;
    const response = await fetch(`/api/proactive/proposals/${id}/decision?approved=${approved}`, { method: "POST", headers: authHeaders(accessToken), credentials: "include" });
    if (!response.ok) { setError("Could not save the proposal decision."); return; }
    await load();
  }

  async function enableDefaultSchedule() {
    if (!accessToken) return;
    const response = await fetch("/api/proactive/morning-briefing/schedule", { method: "PUT", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, credentials: "include", body: JSON.stringify({ enabled: true, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", hour: 8, minute: 0 }) });
    if (!response.ok) { setError("Could not save the briefing schedule."); return; }
    await load();
  }

  async function createSchedule(event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    const local = new Date(`${date}T${time}:00`);
    if (Number.isNaN(local.valueOf()) || local <= new Date()) { setError("Choose a future local date and time."); return; }
    setSavingSchedule(true);
    try {
      const response = await fetch("/api/proactive/scheduled-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(accessToken) },
        credentials: "include",
        body: JSON.stringify({ title: title.trim() || prompt.trim().slice(0, 90), prompt: prompt.trim(), schedule_type: recurrence, run_at: local.toISOString(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Could not save the schedule.");
      setTitle(""); setPrompt(""); setError(null); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not save the schedule."); }
    finally { setSavingSchedule(false); }
  }

  async function createCampaign(event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken || !campaignTitle.trim() || !campaignObjective.trim()) return;
    setCreatingCampaign(true);
    try {
      const response = await fetch("/api/proactive/campaigns", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, body: JSON.stringify({ title: campaignTitle.trim(), objective: campaignObjective.trim(), channel: campaignChannel.trim() || undefined, owner_user_id: campaignOwner || undefined }) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Could not create the campaign.");
      setCampaignTitle(""); setCampaignObjective(""); setCampaignChannel(""); setCampaignOwner(""); setCampaignOpen(false); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not create the campaign."); }
    finally { setCreatingCampaign(false); }
  }

  async function updateCampaign(campaign: Campaign, nextStatus: Campaign["status"], ownerUserId?: string) {
    if (!accessToken) return;
    try {
      const response = await fetch(`/api/proactive/campaigns/${campaign.id}`, { method: "PATCH", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, body: JSON.stringify({ status: nextStatus, ...(ownerUserId !== undefined ? { owner_user_id: ownerUserId || null } : {}) }) });
      if (!response.ok) throw new Error("Could not update the campaign.");
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update the campaign."); }
  }

  async function cancelSchedule(id: string) {
    if (!accessToken || !window.confirm("Cancel this scheduled agent task?")) return;
    const response = await fetch(`/api/proactive/scheduled-tasks/${id}`, { method: "DELETE", headers: authHeaders(accessToken), credentials: "include" });
    if (!response.ok) { setError("Could not cancel the scheduled task."); return; }
    await load();
  }

  const scheduleNeedsRecovery = Boolean(overview?.schedule?.dead_lettered_at);
  if (status === "loading") return <main className="grid min-h-screen place-items-center text-sm text-[#73736e]"><Loader2 className="h-4 w-4 animate-spin" /></main>;
  if (!user) return <AuthScreen />;

  return <main className="min-h-screen bg-[#f8f8f6] px-5 py-7 text-[#1a1a18] sm:px-8"><div className="mx-auto max-w-7xl">
    <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-[#70706a] hover:text-black"><ArrowLeft className="h-4 w-4" /> Agent workspace</Link>
    <section className="mt-6 flex flex-col justify-between gap-5 rounded-3xl bg-[#18214a] p-7 text-white md:flex-row md:items-end"><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#b8c4ff]">Specialized workspace</p><h1 className="mt-2 text-3xl font-semibold tracking-[-0.04em]">Marketing Operations</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-[#d5dbff]">Turn campaign, content, brand, and competitive signals into a prioritized operating rhythm. Onsite48 drafts actions; you stay in control.</p></div><div className="flex flex-wrap gap-2"><button onClick={() => void runBriefing()} disabled={running} className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-[#17204a] disabled:opacity-60">{running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Run morning briefing</button>{(!overview?.schedule?.enabled || scheduleNeedsRecovery) && <button onClick={() => void enableDefaultSchedule()} className="inline-flex items-center gap-2 rounded-xl border border-white/25 px-4 py-2.5 text-sm font-medium text-white"><Clock3 className="h-4 w-4" /> Enable 08:00 daily</button>}</div></section>

    <section className="mt-5 rounded-2xl border border-black/10 bg-white p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2"><Users className="h-5 w-5 text-indigo-600" /><h2 className="font-semibold">Campaign operating board</h2></div><p className="mt-1 text-xs leading-5 text-[#777771]">Share ownership, move work through the operating stages, and use Onsite48’s signals and approval inbox as evidence—not as automatic external action.</p></div><button onClick={() => setCampaignOpen((open) => !open)} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white"><Plus className="h-3.5 w-3.5" /> New campaign</button></div>{campaignOpen && <form onSubmit={createCampaign} className="mt-4 grid gap-3 rounded-xl bg-[#f7f7fb] p-4 sm:grid-cols-2"><label className="text-xs font-medium text-[#565650]">Campaign title<input required value={campaignTitle} onChange={(event) => setCampaignTitle(event.target.value)} className="mt-1.5 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm" placeholder="Q4 competitor launch response" /></label><label className="text-xs font-medium text-[#565650]">Owner<select value={campaignOwner} onChange={(event) => setCampaignOwner(event.target.value)} className="mt-1.5 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm"><option value="">Unassigned</option>{overview?.members.map((member) => <option key={member.id} value={member.id}>{member.display_name} · {member.role}</option>)}</select></label><label className="sm:col-span-2 text-xs font-medium text-[#565650]">Objective<textarea required value={campaignObjective} onChange={(event) => setCampaignObjective(event.target.value)} rows={3} className="mt-1.5 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm" placeholder="What outcome should this campaign achieve?" /></label><label className="text-xs font-medium text-[#565650]">Channel <span className="font-normal text-[#888]">(optional)</span><input value={campaignChannel} onChange={(event) => setCampaignChannel(event.target.value)} className="mt-1.5 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm" placeholder="LinkedIn, email, paid search" /></label><div className="flex items-end justify-end gap-2"><button type="button" onClick={() => setCampaignOpen(false)} className="rounded-lg px-3 py-2 text-xs font-medium">Cancel</button><button disabled={creatingCampaign} className="rounded-lg bg-[#1b1b1a] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">{creatingCampaign ? "Creating…" : "Create campaign"}</button></div></form>}<div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{overview?.campaigns?.length ? overview.campaigns.map((campaign) => <article key={campaign.id} className="flex min-h-52 flex-col rounded-xl border border-black/10 p-4"><div className="flex items-start justify-between gap-2"><span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium capitalize text-indigo-700">{campaign.status.replaceAll("_", " ")}</span><select aria-label={`Status for ${campaign.title}`} value={campaign.status} onChange={(event) => void updateCampaign(campaign, event.target.value as Campaign["status"])} className="rounded-md border border-black/10 bg-white px-1.5 py-1 text-[11px]"><option value="backlog">Backlog</option><option value="researching">Researching</option><option value="in_review">In review</option><option value="approved">Approved</option><option value="live">Live</option><option value="complete">Complete</option></select></div><h3 className="mt-3 text-sm font-semibold">{campaign.title}</h3><p className="mt-1 line-clamp-3 text-xs leading-5 text-[#656560]">{campaign.objective}</p><p className="mt-3 text-xs text-[#777]">{campaign.channel || "No channel selected"}</p><div className="mt-auto border-t border-black/5 pt-3"><select aria-label={`Owner for ${campaign.title}`} value={campaign.owner_user_id || ""} onChange={(event) => void updateCampaign(campaign, campaign.status, event.target.value)} className="w-full rounded-md border border-black/10 bg-white px-2 py-1.5 text-xs"><option value="">Unassigned owner</option>{overview?.members.map((member) => <option key={member.id} value={member.id}>{member.display_name} · {member.role}</option>)}</select>{campaign.owner && <p className="mt-1 text-[11px] text-[#888]">Owned by {campaign.owner.display_name}</p>}</div></article>) : <div className="md:col-span-2 xl:col-span-3 rounded-xl bg-[#f7f7f4] p-5 text-sm text-[#777771]">No campaigns are on the board. Create one to assign a marketing objective and link it to your signals, schedules, and approvals.</div>}</div></section>

    <section className="mt-5 grid gap-5 lg:grid-cols-[1.15fr_0.85fr]"><form onSubmit={createSchedule} className="rounded-2xl border border-indigo-100 bg-white p-5 shadow-sm"><div className="flex items-center gap-2"><CalendarClock className="h-5 w-5 text-indigo-600" /><div><h2 className="font-semibold">Schedule an agent task</h2><p className="text-xs text-[#777771]">Choose exactly when Onsite48 should run a task. Results appear in task history and as an in-app notification.</p></div></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium text-[#565650]">Task title<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Weekly competitor review" className="mt-1.5 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" /></label><label className="text-xs font-medium text-[#565650]">Repeat<select value={recurrence} onChange={(event) => setRecurrence(event.target.value as "once" | "daily")} className="mt-1.5 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm"><option value="once">Run once</option><option value="daily">Every day</option></select></label><label className="text-xs font-medium text-[#565650]">Date<input type="date" value={date} onChange={(event) => setDate(event.target.value)} required className="mt-1.5 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" /></label><label className="text-xs font-medium text-[#565650]">Time<input type="time" value={time} onChange={(event) => setTime(event.target.value)} required className="mt-1.5 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" /></label></div><label className="mt-3 block text-xs font-medium text-[#565650]">What should Onsite48 do?<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} required maxLength={12000} rows={4} placeholder="Research the three most important competitor changes this week and give me a short action brief." className="mt-1.5 w-full resize-y rounded-lg border border-black/10 px-3 py-2 text-sm" /></label><p className="mt-2 text-xs text-[#777771]">Your browser time zone will be saved with this schedule. External actions still pause for your approval.</p><button disabled={savingSchedule || !prompt.trim()} className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">{savingSchedule ? <Loader2 className="h-4 w-4 animate-spin" /> : <CalendarClock className="h-4 w-4" />} Save schedule</button></form>
      <section className="rounded-2xl border border-black/10 bg-white p-5"><h2 className="font-semibold">Scheduled tasks</h2><p className="mt-1 text-xs text-[#777771]">The background worker checks due tasks every minute. One-time tasks disable after completion; daily tasks calculate their next local run.</p><div className="mt-4 space-y-3">{overview?.scheduled_tasks?.length ? overview.scheduled_tasks.map((task) => <article key={task.id} className="rounded-xl border border-black/10 p-3"><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold">{task.title}</p><p className="mt-1 text-xs text-[#666]">{task.schedule_type === "daily" ? "Daily" : "One-time"} · next {task.next_run_at ? new Date(task.next_run_at).toLocaleString() : "completed or cancelled"}</p>{task.last_error && <p className="mt-1 text-xs text-amber-700">{task.last_error}</p>}</div><button type="button" onClick={() => void cancelSchedule(task.id)} disabled={!task.enabled} title="Cancel scheduled task" className="rounded-lg p-2 text-[#777] hover:bg-red-50 hover:text-red-700 disabled:opacity-30"><Trash2 className="h-4 w-4" /></button></div></article>) : <p className="rounded-xl bg-[#f7f7f4] p-4 text-sm text-[#777771]">No user-scheduled tasks yet.</p>}</div></section></section>

    {overview?.schedule && <section className={`mt-5 rounded-2xl border p-4 text-sm ${scheduleNeedsRecovery ? "border-red-200 bg-red-50 text-red-800" : overview.schedule.failure_count ? "border-amber-200 bg-amber-50 text-amber-800" : "border-emerald-100 bg-emerald-50 text-emerald-800"}`}><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="font-semibold">Daily marketing briefing · {String(overview.schedule.hour).padStart(2, "0")}:{String(overview.schedule.minute).padStart(2, "0")} {overview.schedule.timezone}</p><p className="mt-1 text-xs">{scheduleNeedsRecovery ? "This schedule stopped after repeated failures. Save it again to reset after checking the worker issue." : overview.schedule.failure_count ? `Retry ${overview.schedule.failure_count}/5${overview.schedule.next_retry_at ? ` · next attempt ${new Date(overview.schedule.next_retry_at).toLocaleString()}` : ""}` : overview.schedule.last_run_at ? `Last completed ${new Date(overview.schedule.last_run_at).toLocaleString()}` : "Enabled and waiting for its first scheduled run."}</p></div>{scheduleNeedsRecovery && <button onClick={() => void enableDefaultSchedule()} className="rounded-lg bg-red-700 px-3 py-1.5 text-xs font-semibold text-white">Reset schedule</button>}</div>{overview.schedule.last_error && <p className="mt-2 rounded-lg bg-white/60 px-3 py-2 text-xs leading-5">Latest worker error: {overview.schedule.last_error}</p>}</section>}
    {error && <p className="mt-5 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
    {briefing && <section className="mt-6 rounded-2xl border border-indigo-100 bg-white p-6"><div className="flex items-center gap-2 text-sm font-semibold text-indigo-700"><Sparkles className="h-4 w-4" /> Today’s marketing briefing</div><pre className="mt-4 whitespace-pre-wrap font-sans text-sm leading-6 text-[#3d3d39]">{briefing}</pre></section>}
    <div className="mt-6 grid gap-5 lg:grid-cols-[1.1fr_0.9fr]"><section className="rounded-2xl border border-black/10 bg-white"><div className="flex items-center justify-between border-b border-black/10 px-5 py-4"><div><h2 className="font-semibold">Signals</h2><p className="mt-0.5 text-xs text-[#777771]">Detected events requiring marketing attention</p></div><Megaphone className="h-5 w-5 text-indigo-500" /></div>{loading ? <div className="grid h-56 place-items-center"><Loader2 className="h-4 w-4 animate-spin text-[#777]" /></div> : overview?.signals.length ? <div className="divide-y divide-black/[0.06]">{overview.signals.map((signal) => <article key={signal.id} className="px-5 py-4"><div className="flex gap-3"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{signal.title}</h3><span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${severityStyle[signal.severity] || severityStyle.medium}`}>{signal.severity}</span></div><p className="mt-1 text-sm leading-5 text-[#62625d]">{signal.summary}</p><p className="mt-2 text-xs text-[#92928b]">{signal.source} · {new Date(signal.detected_at).toLocaleString()}</p></div></div></article>)}</div> : <div className="p-10 text-center text-sm text-[#777771]">No signals yet. Connect a marketing source, then enable a monitor to begin building the operational picture.</div>}</section><section className="rounded-2xl border border-black/10 bg-white"><div className="flex items-center justify-between border-b border-black/10 px-5 py-4"><div><h2 className="font-semibold">Approval inbox</h2><p className="mt-0.5 text-xs text-[#777771]">No external change occurs without your decision</p></div><ShieldCheck className="h-5 w-5 text-green-600" /></div>{loading ? <div className="grid h-56 place-items-center"><Loader2 className="h-4 w-4 animate-spin text-[#777]" /></div> : overview?.proposals.length ? <div className="divide-y divide-black/[0.06]">{overview.proposals.map((proposal) => <article key={proposal.id} className="px-5 py-4"><h3 className="text-sm font-semibold">{proposal.title}</h3><p className="mt-1 text-sm leading-5 text-[#62625d]">{proposal.rationale}</p><p className="mt-2 text-xs text-[#92928b]">{proposal.action_type}{proposal.connector_id ? ` · ${proposal.connector_id}` : ""}</p><div className="mt-3 flex gap-2"><button onClick={() => void decide(proposal.id, true)} className="inline-flex items-center gap-1.5 rounded-lg bg-[#1d7148] px-3 py-1.5 text-xs font-medium text-white"><Check className="h-3.5 w-3.5" /> Approve</button><button onClick={() => void decide(proposal.id, false)} className="inline-flex items-center gap-1.5 rounded-lg border border-black/10 px-3 py-1.5 text-xs font-medium"><X className="h-3.5 w-3.5" /> Reject</button></div></article>)}</div> : <div className="p-10 text-center text-sm text-[#777771]">No external actions are waiting for approval.</div>}</section></div>
  </div></main>;
}