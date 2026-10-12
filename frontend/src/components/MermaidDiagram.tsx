"use client";

import { useEffect, useId, useState } from "react";
import mermaid from "mermaid";

interface MermaidDiagramProps {
  source: string;
}

let configured = false;
function configureMermaid() {
  if (configured) return;
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral", fontFamily: "inherit" });
  configured = true;
}

/**
 * Executable constructs that must never reach `dangerouslySetInnerHTML`.
 *
 * `securityLevel: "strict"` already escapes labels and disables Mermaid's click
 * callbacks, so this is defence in depth rather than the primary control: the
 * diagram source originates from model output and provider content, and this is
 * the one place the app injects generated markup. The check is deliberately
 * reject-only -- when anything executable appears the diagram is not rendered at
 * all, because a false positive costs a picture and a false negative costs the
 * page.
 */
const UNSAFE_SVG_PATTERNS: RegExp[] = [
  /<script/i,
  /<iframe/i,
  /<object/i,
  /<embed/i,
  /<foreignObject/i,
  /\son[a-z]+\s*=/i,
  /javascript:/i,
  /data:text\/html/i,
];

// Any href/xlink:href value. External references pull in markup this page does
// not control, so every value is inspected rather than pattern-matched.
const REFERENCE_ATTRIBUTE = /\b(?:href|xlink:href)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi;

export function isSafeMermaidSvg(svg: string): boolean {
  if (typeof svg !== "string" || !svg) return false;
  if (UNSAFE_SVG_PATTERNS.some((pattern) => pattern.test(svg))) return false;
  for (const match of svg.matchAll(REFERENCE_ATTRIBUTE)) {
    // `href="#id"` is a same-document fragment and is how Mermaid wires arrows.
    const value = (match[1] ?? match[2] ?? match[3] ?? "").trim();
    if (!value.startsWith("#")) return false;
  }
  return true;
}

export function MermaidDiagram({ source }: MermaidDiagramProps) {
  const id = `onsite48-mermaid-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    configureMermaid();
    setSvg("");
    setError("");
    mermaid.parse(source, { suppressErrors: false })
      .then(() => mermaid.render(id, source))
      .then(({ svg: rendered }) => {
        if (!active) return;
        if (!isSafeMermaidSvg(rendered)) {
          // The source is still shown below, so the content is not lost --
          // it just is not injected as markup.
          setError("the rendered diagram contained unsupported markup");
          return;
        }
        setSvg(rendered);
      })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Invalid Mermaid diagram."); });
    return () => { active = false; };
  }, [id, source]);

  return (
    <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-3">
      {svg ? <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: svg }} /> : null}
      {error ? <p className="text-sm text-amber-700">The diagram could not be rendered: {error}</p> : null}
      <details>
        <summary className="cursor-pointer text-xs font-medium text-slate-500">View Mermaid source</summary>
        <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100"><code>{source}</code></pre>
      </details>
    </div>
  );
}