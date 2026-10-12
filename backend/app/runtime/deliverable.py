"""Deliverable quality loop: render_preview + deterministic lint.

Converts docx/pptx/xlsx/pdf/html to PNG page previews in the workspace so the
agent (and finish verifier) can inspect visual quality before deliver_file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger("onsite48.runtime.deliverable")

SUPPORTED_EXT = {".docx", ".pptx", ".xlsx", ".pdf", ".html", ".htm"}


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def render_preview(
    path: str,
    workspace_root: str,
    *,
    pages: Optional[list[int]] = None,
    out_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Render a deliverable to PNG pages under workspace/.previews/.

    Returns:
      {
        ok, path, previews: [{page, png_path, width, height}],
        lint: {...}, error?
      }
    """
    src = Path(path)
    if not src.is_file():
        # try relative to workspace
        candidate = Path(workspace_root) / path
        if candidate.is_file():
            src = candidate
        else:
            return {"ok": False, "error": f"file not found: {path}", "previews": [], "lint": {}}

    ext = src.suffix.lower()
    if ext not in SUPPORTED_EXT:
        return {"ok": False, "error": f"unsupported type {ext}", "previews": [], "lint": {}}

    preview_root = Path(out_dir or (Path(workspace_root) / ".previews" / src.stem))
    preview_root.mkdir(parents=True, exist_ok=True)

    lint = lint_deliverable(src, ext)
    previews: list[dict[str, Any]] = []
    error = None

    try:
        if ext == ".pdf":
            previews = _render_pdf(src, preview_root, pages)
        elif ext in (".html", ".htm"):
            previews = _render_html(src, preview_root)
        elif ext in (".docx", ".pptx", ".xlsx"):
            # LibreOffice → PDF → PNG
            pdf_path = preview_root / f"{src.stem}.pdf"
            lo = _which("soffice") or _which("libreoffice")
            if not lo:
                error = "LibreOffice (soffice) not installed; cannot convert office docs"
                # Still return lint
            else:
                cmd = [
                    lo, "--headless", "--nologo", "--nolockcheck",
                    "--convert-to", "pdf", "--outdir", str(preview_root),
                    str(src),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if proc.returncode != 0 or not pdf_path.exists():
                    # LibreOffice names output after input stem
                    candidates = list(preview_root.glob("*.pdf"))
                    if candidates:
                        pdf_path = candidates[0]
                    else:
                        error = f"LibreOffice conversion failed: {proc.stderr[:300]}"
                if error is None:
                    previews = _render_pdf(pdf_path, preview_root, pages)
        else:
            error = f"no renderer for {ext}"
    except subprocess.TimeoutExpired:
        error = "render timed out"
    except Exception as exc:
        error = str(exc)[:400]

    return {
        "ok": error is None and len(previews) > 0,
        "path": str(src),
        "previews": previews,
        "lint": lint,
        "error": error,
    }


def _render_pdf(pdf: Path, out_dir: Path, pages: Optional[list[int]]) -> list[dict[str, Any]]:
    pdftoppm = _which("pdftoppm")
    if not pdftoppm:
        # Fallback: mark not available
        return []
    prefix = out_dir / "page"
    cmd = [pdftoppm, "-png", "-r", "120", str(pdf), str(prefix)]
    if pages:
        # pdftoppm -f -l
        cmd = [pdftoppm, "-png", "-r", "120", "-f", str(min(pages)), "-l", str(max(pages)), str(pdf), str(prefix)]
    subprocess.run(cmd, capture_output=True, timeout=60)
    results = []
    for i, png in enumerate(sorted(out_dir.glob("page*.png"))):
        page_num = i + 1
        if pages and page_num not in pages:
            continue
        results.append({
            "page": page_num,
            "png_path": str(png),
            "sha256": hashlib.sha256(png.read_bytes()).hexdigest()[:16],
            "size_bytes": png.stat().st_size,
        })
    return results


def _render_html(src: Path, out_dir: Path) -> list[dict[str, Any]]:
    """Screenshot HTML via Playwright if available; otherwise skip."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []
    out_png = out_dir / "page-1.png"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(src.as_uri(), wait_until="networkidle", timeout=15_000)
            page.screenshot(path=str(out_png), full_page=True)
            # Mobile
            page.set_viewport_size({"width": 390, "height": 844})
            mobile = out_dir / "page-1-mobile.png"
            page.screenshot(path=str(mobile), full_page=True)
            browser.close()
        results = [{
            "page": 1,
            "png_path": str(out_png),
            "sha256": hashlib.sha256(out_png.read_bytes()).hexdigest()[:16],
            "size_bytes": out_png.stat().st_size,
            "viewport": "desktop",
        }]
        if mobile.exists():
            results.append({
                "page": 1,
                "png_path": str(mobile),
                "sha256": hashlib.sha256(mobile.read_bytes()).hexdigest()[:16],
                "size_bytes": mobile.stat().st_size,
                "viewport": "mobile",
            })
        return results
    except Exception as exc:
        logger.warning("html_render_failed", error=str(exc))
        return []


def lint_deliverable(src: Path, ext: str) -> dict[str, Any]:
    """Deterministic lint signals for the model / verifier."""
    lint: dict[str, Any] = {
        "file_size_bytes": src.stat().st_size if src.exists() else 0,
        "extension": ext,
        "issues": [],
    }
    if lint["file_size_bytes"] == 0:
        lint["issues"].append("empty_file")
    if ext == ".pptx":
        lint.update(_lint_pptx(src))
    elif ext == ".docx":
        lint.update(_lint_docx(src))
    elif ext == ".xlsx":
        lint.update(_lint_xlsx(src))
    elif ext == ".pdf":
        lint.update(_lint_pdf(src))
    elif ext in (".html", ".htm"):
        lint.update(_lint_html(src))
    return lint


def _lint_pptx(src: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"slide_count": None, "issues": []}
    try:
        # Minimal zip-based slide count
        import zipfile
        with zipfile.ZipFile(src) as z:
            slides = [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
            info["slide_count"] = len(slides)
            if len(slides) == 0:
                info["issues"].append("no_slides")
            # Empty placeholders heuristic: look for empty <a:t></a:t>
            empty = 0
            for s in slides[:30]:
                data = z.read(s).decode("utf-8", errors="ignore")
                empty += data.count("<a:t></a:t>")
            info["empty_placeholder_tags"] = empty
            if empty > 5:
                info["issues"].append("many_empty_placeholders")
    except Exception as exc:
        info["issues"].append(f"pptx_lint_error:{exc}")
    return info


def _lint_docx(src: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"issues": []}
    try:
        import zipfile
        with zipfile.ZipFile(src) as z:
            if "word/document.xml" not in z.namelist():
                info["issues"].append("missing_document_xml")
            else:
                data = z.read("word/document.xml").decode("utf-8", errors="ignore")
                if len(data) < 200:
                    info["issues"].append("very_short_document")
                info["xml_chars"] = len(data)
    except Exception as exc:
        info["issues"].append(f"docx_lint_error:{exc}")
    return info


def _lint_xlsx(src: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"sheet_count": None, "issues": []}
    try:
        import zipfile
        with zipfile.ZipFile(src) as z:
            sheets = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
            info["sheet_count"] = len(sheets)
            if not sheets:
                info["issues"].append("no_sheets")
    except Exception as exc:
        info["issues"].append(f"xlsx_lint_error:{exc}")
    return info


def _lint_pdf(src: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"page_count": None, "issues": []}
    try:
        data = src.read_bytes()
        # crude page count
        count = data.count(b"/Type /Page") + data.count(b"/Type/Page")
        info["page_count"] = count if count else None
        if count == 0:
            info["issues"].append("no_pages_detected")
    except Exception as exc:
        info["issues"].append(f"pdf_lint_error:{exc}")
    return info


def _lint_html(src: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"issues": []}
    try:
        text = src.read_text(errors="ignore")
        if "<html" not in text.lower():
            info["issues"].append("missing_html_tag")
        # broken link heuristic
        import re
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', text, re.I)
        broken = [h for h in hrefs if h.startswith("http://example.invalid") or h == "#"]
        info["href_count"] = len(hrefs)
        info["suspicious_hrefs"] = len(broken)
        if len(text) < 100:
            info["issues"].append("very_short_html")
    except Exception as exc:
        info["issues"].append(f"html_lint_error:{exc}")
    return info
