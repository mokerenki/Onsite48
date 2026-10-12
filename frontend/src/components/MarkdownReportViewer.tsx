"use client";

import { useEffect, useMemo, useState } from "react";
import { FileText, Loader2, X } from "lucide-react";
import { authHeaders } from "@/components/AuthProvider";
import { ArtifactDownloadButton } from "@/components/ArtifactDownloadButton";

type MarkdownReportViewerProps = {
  name: string;
  downloadUrl: string;
  accessToken: string | null;
  onClose: () => void;
};

type MarkdownBlock =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "code"; text: string }
  | { kind: "table"; rows: string[][] };

function splitRow(line: string) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function isTableDivider(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) { blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] }); index += 1; continue; }
    if (line.startsWith("```")) {
      const code: string[] = []; index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push({ kind: "code", text: code.join("\n") }); continue;
    }
    if (line.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const rows = [splitRow(line)]; index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) rows.push(splitRow(lines[index++]));
      blocks.push({ kind: "table", rows }); continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) items.push(lines[index++].replace(/^\s*[-*+]\s+/, ""));
      blocks.push({ kind: "list", items }); continue;
    }
    const paragraph = [line.trim()]; index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,4})\s+|^```|^\s*[-*+]\s+/.test(lines[index])) paragraph.push(lines[index++].trim());
    blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
  }
  return blocks;
}

export function MarkdownReportViewer({ name, downloadUrl, accessToken, onClose }: MarkdownReportViewerProps) {
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null); setSource("");
    void fetch(downloadUrl, { headers: authHeaders(accessToken), credentials: "include", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("The Markdown report could not be opened. You can still download it.");
        const contentType = response.headers.get("content-type") || "";
        if (contentType.toLowerCase().split(";", 1)[0].trim() !== "text/markdown") {
          throw new Error(`Onsite48 received ${contentType || "an unknown content type"} instead of a Markdown report.`);
        }
        return response.text();
      })
      .then((value) => setSource(value))
      .catch((reason) => { if (reason.name !== "AbortError") setError(reason instanceof Error ? reason.message : "Unable to open this report."); })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [accessToken, downloadUrl]);

  const blocks = useMemo(() => parseMarkdown(source), [source]);

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/55 p-4" role="dialog" aria-modal="true" aria-label={`Markdown report: ${name}`}>
      <section className="flex h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-5 py-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-semibold text-slate-900"><FileText className="h-4 w-4 text-brand-600" /> Report reader</p>
            <p className="truncate text-xs text-slate-500">{name}</p>
          </div>
          <div className="flex items-center gap-2">
            <ArtifactDownloadButton
              name={name}
              downloadUrl={downloadUrl}
              accessToken={accessToken}
              compact
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-60"
            />
            <button onClick={onClose} className="grid h-8 w-8 place-items-center rounded-lg text-slate-500 hover:bg-slate-200 hover:text-slate-900" title="Close report reader"><X className="h-4 w-4" /></button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto bg-white px-6 py-7 sm:px-10">
          {loading && <div className="grid h-full place-items-center text-sm text-slate-500"><span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Opening report…</span></div>}
          {error && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">{error}</div>}
          {!loading && !error && <article className="prose-like mx-auto max-w-3xl text-[15px] leading-7 text-slate-800">
            {blocks.map((block, index) => {
              if (block.kind === "heading") {
                const className = block.level === 1 ? "mt-0 text-3xl font-semibold tracking-tight text-slate-950" : block.level === 2 ? "mt-9 text-xl font-semibold text-slate-900" : "mt-6 text-base font-semibold text-slate-900";
                return <h2 key={index} className={className}>{block.text}</h2>;
              }
              if (block.kind === "list") return <ul key={index} className="my-4 list-disc space-y-1.5 pl-5">{block.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>;
              if (block.kind === "code") return <pre key={index} className="my-5 overflow-x-auto rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-100"><code>{block.text}</code></pre>;
              if (block.kind === "table") return <div key={index} className="my-5 overflow-x-auto rounded-xl border border-slate-200"><table className="min-w-full text-left text-sm"><thead className="bg-slate-50"><tr>{block.rows[0].map((cell, cellIndex) => <th key={cellIndex} className="whitespace-nowrap border-b border-slate-200 px-3 py-2 font-semibold text-slate-700">{cell}</th>)}</tr></thead><tbody>{block.rows.slice(1).map((row, rowIndex) => <tr key={rowIndex} className="border-b border-slate-100 last:border-0">{row.map((cell, cellIndex) => <td key={cellIndex} className="px-3 py-2 align-top text-slate-700">{cell}</td>)}</tr>)}</tbody></table></div>;
              return <p key={index} className="my-4 whitespace-pre-wrap">{block.text}</p>;
            })}
          </article>}
        </main>
      </section>
    </div>
  );
}