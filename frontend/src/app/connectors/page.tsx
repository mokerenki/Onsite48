"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Braces, CheckCircle2, ChevronDown, Code2, KeyRound, Link2, Loader2, PlugZap, Plus, Radio, ServerCog, ShieldCheck, TestTube2, Trash2, Unplug } from "lucide-react";
import { authHeaders, useAuth } from "@/components/AuthProvider";
import { AuthScreen } from "@/components/AuthScreen";

type Connector = {
  id: string; name: string; description: string; category: string; auth_type: "oauth2" | "api_key"; enabled: boolean; required_environment: string[];
  connection: { status: "disconnected" | "connected" | "error" | "expired"; account_name?: string | null; scopes: string[]; last_error?: string | null };
};
type CustomConnector = {
  id: string; name: string; slug: string; kind: "api" | "mcp"; base_url: string; health_path?: string | null; description?: string | null; usage_guide?: string | null;
  auth_mode: string; credential_name?: string | null; header_names: string[]; has_credentials: boolean; status: "configured" | "connected" | "error"; last_tested_at?: string | null; last_error?: string | null;
};
type CustomMode = "api" | "mcp" | "import" | "url";

async function readError(response: Response) {
  const body = await response.json().catch(() => ({}));
  return body.message || body.detail || "The connection could not be updated.";
}

const initialForm = {
  name: "", baseUrl: "", healthPath: "", description: "", usageGuide: "", authMode: "none", credentialName: "", secret: "", headerName: "", headerValue: "", importJson: "",
};

