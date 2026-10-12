"""Tests for render_preview + lint (no LibreOffice required for lint path)."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.runtime.deliverable import lint_deliverable, render_preview, SUPPORTED_EXT


def _minimal_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello deliverable</w:t></w:r></w:p></w:body></w:document>')


def _minimal_pptx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
        z.writestr("ppt/slides/slide1.xml", '<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t></a:t><a:t></a:t></p:sld>')
        z.writestr("ppt/slides/slide2.xml", '<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><a:t>Title</a:t></p:sld>')


def test_lint_docx(tmp_path: Path):
    p = tmp_path / "report.docx"
    _minimal_docx(p)
    lint = lint_deliverable(p, ".docx")
    assert lint["file_size_bytes"] > 0
    assert "missing_document_xml" not in lint.get("issues", [])


def test_lint_pptx_slide_count(tmp_path: Path):
    p = tmp_path / "deck.pptx"
    _minimal_pptx(p)
    lint = lint_deliverable(p, ".pptx")
    assert lint.get("slide_count") == 2
    assert lint.get("empty_placeholder_tags", 0) >= 2


def test_render_preview_missing_file(tmp_path: Path):
    result = render_preview("nope.docx", str(tmp_path))
    assert result["ok"] is False
    assert "not found" in (result.get("error") or "")


def test_render_preview_unsupported(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    result = render_preview(str(p), str(tmp_path))
    assert result["ok"] is False
    assert "unsupported" in (result.get("error") or "")


def test_render_preview_docx_without_libreoffice(tmp_path: Path):
    p = tmp_path / "report.docx"
    _minimal_docx(p)
    result = render_preview(str(p), str(tmp_path))
    # May fail conversion if soffice missing — but lint must be present
    assert "lint" in result
    assert result["lint"]["file_size_bytes"] > 0
    # If LibreOffice missing, error is set and ok is False — that is expected in CI
    if result.get("error"):
        assert "LibreOffice" in result["error"] or "soffice" in result["error"] or "not installed" in result["error"]


def test_html_lint(tmp_path: Path):
    p = tmp_path / "site.html"
    p.write_text("<html><body><a href='#'>x</a><a href='http://example.invalid/x'>bad</a></body></html>")
    lint = lint_deliverable(p, ".html")
    assert lint.get("href_count") == 2
    assert lint.get("suspicious_hrefs", 0) >= 1
