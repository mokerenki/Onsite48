"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Activity, ArrowLeft, Check, Copy, Laptop, Link2, Loader2, ShieldAlert, ShieldCheck, Trash2 } from "lucide-react";
import { authHeaders, useAuth } from "@/components/AuthProvider";
import { DataLoader } from "@/components/DataLoader";
import { useDataResource } from "@/hooks/useDataResource";

type Device = {
  id: string; device_id: string; display_name: string; status: string; lifecycle?: "connected" | "offline" | "pending" | "revoked" | string; is_available?: boolean; granted_capabilities: string[];
  active_context?: { hostname?: string; title?: string; observed_at?: string };
  last_seen_at?: string | null; paired_at?: string | null; is_preferred?: boolean;
};
type TrustRule = { id: string; device_id: string; hostname: string; allowed_capabilities: string[]; enabled: boolean };
type Pairing = { device: Device; pairing_code: string; expires_in_seconds: number };
type Command = { id: string; command_type: string; status: string; requires_approval: boolean; hostname?: string; url?: string | null; challenge_detected: boolean; cancel_reason?: string | null; error?: string | null; verification_status?: string | null; evidence_available: boolean; created_at: string; completed_at?: string | null };

const TRUSTABLE = [
  ["read_active_tab", "Read page content"],
  ["inspect_active_tab", "Inspect visible controls"],
  ["scroll_active_tab", "Scroll the page"],
  ["navigate_active_tab", "Navigate within the same site"],
] as const;

