"use client";

import { CheckCircle2, CircleAlert, CircleDashed, Loader2, ShieldAlert, Wrench } from "lucide-react";

export type ExecutionTimelineItem = {
  id: string;
  phase: string;
  label: string;
  detail?: string;
  status: "active" | "completed" | "waiting" | "failed" | "pending";
  timestamp?: string;
};

function Icon({ item }: { item: ExecutionTimelineItem }) {
  if (item.status === "active") return <Loader2 className="h-4 w-4 animate-spin text-blue-600" />;
  if (item.status === "completed") return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
  if (item.status === "waiting") return <ShieldAlert className="h-4 w-4 text-amber-600" />;
  if (item.status === "failed") return <CircleAlert className="h-4 w-4 text-rose-600" />;
  return <CircleDashed className="h-4 w-4 text-slate-400" />;
}

export function ExecutionTimeline({ items }: { items: ExecutionTimelineItem[] }) {
  if (!items.length) return null;
  return <section aria-label="Execution progress" className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
    <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-800"><Wrench className="h-4 w-4 text-blue-600" /> Execution progress</div>
    <ol className="space-y-2">
      {items.map((item) => <li key={item.id} className="flex items-start gap-3 rounded-xl px-2 py-2 hover:bg-slate-50">
        <span className="mt-0.5 shrink-0"><Icon item={item} /></span>
        <span className="min-w-0 flex-1"><span className="block text-xs font-medium text-slate-800">{item.label}</span>{item.detail && <span className="mt-0.5 block text-[11px] leading-5 text-slate-500">{item.detail}</span>}</span>
        {item.timestamp && <time className="shrink-0 text-[10px] text-slate-400">{new Date(item.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>}
      </li>)}
    </ol>
  </section>;
}