"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ArrowUpRight, CheckCircle2, Loader2, PlugZap, ShieldCheck } from "lucide-react";
import { AuthScreen } from "@/components/AuthScreen";
import { authHeaders, useAuth } from "@/components/AuthProvider";

type MarketplaceItem = { id: string; name: string; category: string; description: string; auth_type: string; setup_url: string; capabilities: string[]; status: "supported" | "contract_ready" | "planned"; configured: boolean; redirect_uri?: string | null; connection?: { status: string } | null };

export default function ConnectorMarketplacePage() {
  const { accessToken, user } = useAuth();
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!accessToken) return;
    try { const response = await fetch("/api/connectors/marketplace", { headers: authHeaders(accessToken), credentials: "include" }); if (!response.ok) throw new Error("Could not load connector marketplace."); setItems(await response.json() as MarketplaceItem[]); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load connector marketplace."); }
    finally { setLoading(false); }
  }, [accessToken]);
  useEffect(() => { void load(); }, [load]);
  if (!user) return <AuthScreen />;
  return <main className="min-h-screen bg-[#f8f8f6] px-5 py-7 text-[#181817] sm:px-8"><div className="mx-auto max-w-6xl"><Link href="/connectors" className="inline-flex items-center gap-1.5 text-sm text-[#6d6d67] hover:text-black"><ArrowLeft className="h-4 w-4" /> Your connected tools</Link><div className="mt-5 flex items-start justify-between gap-5"><div><h1 className="text-3xl font-semibold tracking-[-0.04em]">Connector marketplace</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-[#666661]">Supported connectors can be connected today. Contract-ready providers expose their required onboarding steps now; their live data collectors are released only after credential and API validation.</p></div><div className="hidden items-center gap-2 rounded-xl border border-black/10 bg-white px-3 py-2 text-xs text-[#676763] sm:flex"><ShieldCheck className="h-4 w-4 text-green-600" /> No secrets in browser code</div></div>{error && <p className="mt-5 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}{loading ? <div className="grid h-64 place-items-center"><Loader2 className="h-5 w-5 animate-spin text-[#777]" /></div> : <div className="mt-7 grid gap-4 md:grid-cols-2 xl:grid-cols-3">{items.map((item) => <article key={item.id} className="flex min-h-72 flex-col rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-start justify-between gap-3"><div className="grid h-10 w-10 place-items-center rounded-xl bg-[#f0f2ff] text-[#485ed2]"><PlugZap className="h-5 w-5" /></div>{item.status === "supported" ? <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-1 text-[11px] font-medium text-green-700"><CheckCircle2 className="h-3 w-3" /> Available</span> : <span className="rounded-full bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700">Setup contract</span>}</div><h2 className="mt-5 text-lg font-semibold">{item.name}</h2><p className="mt-2 text-sm leading-6 text-[#686864]">{item.description}</p><div className="mt-4 flex flex-wrap gap-1.5">{item.capabilities.map((capability) => <span key={capability} className="rounded-md bg-[#f5f5f1] px-2 py-1 text-[11px] text-[#666]">{capability}</span>)}</div><div className="mt-auto pt-5"><a href={item.setup_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm font-medium text-[#4056cf] hover:text-[#283cae]">Open provider setup <ArrowUpRight className="h-3.5 w-3.5" /></a>{item.redirect_uri && <p className="mt-2 break-all text-[11px] leading-4 text-[#888]">Callback: {item.redirect_uri}</p>}</div></article>)}</div>}</div></main>;
}