export default function BridgePage() {
  const { user, status, accessToken } = useAuth();
  const [name, setName] = useState("My browser");
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [creatingPairing, setCreatingPairing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [ruleDeviceId, setRuleDeviceId] = useState("");
  const [hostname, setHostname] = useState("");
  const [capabilities, setCapabilities] = useState<string[]>(["read_active_tab", "inspect_active_tab", "scroll_active_tab"]);

  const devicesResource = useDataResource(async (signal: AbortSignal) => {
    if (!accessToken) return [] as Device[];
    const response = await fetch("/api/bridge/devices", { signal, headers: authHeaders(accessToken), credentials: "include" });
    if (!response.ok) throw new Error("Paired browsers could not be loaded.");
    return ((await response.json()).devices || []) as Device[];
  }, [accessToken], Boolean(accessToken), { cacheKey: `bridge:devices:${user?.id || "anonymous"}`, staleMs: 15_000 });
  const rulesResource = useDataResource(async (signal: AbortSignal) => {
    if (!accessToken) return [] as TrustRule[];
    const response = await fetch("/api/bridge/trust-rules", { signal, headers: authHeaders(accessToken), credentials: "include" });
    if (!response.ok) throw new Error("Trusted-site scopes could not be loaded.");
    return ((await response.json()).rules || []) as TrustRule[];
  }, [accessToken], Boolean(accessToken), { cacheKey: `bridge:rules:${user?.id || "anonymous"}`, staleMs: 15_000 });
  const commandsResource = useDataResource(async (signal: AbortSignal) => {
    if (!accessToken) return [] as Command[];
    const response = await fetch("/api/bridge/commands?limit=20", { signal, headers: authHeaders(accessToken), credentials: "include" });
    if (!response.ok) throw new Error("Bridge activity could not be loaded.");
    return ((await response.json()).commands || []) as Command[];
  }, [accessToken], Boolean(accessToken), { cacheKey: `bridge:commands:${user?.id || "anonymous"}`, staleMs: 10_000 });

  const devices = devicesResource.data ?? [];
  const rules = rulesResource.data ?? [];
  const commands = commandsResource.data ?? [];
  const { retry: retryDevices } = devicesResource;
  const { retry: retryRules } = rulesResource;
  const { retry: retryCommands } = commandsResource;
  const connectedDevices = useMemo(() => devices.filter((device) => device.is_available ?? (device.lifecycle === "connected" || device.status === "connected")), [devices]);
  const historicalDevices = useMemo(() => devices.filter((device) => !(device.is_available ?? (device.lifecycle === "connected" || device.status === "connected"))), [devices]);
  const selectedDevice = connectedDevices.find((device) => device.id === ruleDeviceId) || connectedDevices[0];
  const load = useCallback(async () => {
    retryDevices();
    retryRules();
    retryCommands();
  }, [retryDevices, retryRules, retryCommands]);

  useEffect(() => {
    if (selectedDevice?.active_context?.hostname) setHostname((value) => value || selectedDevice.active_context?.hostname || "");
  }, [selectedDevice]);

  async function createPairing(event: FormEvent) {
    event.preventDefault();
    if (creatingPairing || !name.trim()) return;
    setNotice(null);
    setCreatingPairing(true);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch("/api/bridge/pairings", {
        method: "POST", credentials: "include", signal: controller.signal,
        headers: { "Content-Type": "application/json", ...authHeaders(accessToken) },
        body: JSON.stringify({ display_name: name.trim() }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) { setNotice(body.detail || "Could not create a pairing request."); return; }
      setPairing(body);
      await load();
    } catch (error) {
      setNotice(error instanceof DOMException && error.name === "AbortError"
        ? "The pairing request timed out. Check the backend and try again."
        : "The pairing request could not be sent. Check your connection and try again.");
    } finally {
      window.clearTimeout(timeout);
      setCreatingPairing(false);
    }
  }

  async function saveRule(event: FormEvent) {
    event.preventDefault(); setNotice(null);
    const response = await fetch("/api/bridge/trust-rules", {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) },
      body: JSON.stringify({ device_id: ruleDeviceId, hostname, allowed_capabilities: capabilities, enabled: true }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) { setNotice(body.detail || "Could not save this trusted-site scope."); return; }
    setNotice(`Trusted-site scope saved for ${body.hostname}.`); setHostname(""); await load();
  }

  async function makePreferred(id: string) {
    if (!accessToken) return;
    const response = await fetch(`/api/bridge/devices/${id}/preferred`, { method: "PUT", credentials: "include", headers: authHeaders(accessToken) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) { setNotice(body.detail || "Could not set the preferred browser."); return; }
    setNotice(`${body.device?.display_name || "Browser"} will receive new local-browser tasks by default.`); await load();
  }

  async function revoke(id: string) {
    if (!confirm("Disconnect this browser? Onsite48 will no longer be able to send it requests.")) return;
    await fetch(`/api/bridge/devices/${id}`, { method: "DELETE", credentials: "include", headers: authHeaders(accessToken) });
    setPairing(null); await load();
  }

  async function deleteRule(id: string) {
    if (!confirm("Remove this trusted-site scope? Future actions on that site will ask again.")) return;
    await fetch(`/api/bridge/trust-rules/${id}`, { method: "DELETE", credentials: "include", headers: authHeaders(accessToken) });
    await load();
  }

  async function copy(value: string) { await navigator.clipboard.writeText(value); setNotice("Copied to clipboard."); }
  function toggleCapability(value: string) { setCapabilities((current) => current.includes(value) ? current.filter((item) => item !== value) : [...current, value]); }

  if (status === "loading") return <div className="grid min-h-screen place-items-center bg-[#f8f8f6] text-sm text-[#6a6a67]">Opening Onsite48 Bridge…</div>;
  if (!user) return <div className="grid min-h-screen place-items-center bg-[#f8f8f6] text-sm text-[#6a6a67]">Sign in to manage Onsite48 Bridge.</div>;

  return <main className="min-h-screen bg-[#f7f8fa] px-5 py-6 text-[#1b1b1b] sm:px-8"><div className="mx-auto max-w-5xl">
    <Link href="/" className="mb-5 inline-flex items-center gap-1.5 text-sm text-[#60646c] hover:text-black"><ArrowLeft className="h-4 w-4" /> Workspace</Link>
    <header className="border-b border-black/10 pb-6"><p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-600">Local browser operator</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Onsite48 Bridge</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-[#60646c]">Use your existing local browser sessions without sharing cookies, saved passwords, or session tokens. Create explicit trusted-site scopes for low-risk browser work; consequential actions always stop for approval.</p></header>
    {notice && <p className="mt-4 rounded-lg border border-black/10 bg-white px-3 py-2 text-sm text-[#4f5560]">{notice}</p>}

    <div className="mt-6 grid gap-5 lg:grid-cols-2">
      <section className="rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-center gap-2"><Link2 className="h-4 w-4 text-indigo-600" /><h2 className="font-semibold">Pair this browser</h2></div><ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-[#60646c]"><li>Load the `extension/onsite48-bridge` folder as an unpacked extension in Chrome.</li><li>Open the extension popup and paste the Device ID and one-time code below.</li><li>Keep a normal website tab active. The extension reports only its hostname and title for policy matching.</li></ol><form onSubmit={createPairing} className="mt-4 flex gap-2"><input value={name} onChange={(event) => setName(event.target.value)} disabled={creatingPairing} className="min-w-0 flex-1 rounded-lg border border-black/10 px-3 py-2 text-sm outline-none focus:border-indigo-500 disabled:bg-slate-50" placeholder="Browser name" /><button type="submit" disabled={creatingPairing || !name.trim()} className="inline-flex min-w-[120px] items-center justify-center gap-1.5 rounded-lg bg-[#171717] px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60">{creatingPairing ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Generating…</> : "Generate code"}</button></form>{pairing && <div className="mt-4 space-y-3 rounded-xl border border-indigo-200 bg-indigo-50 p-3"><p className="flex items-center gap-1.5 text-xs font-semibold text-indigo-900"><ShieldCheck className="h-3.5 w-3.5" /> Pairing code expires in {Math.round(pairing.expires_in_seconds / 60)} minutes</p><PairValue label="Device ID" value={pairing.device.device_id} onCopy={copy} /><PairValue label="One-time pairing code" value={pairing.pairing_code} onCopy={copy} /><p className="text-xs leading-5 text-indigo-800">This code is shown once. Do not send it to anyone or add it to `.env`.</p></div>}</section>
      <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><div className="flex items-center gap-2"><ShieldAlert className="h-4 w-4 text-amber-700" /><h2 className="font-semibold text-amber-900">Trust boundary</h2></div><p className="mt-3 text-sm leading-6 text-amber-900">A trusted-site scope can suppress repeated confirmation only for reading, inspecting, scrolling, and same-site navigation. Cross-site navigation, clicking, typing, publishing, sending messages, purchases, deletion, and settings changes always require explicit approval.</p><p className="mt-3 text-xs leading-5 text-amber-800">A scope is pinned to one paired browser and one exact hostname. If the active tab changes, Onsite48 Bridge stops the command rather than operating on a different website.</p></section>
    </div>

    <section className="mt-5 rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-indigo-600" /><div><h2 className="font-semibold">Trusted-site scopes</h2><p className="mt-0.5 text-xs text-[#777]">Reduce approval fatigue only where you intentionally grant low-risk access.</p></div></div>{connectedDevices.length ? <form onSubmit={saveRule} className="mt-4 grid gap-3 rounded-xl bg-[#f7f8fa] p-4 md:grid-cols-[1fr_1fr]"><label className="text-xs font-medium text-[#555]">Paired browser<select value={selectedDevice?.id || ""} onChange={(event) => { setRuleDeviceId(event.target.value); setHostname(""); }} className="mt-1.5 block w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm"><option value="" disabled>Select browser</option>{connectedDevices.map((device) => <option key={device.id} value={device.id}>{device.display_name}{device.active_context?.hostname ? ` · active: ${device.active_context.hostname}` : ""}</option>)}</select></label><label className="text-xs font-medium text-[#555]">Exact hostname<input value={hostname} onChange={(event) => setHostname(event.target.value)} className="mt-1.5 block w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm" placeholder="www.linkedin.com" /></label><fieldset className="md:col-span-2"><legend className="text-xs font-medium text-[#555]">Allow without a repeated prompt</legend><div className="mt-2 flex flex-wrap gap-2">{TRUSTABLE.map(([value, label]) => <label key={value} className="inline-flex items-center gap-2 rounded-lg border border-black/10 bg-white px-2.5 py-2 text-xs text-[#4f5560]"><input type="checkbox" checked={capabilities.includes(value)} onChange={() => toggleCapability(value)} /> {label}</label>)}</div></fieldset><div className="md:col-span-2 flex justify-end"><button disabled={!selectedDevice || !hostname.trim() || !capabilities.length} className="rounded-lg bg-[#171717] px-3 py-2 text-sm font-medium text-white disabled:opacity-40">Save trusted-site scope</button></div></form> : <p className="mt-4 text-sm text-[#777]">Pair a browser before creating a trusted-site scope.</p>}<div className="mt-5 divide-y divide-black/5"><DataLoader state={rulesResource} emptyMessage="No trusted-site scopes yet. Every Bridge operation will ask first." onRetry={rulesResource.retry}>{(loadedRules) => <>{loadedRules.map((rule) => <div key={rule.id} className="flex items-center justify-between gap-4 py-3"><div><p className="text-sm font-medium">{rule.hostname}</p><p className="mt-0.5 text-xs text-[#777]">{rule.enabled ? "Enabled" : "Disabled"} · {rule.allowed_capabilities.map((value) => value.replace("_active_tab", "").replaceAll("_", " ")).join(", ")}</p></div><button onClick={() => void deleteRule(rule.id)} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 px-2.5 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50"><Trash2 className="h-3.5 w-3.5" /> Remove</button></div>)}</>}</DataLoader></div></section>

    <section className="mt-5 rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-center gap-2"><Activity className="h-4 w-4 text-indigo-600" /><div><h2 className="font-semibold">Operator activity</h2><p className="mt-0.5 text-xs text-[#777]">A redacted record of approved local-browser work. Page text, screenshots, cookies, and typed values are never shown here.</p></div></div><div className="mt-4 divide-y divide-black/5"><DataLoader state={commandsResource} emptyMessage="No local-browser actions have been recorded yet." onRetry={commandsResource.retry}>{(loadedCommands) => <>{loadedCommands.map((command) => <article key={command.id} className="flex items-start justify-between gap-4 py-3"><div className="min-w-0"><p className="text-sm font-medium capitalize">{command.command_type.replace("_active_tab", "").replaceAll("_", " ")}</p><p className="mt-0.5 truncate text-xs text-[#777]">{command.hostname || command.url || "Local browser action"} · {new Date(command.created_at).toLocaleString()}</p>{(command.error || command.cancel_reason) && <p className="mt-1 text-xs text-amber-700">{command.cancel_reason || command.error}</p>}</div><div className="shrink-0 text-right"><span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${command.status === "completed" ? "bg-emerald-100 text-emerald-700" : command.status === "cancelled" ? "bg-amber-100 text-amber-800" : command.status === "failed" || command.status === "expired" ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-700"}`}>{command.status}</span><p className="mt-1 text-[11px] text-[#888]">{command.evidence_available ? "Evidence captured" : "No evidence"}{command.verification_status ? ` · ${command.verification_status}` : ""}</p></div></article>)}</>}</DataLoader></div></section>

    <section className="mt-5 rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Laptop className="h-4 w-4 text-indigo-600" /><div><h2 className="font-semibold">Browser control center</h2><p className="mt-0.5 text-xs text-[#777]">Only browsers marked available can receive local-browser work.</p></div></div><button type="button" onClick={() => void load()} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-xs font-medium text-[#555] hover:bg-[#f7f8fa]">Refresh status</button></div><div className="mt-4 divide-y divide-black/5"><DataLoader state={devicesResource} emptyMessage="No browser is paired yet." onRetry={devicesResource.retry}>{() => <>{connectedDevices.length ? connectedDevices.map((device) => <div key={device.id} className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4"><div className="flex items-start justify-between gap-4"><div><p className="flex items-center gap-2 text-sm font-semibold"><span className="h-2 w-2 rounded-full bg-emerald-500" />{device.display_name}{device.is_preferred ? " · Preferred" : ""}</p><p className="mt-1 text-xs text-[#59615e]">Browser connected · {device.last_seen_at ? `last seen ${new Date(device.last_seen_at).toLocaleString()}` : "recently connected"}</p>{device.active_context?.hostname && <p className="mt-2 text-sm text-[#27312c]">Current tab: <span className="font-medium">{device.active_context.hostname}</span></p>}</div><div className="flex shrink-0 items-center gap-2">{!device.is_preferred && <button onClick={() => void makePreferred(device.id)} className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-2.5 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50"><Check className="h-3.5 w-3.5" /> Prefer</button>}<button onClick={() => void revoke(device.id)} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-2.5 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50"><Trash2 className="h-3.5 w-3.5" /> Disconnect</button></div></div><p className="mt-3 text-[11px] text-[#68736c]">Capabilities: {device.granted_capabilities.map((value) => value.replace("_active_tab", "").replaceAll("_", " ")).join(", ") || "None"}</p></div>) : <p className="py-3 text-sm text-[#777]">No browser is currently available. Pair or reconnect Onsite48 Bridge to continue.</p>}{historicalDevices.length > 0 && <details className="mt-4"><summary className="cursor-pointer text-xs font-semibold text-[#60646c]">Offline, pending, or revoked devices ({historicalDevices.length})</summary><div className="mt-2 divide-y divide-black/5 rounded-xl bg-[#f7f8fa] px-3">{historicalDevices.map((device) => <div key={device.id} className="flex items-center justify-between gap-4 py-3"><div><p className="text-sm font-medium text-[#555]">{device.display_name}</p><p className="mt-0.5 text-xs text-[#888]">{device.lifecycle || device.status} · {device.last_seen_at ? `last seen ${new Date(device.last_seen_at).toLocaleString()}` : "not seen"}</p></div>{device.lifecycle !== "revoked" && <button onClick={() => void revoke(device.id)} className="rounded-lg border border-rose-200 bg-white px-2.5 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50">Revoke access</button>}</div>)}</div></details>}</>}</DataLoader></div></section>
  </div></main>;
}

function PairValue({ label, value, onCopy }: { label: string; value: string; onCopy: (value: string) => Promise<void> }) {
  return <div><p className="text-[11px] font-semibold uppercase tracking-wide text-indigo-700">{label}</p><div className="mt-1 flex items-center gap-2"><code className="min-w-0 flex-1 break-all rounded bg-white px-2 py-1.5 text-xs text-[#373b43]">{value}</code><button type="button" onClick={() => void onCopy(value)} className="rounded border border-indigo-200 bg-white p-1.5 text-indigo-700"><Copy className="h-3.5 w-3.5" /></button></div></div>;
}