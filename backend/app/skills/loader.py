"""Skill discovery and progressive disclosure."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("onsite48.skills")

BUILTIN_ROOT = Path(__file__).resolve().parent
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    body = text[m.end() :]
    return meta, body


def _skill_dirs(org_id: str | None = None, user_id: str | None = None) -> list[tuple[str, Path]]:
    """Ordered candidates: user, org, builtin."""
    dirs: list[tuple[str, Path]] = []
    user_root = os.environ.get("ONSITE48_USER_SKILLS")
    org_root = os.environ.get("ONSITE48_ORG_SKILLS")
    if user_id and user_root:
        p = Path(user_root) / str(user_id)
        if p.is_dir():
            dirs.append(("user", p))
    if org_id and org_root:
        p = Path(org_root) / str(org_id)
        if p.is_dir():
            dirs.append(("org", p))
    dirs.append(("builtin", BUILTIN_ROOT))
    return dirs


def list_skill_summaries(
    org_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, str]]:
    """Return name + description for system prompt (progressive disclosure)."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for source, root in _skill_dirs(org_id, user_id):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("_") or child.name.startswith("."):
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            name = child.name
            if name in seen:
                continue
            try:
                text = skill_md.read_text(encoding="utf-8")
                meta, _ = _parse_frontmatter(text)
                desc = meta.get("description") or (text.strip().splitlines()[0][:160] if text.strip() else name)
                out.append({
                    "name": meta.get("name") or name,
                    "description": desc,
                    "source": source,
                })
                seen.add(name)
            except Exception as e:
                logger.warning("skills.list_failed", name=name, error=str(e))
    return out


def load_skill_body(
    name: str,
    org_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Return full skill body + file listing for load_skill tool."""
    # Sanitize name
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", name or ""):
        return {"error_code": "invalid_name", "message": "skill name must be alnum/underscore/hyphen", "retryable": False}
    for source, root in _skill_dirs(org_id, user_id):
        skill_dir = root / name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            files = []
            for p in sorted(skill_dir.rglob("*")):
                if p.is_file() and p.name != "SKILL.md":
                    rel = str(p.relative_to(skill_dir))
                    files.append(rel)
            return {
                "name": meta.get("name") or name,
                "description": meta.get("description", ""),
                "source": source,
                "body": body.strip(),
                "files": files,
                "dir": str(skill_dir),
            }
        except Exception as e:
            return {"error_code": "load_failed", "message": str(e), "retryable": False}
    return {"error_code": "not_found", "message": f"skill '{name}' not found", "retryable": False}
