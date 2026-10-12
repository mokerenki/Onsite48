"use client";

import React from "react";
import { MermaidDiagram } from "./MermaidDiagram";

interface MessageContentProps {
  markdown: string;
  className?: string;
}

function renderInline(text: string): React.ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.9em] text-slate-800">{part.slice(1, -1)}</code>;
    }
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*")) return <em key={index}>{part.slice(1, -1)}</em>;
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}

/** A dependency-free, safe Markdown subset for agent messages. */
export function MessageContent({ markdown, className = "" }: MessageContentProps) {
  const safeMarkdown = typeof markdown === "string" ? markdown : String(markdown ?? "");
  const lines = safeMarkdown.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let code: string[] | null = null;
  let codeLanguage = "";

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(<p key={`p-${blocks.length}`} className="whitespace-pre-wrap leading-7">{renderInline(paragraph.join("\n"))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const List = list.ordered ? "ol" : "ul";
    blocks.push(
      <List key={`l-${blocks.length}`} className={`space-y-1.5 pl-6 ${list.ordered ? "list-decimal" : "list-disc"}`}>
        {list.items.map((item, index) => <li key={index}>{renderInline(item)}</li>)}
      </List>
    );
    list = null;
  };
  const flushCode = () => {
    if (code === null) return;
    if (codeLanguage.toLowerCase() === "mermaid") {
      blocks.push(<MermaidDiagram key={`m-${blocks.length}`} source={code.join("\n")} />);
      code = null;
      codeLanguage = "";
      return;
    }
    blocks.push(
      <pre key={`c-${blocks.length}`} className="overflow-x-auto rounded-xl bg-slate-950 p-4 text-sm text-slate-100">
        {codeLanguage && <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{codeLanguage}</div>}
        <code>{code.join("\n")}</code>
      </pre>
    );
    code = null;
    codeLanguage = "";
  };

  lines.forEach((line) => {
    if (line.trimStart().startsWith("```") || code !== null) {
      if (line.trimStart().startsWith("```") && code === null) {
        flushParagraph(); flushList();
        code = [];
        codeLanguage = line.trim().slice(3).trim();
      } else if (line.trim() === "```" && code !== null) {
        flushCode();
      } else if (code !== null) {
        code.push(line);
      }
      return;
    }

    if (!line.trim()) {
      flushParagraph(); flushList();
      return;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph(); flushList();
      const level = heading[1].length;
      const Tag = level === 1 ? "h2" : level === 2 ? "h3" : "h4";
      blocks.push(<Tag key={`h-${blocks.length}`} className="font-semibold tracking-tight text-slate-950">{renderInline(heading[2])}</Tag>);
      return;
    }

    const ordered = /^\s*\d+[.)]\s+(.+)$/.exec(line);
    const unordered = /^\s*[-*]\s+(.+)$/.exec(line);
    if (ordered || unordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      const item = (ordered || unordered)![1];
      if (!list || list.ordered !== isOrdered) { flushList(); list = { ordered: isOrdered, items: [] }; }
      list.items.push(item);
      return;
    }

    flushList();
    paragraph.push(line);
  });

  flushCode();
  flushList();
  flushParagraph();

  return <div className={`space-y-3 text-[15px] text-slate-900 ${className}`}>{blocks}</div>;
}