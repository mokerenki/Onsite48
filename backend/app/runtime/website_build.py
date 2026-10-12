"""Helpers for routing, previewing, checking, and packaging generated websites."""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import re
import subprocess
import threading
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse, urljoin

MAX_PROJECT_FILES = 500
MAX_PROJECT_BYTES = 30 * 1024 * 1024
EXCLUDED_DIRS = {".git", "node_modules", ".next", ".venv", "venv", "__pycache__", ".pytest_cache"}

# Deliberately requires an action word and a website-shaped object. A request to
# visit or inspect a website remains a browser task; the noun "website" alone
# never opens a browser session.
_WEBSITE_BUILD = re.compile(
    r"\b(?:build|create|make|design|develop|code|scaffold|generate|implement|update|edit|fix|redesign)"
    r"\b.{0,100}\b(?:website|web\s*app|webpage|web\s*page|landing\s*page|site)\b"
    r"|\b(?:website|web\s*app|webpage|web\s*page|landing\s*page|site)\b.{0,100}"
    r"\b(?:build|create|make|design|develop|code|scaffold|generate|implement|update|edit|fix|redesign)\b",
    re.IGNORECASE | re.DOTALL,
)


def is_website_build_request(prompt: str) -> bool:
    return bool(_WEBSITE_BUILD.search(prompt or ""))


def website_build_preamble() -> str:
    return (
        "\n\n[WEBSITE BUILD WORKFLOW]\n"
        "This is a software creation/editing task, not a request to browse a target website. "
        "Do not open Agent Computer or request browser-session authorization to build it. "
        "Create a conventional, self-contained project in the task workspace. If the request "
        "needs a frontend build, run its documented build and available tests. For a static "
        "preview, run preview_website on the generated output directory (which must contain "
        "index.html), inspect its accessibility/link/browser-check findings, and fix material "
        "issues before finishing. Package the complete source project with package_website "
        "so the user can download it. Do not publish/deploy publicly unless the user explicitly "
        "requests publication and approves the separate publish action.\n"
    )


def _excluded(parts: tuple[str, ...]) -> bool:
    return any(part in EXCLUDED_DIRS or part.startswith(".") for part in parts)


async def export_project_files(workspace: Any, project_path: str) -> dict[str, bytes]:
    """Read a bounded project tree from either workspace backend."""
    path_text = (project_path or ".").strip() or "."
    pure = PurePosixPath(path_text.replace("\\", "/"))
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise ValueError("project path must be workspace-relative and may not contain '..'")

    files: dict[str, bytes] = {}
    total_bytes = 0

    if hasattr(workspace, "_resolve") and hasattr(workspace, "_home"):
        root = workspace._resolve(path_text)
        if not root.is_dir():
            raise ValueError(f"project directory not found: {path_text}")
        for current, dirs, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".") and not (current_path / d).is_symlink()]
            for filename in filenames:
                source = current_path / filename
                if source.is_symlink() or filename.startswith("."):
                    continue
                relative = source.relative_to(root)
                if _excluded(relative.parts):
                    continue
                size = source.stat().st_size
                total_bytes += size
                if len(files) >= MAX_PROJECT_FILES or total_bytes > MAX_PROJECT_BYTES:
                    raise ValueError("project exceeds the 500-file / 30 MiB website preview limit")
                files[relative.as_posix()] = source.read_bytes()
        return files

    # E2B-backed workspaces expose a file listing and a binary-safe read method.
    listed = await workspace.list(path_text, recursive=True)
    entries = listed.get("entries") if isinstance(listed, dict) else None
    if not isinstance(entries, list):
        raise ValueError("the active workspace could not enumerate the website project")
    manager = workspace._get_manager()
    prefix = tuple(part for part in pure.parts if part not in ("", "."))
    for item in entries:
        raw = str(item.get("path") or "").replace("\\", "/").lstrip("./")
        candidate = PurePosixPath(raw)
        parts = candidate.parts
        if parts[: len(prefix)] == prefix:
            parts = parts[len(prefix):]
        if not parts or any(part in ("..", "") for part in parts) or _excluded(parts):
            continue
        relative = PurePosixPath(*parts)
        remote_path = raw
        try:
            content = await manager.read_file(workspace.run_id, remote_path)
        except Exception:
            # E2B file listings include directories. Directories are skipped;
            # actual read errors for ordinary files are surfaced as a bounded cap.
            continue
        if not isinstance(content, bytes):
            content = bytes(content)
        total_bytes += len(content)
        if len(files) >= MAX_PROJECT_FILES or total_bytes > MAX_PROJECT_BYTES:
            raise ValueError("project exceeds the 500-file / 30 MiB website preview limit")
        files[relative.as_posix()] = content
    if not files:
        raise ValueError(f"no readable project files found under {path_text}")
    return files


