"use client";

import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { authHeaders } from "@/components/AuthProvider";

type ArtifactDownloadButtonProps = {
  name: string;
  downloadUrl: string;
  accessToken: string | null;
  expectedContentType?: string;
  className?: string;
  compact?: boolean;
};

function filenameFromDisposition(value: string | null, fallback: string) {
  if (!value) return fallback;
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded); } catch { return fallback; }
  }
  const quoted = value.match(/filename="([^"]+)"/i)?.[1];
  return quoted || fallback;
}

function isExpectedContentType(actual: string, expected?: string) {
  if (!expected) return true;
  return actual.toLowerCase().split(";", 1)[0].trim() === expected.toLowerCase().split(";", 1)[0].trim();
}

async function responseError(response: Response) {
  const raw = (await response.text()).slice(0, 500);
  try {
    const parsed = JSON.parse(raw) as { detail?: string; message?: string };
    return parsed.detail || parsed.message || `Download failed with status ${response.status}.`;
  } catch {
    return raw || `Download failed with status ${response.status}.`;
  }
}

export function ArtifactDownloadButton({ name, downloadUrl, accessToken, expectedContentType = "text/markdown", className = "", compact = false }: ArtifactDownloadButtonProps) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setDownloading(true); setError(null);
    try {
      const response = await fetch(downloadUrl, {
        headers: authHeaders(accessToken),
        credentials: "include",
      });
      if (!response.ok) throw new Error(await responseError(response));
      const contentType = response.headers.get("content-type") || "";
      if (!isExpectedContentType(contentType, expectedContentType)) {
        throw new Error(`Onsite48 received ${contentType || "an unknown content type"} instead of the requested ${expectedContentType} artifact. The file was not saved.`);
      }
      const blob = await response.blob();
      if (blob.size === 0) throw new Error("The generated artifact was empty and was not saved.");
      const filename = filenameFromDisposition(response.headers.get("content-disposition"), name);
      const typedBlob = new Blob([blob], { type: expectedContentType });
      const objectUrl = URL.createObjectURL(typedBlob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to download this artifact.");
    } finally {
      setDownloading(false);
    }
  }

  return <span className="inline-flex flex-col items-start gap-1">
    <button
      type="button"
      onClick={() => void download()}
      disabled={downloading}
      className={className || "inline-flex items-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-sm font-medium text-brand-700 transition-colors hover:bg-brand-100 disabled:opacity-60"}
      title={`Download ${name}`}
    >
      {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      {compact ? "Download" : downloading ? "Preparing download" : "Download Markdown report"}
    </button>
    {error && <span role="alert" className="max-w-xs text-xs leading-4 text-rose-700">{error}</span>}
  </span>;
}