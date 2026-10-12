"""HTTP delivery for signed, short-lived website preview snapshots."""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from app.services.website_preview import (
    cleanup_expired_previews,
    preview_content_type,
    preview_security_headers,
    resolve_preview_file,
)

router = APIRouter(prefix="/api/web-previews", tags=["website-previews"])


@router.get("/{preview_id}/{expires}/{signature}/{file_path:path}")
async def get_preview_file(
    preview_id: str,
    expires: int,
    signature: str,
    file_path: str = "index.html",
) -> Response:
    cleanup_expired_previews()
    resolved = resolve_preview_file(preview_id, expires, signature, file_path)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail="This website preview is unavailable or has expired.",
        )

    _record, target = resolved
    headers = preview_security_headers()
    prefix = f"/api/web-previews/{preview_id}/{expires}/{signature}/"

    if target.suffix.lower() in {".html", ".htm"}:
        text = target.read_text(encoding="utf-8", errors="replace")

        def rewrite_root_reference(match: re.Match[str]) -> str:
            raw = match.group("url")
            path, separator, query = raw.partition("?")
            rewritten = prefix + quote(
                path.lstrip("/"), safe="/:@-._~!$&'()*+,;=%"
            )
            return (
                f"{match.group('attr')}{match.group('quote')}"
                f"{rewritten}{separator}{query}{match.group('quote')}"
            )

        text = re.sub(
            r"(?P<attr>\b(?:src|href|poster|action)\s*=\s*)"
            r"(?P<quote>[\"'])(?P<url>/(?!/)[^\"']*)(?P=quote)",
            rewrite_root_reference,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"<base\b[^>]*>", "", text, flags=re.IGNORECASE)
        if re.search(r"<head(?:\s[^>]*)?>", text, flags=re.IGNORECASE):
            text = re.sub(
                r"<head(?:\s[^>]*)?>",
                lambda match: f'{match.group(0)}<base href="{prefix}">',
                text,
                count=1,
                flags=re.IGNORECASE,
            )
        headers["Content-Security-Policy"] = headers[
            "Content-Security-Policy"
        ].replace("base-uri 'none'", "base-uri 'self'")
        return Response(text, media_type="text/html", headers=headers)

    if target.suffix.lower() == ".css":
        text = target.read_text(encoding="utf-8", errors="replace")

        def rewrite_css_reference(match: re.Match[str]) -> str:
            raw = match.group("url")
            rewritten = prefix + quote(
                raw.lstrip("/"), safe="/:@-._~!$&'()*+,;=%"
            )
            return f"url({match.group('quote')}{rewritten}{match.group('quote')})"

        text = re.sub(
            r"url\(\s*(?P<quote>[\"']?)"
            r"(?P<url>/(?!/)[^)\"'\s]+)(?P=quote)\s*\)",
            rewrite_css_reference,
            text,
            flags=re.IGNORECASE,
        )
        return Response(text, media_type="text/css", headers=headers)

    return FileResponse(
        target,
        media_type=preview_content_type(target),
        headers=headers,
    )