export default function ConnectorsPage() {
  const { accessToken, user } = useAuth();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [customConnectors, setCustomConnectors] = useState<CustomConnector[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [apiKeyTarget, setApiKeyTarget] = useState<Connector | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [accountName, setAccountName] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [customMode, setCustomMode] = useState<CustomMode | null>(null);
  const [form, setForm] = useState(initialForm);

  const load = useCallback(async () => {
    if (!accessToken) return;
    setLoading(true);
    try {
      const headers = authHeaders(accessToken);
      const [builtIn, custom] = await Promise.all([
        fetch("/api/connectors", { headers, credentials: "include" }),
        fetch("/api/custom-connectors", { headers, credentials: "include" }),
      ]);
      if (!builtIn.ok) throw new Error(await readError(builtIn));
      if (!custom.ok) throw new Error(await readError(custom));
      setConnectors((await builtIn.json()) as Connector[]);
      setCustomConnectors(((await custom.json()).connectors || []) as CustomConnector[]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load connectors.");
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    function listener(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === "connector_success") void load();
      if (event.data?.type === "connector_error") setError("The provider could not complete the connection. Try again or check its consent settings.");
    }
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [load]);

  function setField(name: keyof typeof initialForm, value: string) { setForm((current) => ({ ...current, [name]: value })); }
  function openCreate(mode: CustomMode) { setForm(initialForm); setCustomMode(mode); setCreateOpen(false); setError(null); }
  function closeCustomModal() { setCustomMode(null); setForm(initialForm); }

  async function connectOAuth(connector: Connector) {
    setWorkingId(connector.id);
    try {
      const response = await fetch(`/api/connectors/${connector.id}/oauth/start`, { headers: authHeaders(accessToken), credentials: "include" });
      if (!response.ok) throw new Error(await readError(response));
      const { authorization_url } = (await response.json()) as { authorization_url: string };
      const popup = window.open(authorization_url, `onsite48-${connector.id}`, "popup,width=560,height=720");
      if (!popup) throw new Error("Your browser blocked the connection window. Allow popups and try again.");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to start connector authorization."); }
    finally { setWorkingId(null); }
  }

  async function connectApiKey() {
    if (!apiKeyTarget || !apiKey.trim()) return;
    setWorkingId(apiKeyTarget.id);
    try {
      const response = await fetch(`/api/connectors/${apiKeyTarget.id}/api-key`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, body: JSON.stringify({ api_key: apiKey, account_name: accountName || undefined }) });
      if (!response.ok) throw new Error(await readError(response));
      setApiKeyTarget(null); setApiKey(""); setAccountName(""); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to save the API key."); }
    finally { setWorkingId(null); }
  }

  async function disconnect(connector: Connector) {
    setWorkingId(connector.id);
    try {
      const response = await fetch(`/api/connectors/${connector.id}`, { method: "DELETE", headers: authHeaders(accessToken), credentials: "include" });
      if (!response.ok) throw new Error(await readError(response));
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to disconnect this tool."); }
    finally { setWorkingId(null); }
  }

  async function createCustom(event: FormEvent) {
    event.preventDefault();
    if (!customMode) return;
    const creatingId = `create-${customMode}`;
    setWorkingId(creatingId); setError(null);
    try {
      let response: Response;
      if (customMode === "api") {
        response = await fetch("/api/custom-connectors/apis", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, body: JSON.stringify({ name: form.name, base_url: form.baseUrl, health_path: form.healthPath || undefined, description: form.description || undefined, usage_guide: form.usageGuide || undefined, auth_mode: form.authMode, credential_name: form.credentialName || undefined, secret: form.secret || undefined }) });
      } else if (customMode === "import") {
        response = await fetch("/api/custom-connectors/mcps/import", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, body: JSON.stringify({ config_json: form.importJson }) });
      } else {
        response = await fetch("/api/custom-connectors/mcps", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) }, body: JSON.stringify({ name: form.name, endpoint: form.baseUrl, description: form.description || undefined, usage_guide: form.usageGuide || undefined, headers: form.headerName && form.headerValue ? [{ name: form.headerName, value: form.headerValue }] : [] }) });
      }
      if (!response.ok) throw new Error(await readError(response));
      const created = await response.json() as CustomConnector;
      closeCustomModal();
      setNotice(`${created.name} was created. Test it before using it in a task.`);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to create the custom connector."); }
    finally { setWorkingId(null); }
  }

  async function testCustom(connector: CustomConnector) {
    setWorkingId(connector.id); setError(null);
    try {
      const response = await fetch(`/api/custom-connectors/${connector.id}/test`, { method: "POST", credentials: "include", headers: authHeaders(accessToken) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.detail || "The connector test did not complete.");
      setNotice(`${connector.name} connected successfully${connector.kind === "mcp" && result.tools ? ` with ${result.tools.length} tool${result.tools.length === 1 ? "" : "s"}` : ""}.`);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "The connector test did not complete."); await load(); }
    finally { setWorkingId(null); }
  }

  async function removeCustom(connector: CustomConnector) {
    if (!confirm(`Remove ${connector.name}? Its encrypted credential envelope will be deleted.`)) return;
    setWorkingId(connector.id);
    try {
      const response = await fetch(`/api/custom-connectors/${connector.id}`, { method: "DELETE", credentials: "include", headers: authHeaders(accessToken) });
      if (!response.ok) throw new Error(await readError(response));
      setNotice(`${connector.name} was removed.`); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to remove the custom connector."); }
    finally { setWorkingId(null); }
  }

  if (!user) return <AuthScreen />;
  const authNeedsSecret = ["bearer", "api_key_header", "api_key_query", "custom_header"].includes(form.authMode);

  return (
    <main className="min-h-screen bg-[#f8f8f6] px-5 py-7 text-[#181817] sm:px-8"><div className="mx-auto max-w-6xl">
      <div className="mb-8 flex items-start justify-between gap-4"><div><Link href="/" className="inline-flex items-center gap-1.5 text-sm text-[#6c6c68] hover:text-black"><ArrowLeft className="h-4 w-4" /> Workspace</Link><h1 className="mt-4 text-3xl font-semibold tracking-[-0.045em]">Your connected tools</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-[#676763]">Onsite48 accesses only accounts and endpoints you explicitly connect in {user.organization_name}. Provider write actions and every custom MCP call require exact confirmation in the workspace.</p></div>
        <div className="relative flex items-center gap-3"><Link href="/connectors/marketplace" className="hidden rounded-xl border border-black/10 bg-white px-3 py-2 text-xs font-medium text-[#4b4b47] hover:bg-[#f5f5f2] sm:inline-flex">Browse marketplace</Link><div className="hidden rounded-xl border border-black/10 bg-white px-3 py-2 text-xs text-[#666] sm:flex sm:items-center sm:gap-2"><ShieldCheck className="h-4 w-4 text-green-600" /> Encrypted credentials</div><button onClick={() => setCreateOpen((open) => !open)} className="inline-flex items-center gap-1.5 rounded-xl bg-[#181818] px-3 py-2 text-xs font-semibold text-white hover:bg-[#343434]"><Plus className="h-3.5 w-3.5" /> Create <ChevronDown className="h-3.5 w-3.5" /></button>{createOpen && <div className="absolute right-0 top-11 z-20 w-56 rounded-xl border border-black/10 bg-white p-1.5 shadow-xl"><CreateOption icon={<Code2 className="h-4 w-4" />} title="Custom API" detail="Connect a documented HTTPS API" onClick={() => openCreate("api")} /><CreateOption icon={<ServerCog className="h-4 w-4" />} title="Custom MCP" detail="Add a remote SSE MCP server" onClick={() => openCreate("mcp")} /><CreateOption icon={<Braces className="h-4 w-4" />} title="Import MCP by JSON" detail="Import one remote MCP config" onClick={() => openCreate("import")} /><CreateOption icon={<Radio className="h-4 w-4" />} title="Add MCP by URL" detail="Paste an HTTPS SSE endpoint" onClick={() => openCreate("url")} /></div>}</div>
      </div>
      {error && <div className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}{notice && <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</div>}
      <section className="mb-8"><div className="mb-3 flex items-end justify-between gap-3"><div><h2 className="text-lg font-semibold">Custom connectors</h2><p className="mt-1 text-sm text-[#676763]">Remote HTTPS endpoints only. Onsite48 blocks local and private network targets; secrets are encrypted and never displayed again.</p></div></div>{loading ? <Loading /> : customConnectors.length ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{customConnectors.map((connector) => <CustomCard key={connector.id} connector={connector} busy={workingId === connector.id} onTest={() => void testCustom(connector)} onDelete={() => void removeCustom(connector)} />)}</div> : <div className="rounded-2xl border border-dashed border-black/15 bg-white px-5 py-8 text-sm text-[#72726d]">Create a Custom API, Custom MCP, import an MCP JSON file, or add a remote MCP URL. Test a connector before it becomes available to Onsite48 tasks.</div>}</section>
      <section><div className="mb-3"><h2 className="text-lg font-semibold">Built-in connectors</h2><p className="mt-1 text-sm text-[#676763]">Preconfigured OAuth and API-key connections.</p></div>{loading ? <Loading /> : <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{connectors.map((connector) => { const connected = connector.connection.status === "connected"; const busy = workingId === connector.id; return <article key={connector.id} className="flex min-h-64 flex-col rounded-2xl border border-black/10 bg-white p-5 shadow-[0_2px_10px_rgba(0,0,0,0.025)]"><div className="flex items-start justify-between gap-3"><div className="grid h-10 w-10 place-items-center rounded-xl bg-[#f0f2ff] text-[#455ed3]"><PlugZap className="h-5 w-5" /></div>{connected ? <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700"><CheckCircle2 className="h-3.5 w-3.5" /> Connected</span> : <span className="rounded-full bg-[#f3f3f0] px-2.5 py-1 text-xs font-medium text-[#6d6d68]">{connector.category}</span>}</div><h3 className="mt-5 text-lg font-semibold">{connector.name}</h3><p className="mt-2 text-sm leading-6 text-[#686864]">{connector.description}</p><div className="mt-auto pt-5">{connected ? <><p className="mb-3 text-xs text-[#74746f]">{connector.connection.account_name ? `Connected as ${connector.connection.account_name}` : "Connected to your workspace"}</p><button disabled={busy} onClick={() => void disconnect(connector)} className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-black/10 text-sm font-medium hover:bg-red-50 hover:text-red-700 disabled:opacity-50">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Unplug className="h-4 w-4" />} Disconnect</button></> : connector.enabled ? <button disabled={busy} onClick={() => connector.auth_type === "oauth2" ? void connectOAuth(connector) : setApiKeyTarget(connector)} className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#181818] text-sm font-medium text-white hover:bg-[#393939] disabled:opacity-50">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : connector.auth_type === "oauth2" ? <Link2 className="h-4 w-4" /> : <KeyRound className="h-4 w-4" />}{connector.auth_type === "oauth2" ? "Connect account" : "Add API key"}</button> : <p className="rounded-xl bg-[#fafaf8] px-3 py-2 text-xs leading-5 text-[#77736e]">This integration will be enabled once its provider credentials are configured on the Onsite48AI server.</p>}</div></article>; })}</div>}</section>
    </div>
    {apiKeyTarget && <div className="fixed inset-0 z-30 grid place-items-center bg-black/40 p-5"><div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-lg font-semibold">Connect {apiKeyTarget.name}</h2><p className="mt-1 text-sm text-[#6b6b67]">Your key is encrypted before it is stored and is never shown again.</p><label className="mt-5 block text-sm font-medium">Account label (optional)<input value={accountName} onChange={(event) => setAccountName(event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="Marketing workspace" /></label><label className="mt-4 block text-sm font-medium">API key<input autoFocus type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="Paste your provider key" /></label><div className="mt-6 flex justify-end gap-3"><button onClick={() => setApiKeyTarget(null)} className="rounded-lg px-3 py-2 text-sm">Cancel</button><button disabled={!apiKey.trim() || workingId === apiKeyTarget.id} onClick={() => void connectApiKey()} className="rounded-lg bg-[#181818] px-4 py-2 text-sm font-medium text-white">Save connection</button></div></div></div>}
    {customMode && <div className="fixed inset-0 z-30 grid place-items-center overflow-y-auto bg-black/40 p-5"><form onSubmit={createCustom} className="my-8 w-full max-w-xl rounded-2xl bg-white p-6 shadow-xl"><div className="flex items-start justify-between gap-4"><div><h2 className="text-lg font-semibold">{customMode === "api" ? "Create Custom API" : customMode === "import" ? "Import MCP by JSON" : customMode === "url" ? "Add MCP by URL" : "Create Custom MCP"}</h2><p className="mt-1 text-sm leading-6 text-[#6b6b67]">{customMode === "api" ? "Connect a remote HTTPS API. GET calls are bounded; write calls always ask for exact approval." : customMode === "import" ? "Paste a configuration containing exactly one remote HTTPS SSE MCP server. Local commands and private endpoints are rejected." : "Connect an HTTPS Server-Sent Events MCP endpoint. Every tool list and call is approval-gated because an MCP server is third-party code."}</p></div><button type="button" onClick={closeCustomModal} className="text-sm text-[#777] hover:text-black">Close</button></div>
      {customMode === "import" ? <label className="mt-5 block text-sm font-medium">MCP JSON<textarea required autoFocus value={form.importJson} onChange={(event) => setField("importJson", event.target.value)} className="mt-1.5 h-56 w-full rounded-lg border border-black/10 p-3 font-mono text-xs" placeholder={'{\n  "mcpServers": {\n    "my-service": { "url": "https://example.com/sse" }\n  }\n}'} /></label> : <>
        <div className="mt-5 grid gap-4 sm:grid-cols-2"><label className="text-sm font-medium">Connector name<input required autoFocus value={form.name} onChange={(event) => setField("name", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="Marketing analytics" /></label><label className="text-sm font-medium">{customMode === "api" ? "HTTPS base URL" : "HTTPS SSE endpoint"}<input required type="url" value={form.baseUrl} onChange={(event) => setField("baseUrl", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder={customMode === "api" ? "https://api.example.com" : "https://example.com/sse"} /></label></div>
        <label className="mt-4 block text-sm font-medium">What should Onsite48 use this for? <span className="font-normal text-[#777]">(optional)</span><input value={form.description} onChange={(event) => setField("description", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="Read campaign metrics and audience segments" /></label><label className="mt-4 block text-sm font-medium">Usage note for Onsite48 <span className="font-normal text-[#777]">(optional)</span><textarea value={form.usageGuide} onChange={(event) => setField("usageGuide", event.target.value)} className="mt-1.5 h-20 w-full rounded-lg border border-black/10 p-3 text-sm" placeholder="Use only for reporting; do not create or modify records." /></label>
        {customMode === "api" && <><div className="mt-4 grid gap-4 sm:grid-cols-2"><label className="text-sm font-medium">Health path <span className="font-normal text-[#777]">(optional)</span><input value={form.healthPath} onChange={(event) => setField("healthPath", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="/v1/status" /></label><label className="text-sm font-medium">Authentication<select value={form.authMode} onChange={(event) => setField("authMode", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 bg-white px-3"><option value="none">No authentication</option><option value="bearer">Bearer token</option><option value="api_key_header">API key header</option><option value="api_key_query">API key query parameter</option><option value="custom_header">Custom header</option></select></label></div>{authNeedsSecret && <div className="mt-4 grid gap-4 sm:grid-cols-2"><label className="text-sm font-medium">{form.authMode === "bearer" ? "Token" : "Header or parameter name"}<input required={form.authMode !== "bearer"} value={form.credentialName} onChange={(event) => setField("credentialName", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder={form.authMode === "api_key_query" ? "api_key" : "X-API-Key"} /></label><label className="text-sm font-medium">Secret<input required type="password" autoComplete="new-password" value={form.secret} onChange={(event) => setField("secret", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="Stored encrypted; never shown again" /></label></div>}</>}
        {(customMode === "mcp") && <div className="mt-4 grid gap-4 sm:grid-cols-2"><label className="text-sm font-medium">Header name <span className="font-normal text-[#777]">(optional)</span><input value={form.headerName} onChange={(event) => setField("headerName", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="Authorization" /></label><label className="text-sm font-medium">Header value <span className="font-normal text-[#777]">(optional)</span><input type="password" autoComplete="new-password" value={form.headerValue} onChange={(event) => setField("headerValue", event.target.value)} className="mt-1.5 h-10 w-full rounded-lg border border-black/10 px-3" placeholder="Stored encrypted; never shown again" /></label></div>}
      </>}
      <div className="mt-6 flex items-center justify-between gap-3"><p className="text-xs leading-5 text-[#777]">Secrets are encrypted at rest. The test action uses the registered endpoint only; it never reveals credentials in the response.</p><button disabled={workingId === `create-${customMode}`} className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-[#181818] px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{workingId === `create-${customMode}` && <Loader2 className="h-4 w-4 animate-spin" />} Create connector</button></div>
    </form></div>}
    </main>
  );
}

function Loading() { return <div className="grid h-40 place-items-center text-sm text-[#777]"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> Loading connectors…</div>; }
function CreateOption({ icon, title, detail, onClick }: { icon: React.ReactNode; title: string; detail: string; onClick: () => void }) { return <button onClick={onClick} className="flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-[#f6f6f3]"><span className="mt-0.5 text-[#555]">{icon}</span><span><span className="block text-sm font-medium">{title}</span><span className="block text-[11px] text-[#777]">{detail}</span></span></button>; }
function CustomCard({ connector, busy, onTest, onDelete }: { connector: CustomConnector; busy: boolean; onTest: () => void; onDelete: () => void }) { const connected = connector.status === "connected"; return <article className="flex min-h-64 flex-col rounded-2xl border border-black/10 bg-white p-5 shadow-[0_2px_10px_rgba(0,0,0,0.025)]"><div className="flex items-start justify-between gap-3"><div className="grid h-10 w-10 place-items-center rounded-xl bg-violet-50 text-violet-700">{connector.kind === "api" ? <Code2 className="h-5 w-5" /> : <ServerCog className="h-5 w-5" />}</div><span className={`rounded-full px-2.5 py-1 text-xs font-medium ${connected ? "bg-green-50 text-green-700" : connector.status === "error" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-800"}`}>{connected ? "Connected" : connector.status === "error" ? "Needs attention" : "Test required"}</span></div><h3 className="mt-5 text-lg font-semibold">{connector.name}</h3><p className="mt-1 text-xs font-medium uppercase tracking-wide text-[#777]">Custom {connector.kind === "api" ? "API" : "MCP"} · {connector.slug}</p><p className="mt-2 line-clamp-2 text-sm leading-6 text-[#686864]">{connector.description || connector.base_url}</p><p className="mt-3 break-all text-xs text-[#888]">{connector.base_url}</p>{connector.last_error && <p className="mt-2 line-clamp-2 text-xs text-rose-700">Last test: {connector.last_error}</p>}<div className="mt-auto grid grid-cols-[1fr_auto] gap-2 pt-5"><button disabled={busy} onClick={onTest} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-[#181818] text-sm font-medium text-white hover:bg-[#393939] disabled:opacity-50">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />} Test connection</button><button disabled={busy} onClick={onDelete} className="grid h-10 w-10 place-items-center rounded-xl border border-rose-200 text-rose-700 hover:bg-rose-50 disabled:opacity-50" title="Remove custom connector"><Trash2 className="h-4 w-4" /></button></div></article>; }