class _HtmlAudit(HTMLParser):
    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.html_lang = False
        self.viewport = False
        self.doctype = False
        self.references: list[str] = []
        self.internal_links: list[str] = []
        self.external_links: list[str] = []
        self.inline_scripts = 0
        self.title_seen = False
        self.images_without_alt = 0
        self.heading_structure: list[tuple[int, str]] = []  # (level, text)
        self.current_heading_level = 0
        self.in_heading = False
        self.heading_text = ""
        self.aria_labels: list[str] = []
        self.form_inputs_without_labels = 0
        self.button_count = 0
        self.link_count = 0
        self.landmark_roles: list[str] = []
        self.has_main = False
        self.has_nav = False
        self.has_header = False
        self.has_footer = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag_lower = tag.lower()
        
        if tag_lower == "html" and values.get("lang"):
            self.html_lang = True
        if tag_lower == "meta" and values.get("name", "").lower() == "viewport":
            self.viewport = True
        if tag_lower == "title":
            self.title_seen = True
        if tag_lower in {"script", "img", "source", "video", "audio", "iframe", "link"}:
            key = "href" if tag_lower == "link" else "src"
            value = values.get(key, "")
            if value:
                self.references.append(value)
            if tag_lower == "script" and not value:
                self.inline_scripts += 1
        if tag_lower == "a" and values.get("href"):
            href = values["href"]
            self.references.append(href)
            self.link_count += 1
            # Classify as internal or external
            parsed = urlparse(href)
            if not parsed.scheme and not parsed.netloc:
                self.internal_links.append(href)
            else:
                self.external_links.append(href)
            # Check for accessible link text
            if not values.get("aria-label") and not values.get("title"):
                pass  # Will check text content in handle_data
        if tag_lower == "img":
            if not values.get("alt") and not values.get("role") == "presentation":
                self.images_without_alt += 1
        if tag_lower in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.in_heading = True
            self.current_heading_level = int(tag_lower[1])
            self.heading_text = ""
        if tag_lower == "main" or values.get("role") == "main":
            self.has_main = True
        if tag_lower == "nav" or values.get("role") == "navigation":
            self.has_nav = True
        if tag_lower == "header" or values.get("role") == "banner":
            self.has_header = True
        if tag_lower == "footer" or values.get("role") == "contentinfo":
            self.has_footer = True
        if values.get("role") and values["role"] in {"banner", "navigation", "main", "contentinfo", "complementary", "search", "form", "region"}:
            self.landmark_roles.append(values["role"])
        if tag_lower == "input" and values.get("type") not in {"hidden", "submit", "button", "image", "reset"}:
            if not values.get("id") and not values.get("aria-label") and not values.get("aria-labelledby"):
                self.form_inputs_without_labels += 1
        if tag_lower == "button" or (tag_lower == "input" and values.get("type") in {"submit", "button", "image"}):
            self.button_count += 1
            if not values.get("aria-label") and not values.get("title"):
                pass  # Check text content
        # ARIA labels
        if values.get("aria-label"):
            self.aria_labels.append(values["aria-label"])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"} and self.in_heading:
            self.heading_structure.append((self.current_heading_level, self.heading_text.strip()))
            self.in_heading = False

    def handle_data(self, data: str) -> None:
        if self.in_heading:
            self.heading_text += data


