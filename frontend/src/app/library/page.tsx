"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BookOpenText, FilePlus2, FolderPlus, Loader2, Search, Sparkles, Upload } from "lucide-react";
import { authHeaders, useAuth } from "@/components/AuthProvider";

type Collection = { id: string; name: string; description?: string | null };
type Document = { id: string; title: string; collection_id?: string | null; source_type: string; status: string; char_count: number; created_at: string };
type SearchResult = { document_id: string; document_title: string; content: string; chunk_index: number; score: number };

export default function LibraryPage() {
  const { user, status, accessToken } = useAuth();
  const [collections, setCollections] = useState<Collection[]>([]);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [query, setQuery] = useState("");
  const [collectionName, setCollectionName] = useState("");
  const [selectedCollection, setSelectedCollection] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const headers = useCallback(() => authHeaders(accessToken), [accessToken]);
  const load = useCallback(async () => {
    if (!accessToken) return;
    const [collectionResponse, documentResponse] = await Promise.all([
      fetch("/api/library/collections", { headers: headers(), credentials: "include" }),
      fetch("/api/library/documents", { headers: headers(), credentials: "include" }),
    ]);
    if (collectionResponse.ok) setCollections((await collectionResponse.json()).collections || []);
    if (documentResponse.ok) setDocuments((await documentResponse.json()).documents || []);
  }, [accessToken, headers]);

  useEffect(() => { void load(); }, [load]);

  async function addCollection(event: FormEvent) {
    event.preventDefault();
    if (!collectionName.trim()) return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/api/library/collections", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...headers() }, body: JSON.stringify({ name: collectionName }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not create the collection.");
      setCollectionName(""); setSelectedCollection(body.id); await load();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Could not create the collection."); } finally { setBusy(false); }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !accessToken) return;
    setBusy(true); setNotice(null);
    try {
      const form = new FormData(); form.append("files", file);
      const uploadResponse = await fetch("/api/files/upload", { method: "POST", credentials: "include", headers: headers(), body: form });
      const uploaded = await uploadResponse.json();
      if (!uploadResponse.ok) throw new Error(uploaded.detail || "The source upload failed.");
      const attachment = uploaded.files?.[0];
      if (!attachment?.id) throw new Error("The file upload did not return a usable source ID.");
      const indexResponse = await fetch("/api/library/documents/from-attachment", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...headers() },
        body: JSON.stringify({ attachment_id: attachment.id, collection_id: selectedCollection || null, title: attachment.name }),
      });
      const indexed = await indexResponse.json();
      if (!indexResponse.ok) throw new Error(indexed.detail || "The source could not be indexed. It may not contain readable text.");
      setNotice(`Added “${indexed.title}” to the organization Source Library.`); await load();
    } catch (error) { setNotice(error instanceof Error ? error.message : "The source could not be indexed."); } finally { setBusy(false); event.target.value = ""; }
  }

  async function search(event: FormEvent) {
    event.preventDefault(); if (!query.trim()) return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch(`/api/library/search?q=${encodeURIComponent(query.trim())}`, { headers: headers(), credentials: "include" });
      const body = await response.json(); if (!response.ok) throw new Error(body.detail || "Source search failed.");
      setResults(body.results || []);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Source search failed."); } finally { setBusy(false); }
  }

  if (status === "loading") return <div className="grid min-h-screen place-items-center bg-[#f8f8f6] text-sm text-[#6a6a67]">Opening Source Library…</div>;
  if (!user) return <div className="grid min-h-screen place-items-center bg-[#f8f8f6] text-sm text-[#6a6a67]">Sign in to access the Source Library.</div>;

  return <main className="min-h-screen bg-[#f7f8fa] px-5 py-6 text-[#1b1b1b] sm:px-8">
    <div className="mx-auto max-w-6xl">
      <Link href="/" className="mb-5 inline-flex items-center gap-1.5 text-sm text-[#60646c] hover:text-black"><ArrowLeft className="h-4 w-4" /> Workspace</Link>
      <header className="flex flex-col justify-between gap-4 border-b border-black/10 pb-6 sm:flex-row sm:items-end"><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-600">Organization intelligence</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Source Library</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-[#60646c]">Approved internal sources that Onsite48 can retrieve for grounded work. Sources stay scoped to {user.organization_name}.</p></div><div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-800"><Sparkles className="mr-1 inline h-3.5 w-3.5" /> Agent search is organization-scoped and returns source excerpts.</div></header>

      {notice && <p className="mt-4 rounded-lg border border-black/10 bg-white px-3 py-2 text-sm text-[#4f5560]">{notice}</p>}
      <div className="mt-6 grid gap-5 lg:grid-cols-[280px_1fr]">
        <aside className="space-y-4"><section className="rounded-2xl border border-black/10 bg-white p-4"><div className="flex items-center gap-2"><FolderPlus className="h-4 w-4 text-indigo-600" /><h2 className="font-semibold">Collections</h2></div><form onSubmit={addCollection} className="mt-3 space-y-2"><input value={collectionName} onChange={(e) => setCollectionName(e.target.value)} className="w-full rounded-lg border border-black/10 px-3 py-2 text-sm outline-none focus:border-indigo-500" placeholder="e.g. Brand strategy" /><button disabled={busy} className="w-full rounded-lg bg-[#171717] px-3 py-2 text-sm font-medium text-white disabled:opacity-50">Create collection</button></form><button onClick={() => setSelectedCollection("")} className={`mt-4 w-full rounded-lg px-3 py-2 text-left text-sm ${!selectedCollection ? "bg-indigo-50 text-indigo-800" : "hover:bg-[#f5f5f3]"}`}>All sources <span className="float-right text-xs text-[#777]">{documents.length}</span></button>{collections.map((item) => <button key={item.id} onClick={() => setSelectedCollection(item.id)} className={`mt-1 w-full rounded-lg px-3 py-2 text-left text-sm ${selectedCollection === item.id ? "bg-indigo-50 text-indigo-800" : "hover:bg-[#f5f5f3]"}`}>{item.name}</button>)}</section></aside>
        <section className="space-y-5"><section className="rounded-2xl border border-black/10 bg-white p-5"><div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div><h2 className="font-semibold">Add a source</h2><p className="mt-1 text-sm text-[#60646c]">PDFs, Office files, spreadsheets, text, and code are extracted before indexing.</p></div><label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg bg-[#171717] px-3 py-2 text-sm font-medium text-white"><Upload className="h-4 w-4" /> {busy ? "Working…" : "Upload source"}<input type="file" onChange={upload} disabled={busy} className="hidden" /></label></div></section>
          <section className="rounded-2xl border border-black/10 bg-white p-5"><form onSubmit={search} className="flex gap-2"><div className="flex flex-1 items-center gap-2 rounded-lg border border-black/10 px-3"><Search className="h-4 w-4 text-[#777]" /><input value={query} onChange={(e) => setQuery(e.target.value)} className="w-full py-2.5 text-sm outline-none" placeholder="Search approved sources" /></div><button disabled={busy} className="rounded-lg border border-black/10 px-4 text-sm font-medium hover:bg-[#f5f5f3] disabled:opacity-50">Search</button></form>{results.length > 0 && <div className="mt-4 space-y-3">{results.map((result) => <article key={`${result.document_id}-${result.chunk_index}`} className="rounded-xl bg-[#f7f8fa] p-3"><p className="text-xs font-semibold text-indigo-700">{result.document_title} · excerpt {result.chunk_index + 1}</p><p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-[#3d4148]">{result.content}</p></article>)}</div>}</section>
          <section className="rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-center gap-2"><BookOpenText className="h-4 w-4 text-indigo-600" /><h2 className="font-semibold">Indexed sources</h2></div><div className="mt-3 divide-y divide-black/5">{documents.filter((doc) => !selectedCollection || doc.collection_id === selectedCollection).map((doc) => <div key={doc.id} className="flex items-center justify-between gap-4 py-3"><div className="min-w-0"><p className="truncate text-sm font-medium">{doc.title}</p><p className="mt-0.5 text-xs text-[#777]">{doc.char_count.toLocaleString()} characters · {doc.source_type}</p></div><span className="shrink-0 rounded-full bg-emerald-50 px-2 py-1 text-xs text-emerald-700">{doc.status}</span></div>)}{documents.length === 0 && <div className="py-8 text-center text-sm text-[#777]"><FilePlus2 className="mx-auto mb-2 h-5 w-5" />Add your first approved company source.</div>}</div></section>
        </section>
      </div>
    </div>
  </main>;
}