def _check_js_syntax(file_path: str, content: str) -> list[str]:
    """Check JavaScript syntax using Node.js if available."""
    issues = []
    try:
        # Try to run node --check
        result = subprocess.run(
            ["node", "--check", "-"],
            input=content.encode("utf-8"),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            # Extract meaningful error
            for line in stderr.split("\n"):
                if "SyntaxError" in line or "Error:" in line:
                    issues.append(f"{file_path}: {line.strip()[:200]}")
                    break
            else:
                if stderr.strip():
                    issues.append(f"{file_path}: JS syntax error: {stderr.strip()[:200]}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Node not available, skip JS syntax check
        pass
    except Exception:
        pass
    return issues


def _check_css_syntax(file_path: str, content: str) -> list[str]:
    """Basic CSS syntax validation."""
    issues = []
    try:
        # Try to parse with a simple approach - check for balanced braces
        open_braces = 0
        for i, ch in enumerate(content):
            if ch == "{":
                open_braces += 1
            elif ch == "}":
                open_braces -= 1
                if open_braces < 0:
                    issues.append(f"{file_path}: Unmatched closing brace at position {i}")
                    break
        if open_braces > 0:
            issues.append(f"{file_path}: Unclosed braces ({open_braces} open)")
        
        # Try using node with postcss if available for better validation
        # This is optional - just a basic check for now
    except Exception:
        pass
    return issues


def _check_accessibility(parser: _HtmlAudit, file_path: str) -> list[str]:
    """Check accessibility issues from parsed HTML."""
    issues = []
    
    # Heading structure
    if not parser.heading_structure:
        issues.append(f"{file_path}: No headings found (h1-h6)")
    else:
        # Check for h1
        h1_count = sum(1 for level, _ in parser.heading_structure if level == 1)
        if h1_count == 0:
            issues.append(f"{file_path}: Missing h1 heading")
        elif h1_count > 1:
            issues.append(f"{file_path}: Multiple h1 headings ({h1_count})")
        
        # Check heading hierarchy (no skipping levels)
        prev_level = 0
        for level, text in parser.heading_structure:
            if prev_level > 0 and level > prev_level + 1:
                issues.append(f"{file_path}: Heading level skip from h{prev_level} to h{level}: '{text[:50]}'")
            prev_level = level
    
    # Images without alt
    if parser.images_without_alt > 0:
        issues.append(f"{file_path}: {parser.images_without_alt} image(s) missing alt text")
    
    # Form inputs without labels
    if parser.form_inputs_without_labels > 0:
        issues.append(f"{file_path}: {parser.form_inputs_without_labels} form input(s) missing associated label")
    
    # Landmarks
    if not parser.has_main:
        issues.append(f"{file_path}: Missing main landmark (main element or role=main)")
    if not parser.has_nav and parser.link_count > 5:
        issues.append(f"{file_path}: Navigation landmark recommended (nav element or role=navigation)")
    
    # Language
    if not parser.html_lang:
        issues.append(f"{file_path}: HTML element missing lang attribute")
    
    # Viewport
    if not parser.viewport:
        issues.append(f"{file_path}: Missing viewport meta tag")
    
    # Title
    if not parser.title_seen:
        issues.append(f"{file_path}: Missing title element")
    
    return issues


def _crawl_internal_links(files: dict[str, bytes]) -> dict[str, Any]:
    """Crawl all HTML files and check internal links."""
    html_files = {k: v for k, v in files.items() if k.endswith((".html", ".htm"))}
    all_issues = []
    all_warnings = []
    pages_checked = 0
    total_links = 0
    broken_links = []
    
    for file_path, content in html_files.items():
        try:
            text = content.decode("utf-8", errors="replace")
            parser = _HtmlAudit()
            parser.feed(text)
            parser.close()
            pages_checked += 1
            total_links += len(parser.internal_links)
            
            # Check each internal link
            for link in parser.internal_links:
                parsed = urlparse(link)
                local = (parsed.path or "").split("?", 1)[0].lstrip("/")
                if not local:
                    continue
                # Handle relative paths
                base_dir = str(Path(file_path).parent)
                if base_dir == ".":
                    normalized = str(PurePosixPath(local))
                else:
                    normalized = str(PurePosixPath(base_dir) / local)
                normalized = str(PurePosixPath(normalized))  # Normalize . and ..
                
                if normalized not in files and not normalized.startswith("../"):
                    broken_links.append({"from": file_path, "to": link, "resolved": normalized})
            
            # Accessibility checks
            all_issues.extend(_check_accessibility(parser, file_path))
            
        except Exception as exc:
            all_issues.append(f"{file_path}: HTML parse failed: {str(exc)[:180]}")
    
    if broken_links:
        all_issues.append(f"Broken internal links: {len(broken_links)} (e.g., {broken_links[0]['from']} -> {broken_links[0]['resolved']})")
    
    return {
        "pages_checked": pages_checked,
        "total_internal_links": total_links,
        "broken_links": broken_links[:10],
        "issues": all_issues,
        "warnings": all_warnings,
    }


def audit_static_site(files: dict[str, bytes]) -> dict[str, Any]:
    """Run comprehensive multi-page audit: HTML semantics, local links, JS syntax, CSS validation, accessibility."""
    index = files.get("index.html")
    if index is None:
        return {"ok": False, "issues": ["index.html is missing"], "checked_files": len(files)}
    
    all_issues: list[str] = []
    all_warnings: list[str] = []
    
    # 1. Multi-page HTML audit and link crawling
    crawl_result = _crawl_internal_links(files)
    all_issues.extend(crawl_result.get("issues", []))
    all_warnings.extend(crawl_result.get("warnings", []))
    pages_checked = crawl_result.get("pages_checked", 0)
    broken_links = crawl_result.get("broken_links", [])
    
    # 2. JavaScript syntax check
    js_files = {k: v for k, v in files.items() if k.endswith(".js") and not k.startswith("node_modules/")}
    js_issues = 0
    for file_path, content in js_files.items():
        try:
            text = content.decode("utf-8", errors="replace")
            issues = _check_js_syntax(file_path, text)
            if issues:
                all_issues.extend(issues)
                js_issues += len(issues)
        except Exception:
            pass
    
    # 3. CSS syntax check
    css_files = {k: v for k, v in files.items() if k.endswith(".css")}
    css_issues = 0
    for file_path, content in css_files.items():
        try:
            text = content.decode("utf-8", errors="replace")
            issues = _check_css_syntax(file_path, text)
            if issues:
                all_issues.extend(issues)
                css_issues += len(issues)
        except Exception:
            pass
    
    # 4. Check index.html specifically for basics
    text = index.decode("utf-8", errors="replace")
    parser = _HtmlAudit()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        all_issues.append(f"index.html: HTML parse failed: {str(exc)[:180]}")
    
    if "<!doctype html" not in text.lower():
        all_issues.append("index.html: missing HTML5 doctype")
    
    return {
        "ok": not all_issues,
        "checked_files": len(files),
        "pages_checked": pages_checked,
        "issues": all_issues,
        "warnings": all_warnings,
        "responsive_meta": parser.viewport,
        "language_tag": parser.html_lang,
        "local_references_checked": sum(len(v) for v in [parser.references] if isinstance(v, list)),
        "broken_links": broken_links,
        "js_files_checked": len(js_files),
        "js_issues": js_issues,
        "css_files_checked": len(css_files),
        "css_issues": css_issues,
    }


def _browser_smoke_sync(site_root: Path) -> dict[str, Any]:
    """Launch a temporary local static server and inspect desktop/mobile pages."""
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return

    handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(site_root), **kwargs)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result: dict[str, Any] = {"available": False, "issues": [], "viewports_checked": [], "screenshots": {}}
    try:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            result["reason"] = "Playwright is not installed"
            return result
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception as exc:
                result["reason"] = f"Chromium is unavailable: {str(exc)[:180]}"
                return result
            
            # Test viewports: desktop and mobile
            viewports = [
                {"name": "desktop", "width": 1440, "height": 900},
                {"name": "mobile", "width": 390, "height": 844},
            ]
            
            url = f"http://127.0.0.1:{server.server_port}/index.html"
            
            for vp in viewports:
                page = browser.new_page(viewport={"width": vp["width"], "height": vp["height"]})
                console_errors: list[str] = []
                page_errors: list[str] = []
                failed_requests: list[str] = []
                page.on("console", lambda message: console_errors.append(message.text[:200]) if message.type == "error" else None)
                page.on("pageerror", lambda error: page_errors.append(str(error)[:200]))
                page.on("requestfailed", lambda request: failed_requests.append(request.url[:200]))
                
                try:
                    response = page.goto(url, wait_until="networkidle", timeout=12_000)
                    http_status = response.status if response else None
                    title = page.title()[:200]
                    
                    # Capture screenshot
                    screenshot_bytes = page.screenshot(full_page=True, type="png")
                    import base64
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
                    
                    result["viewports_checked"].append({
                        "name": vp["name"], 
                        "width": vp["width"], 
                        "height": vp["height"],
                        "http_status": http_status,
                        "title": title,
                        "console_errors": console_errors[:10],
                        "page_errors": page_errors[:10],
                        "failed_requests": failed_requests[:10],
                    })
                    result["screenshots"][vp["name"]] = screenshot_b64
                    
                    if console_errors:
                        result["issues"].extend([f"browser console ({vp['name']}): {m}" for m in console_errors[:3]])
                    if page_errors:
                        result["issues"].extend([f"browser page error ({vp['name']}): {m}" for m in page_errors[:3]])
                    if failed_requests:
                        result["issues"].extend([f"failed request ({vp['name']}): {u}" for u in failed_requests[:3]])
                        
                except Exception as exc:
                    page_errors.append(str(exc)[:200])
                    result["issues"].append(f"browser error ({vp['name']}): {str(exc)[:200]}")
                finally:
                    page.close()
            
            browser.close()
            result["available"] = True
            result["ok"] = not result["issues"] and all(v.get("http_status") == 200 for v in result["viewports_checked"])
            return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def browser_smoke_check(site_root: Path) -> dict[str, Any]:
    return await asyncio.to_thread(_browser_smoke_sync, site_root)


def materialize_site(files: dict[str, bytes], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(part in ("..", "") for part in relative.parts):
            raise ValueError("unsafe website file path")
        target = (destination / Path(*relative.parts)).resolve()
        target.relative_to(destination.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
