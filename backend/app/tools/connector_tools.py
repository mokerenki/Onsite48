"""First production connector tools for authenticated Onsite48 tasks.

Read tools are available only when the signed-in user has connected the relevant
provider. Write tools are marked irreversible and must pass the graph's approval
gate before any provider API request is issued.
"""

from __future__ import annotations

import base64
import asyncio
import os
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Mapping, Optional

import httpx
import structlog
from sqlalchemy import select

from app.db import get_session_factory
from app.models import ConnectorConnection, ConnectorStatus
from app.services.connector_vault import decrypt_credentials
from app.services.execution_identity import get_execution_identity
from app.services.provider_errors import (
    ProviderError,
    ProviderErrorCode,
    provider_error_from_status,
    provider_json,
    request_json,
)
from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("onsite48.tools.connectors")


class ConnectorNotConnectedError(RuntimeError):
    """Raised when a required connector is not connected."""
    pass


#: Every connector HTTP failure -- timeouts, DNS, 401/403/404/409/422/429/5xx,
#: malformed JSON -- is one structured contract raised as this class. It is an
#: alias rather than a subclass so there is exactly one error type in the system;
#: the old name is kept because the executor, the tests and the planner's
#: recovery prompt all reference it.
ConnectorProviderError = ProviderError


REFRESH_ENDPOINTS = {
    "github": ("https://github.com/login/oauth/access_token", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"),
    "gmail": ("https://oauth2.googleapis.com/token", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "google_calendar": ("https://oauth2.googleapis.com/token", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "outlook": ("https://login.microsoftonline.com/common/oauth2/v2.0/token", "OUTLOOK_CLIENT_ID", "OUTLOOK_CLIENT_SECRET"),
    "salesforce": ("https://login.salesforce.com/services/oauth2/token", "SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET"),
    "atlassian": ("https://auth.atlassian.com/oauth/token", "ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET"),
}


def connector_id_for_tool(tool_name: str) -> str | None:
    """Resolve a first-party connector id from one of its registered tools.

    This is intentionally a prefix allowlist rather than a fuzzy name match.
    Browser tools may navigate to a sign-in page, but that does not make an
    isolated ``computer_*`` action an OAuth connector eligible for refresh.
    """
    normalized = str(tool_name or "").lower()
    for connector_id in (
        "google_calendar",
        "github",
        "gmail",
        "slack",
        "outlook",
        "salesforce",
        "atlassian",
        "brand24",
        "elevenlabs",
        "similarweb",
    ):
        if normalized.startswith(f"{connector_id}_"):
            return connector_id
    return None


async def refresh_if_needed(connection: ConnectorConnection, credentials: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not force and (not connection.expires_at or connection.expires_at > now + timedelta(minutes=1)):
        return credentials

    refresh_token = credentials.get("refresh_token")
    refresh_config = REFRESH_ENDPOINTS.get(connection.connector_id)
    if not refresh_token or not refresh_config:
        connection.status = ConnectorStatus.EXPIRED
        raise ConnectorProviderError(
            connection.connector_id, "AUTH_EXPIRED",
            f"The {connection.connector_id} connection expired. Reconnect it in Settings.",
            status_code=401, reconnect_required=True,
        )

    token_url, client_id_name, client_secret_name = refresh_config
    client_id = os.getenv(client_id_name)
    client_secret = os.getenv(client_secret_name)
    if not client_id or not client_secret:
        raise ConnectorProviderError(
            connection.connector_id, ProviderErrorCode.CONFIGURATION_ERROR,
            f"{connection.connector_id} OAuth is not configured on the server.",
            retryable=False,
        )

    # The refresh call is the one place a connector must never surface raw
    # provider text: the failure body of a token endpoint is the closest thing a
    # provider has to an echo of the credentials that were just rejected. It
    # goes through the shared classification, which redacts and bounds it.
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Accept": "application/json"},
            )
        except Exception as exc:
            from app.services.provider_errors import provider_error_from_exception

            connection.status = ConnectorStatus.EXPIRED
            raise provider_error_from_exception(connection.connector_id, exc) from None

    if response.status_code >= 400:
        connection.status = ConnectorStatus.EXPIRED
        error = provider_error_from_status(connection.connector_id, response)
        raise ConnectorProviderError(
            connection.connector_id,
            ProviderErrorCode.AUTH_REFRESH_FAILED,
            f"Could not refresh {connection.connector_id} authorization. Reconnect it in Settings.",
            status_code=error.status_code,
            reconnect_required=True,
        )

    refreshed = provider_json(response, connection.connector_id)
    if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
        connection.status = ConnectorStatus.EXPIRED
        raise ConnectorProviderError(
            connection.connector_id,
            ProviderErrorCode.AUTH_REFRESH_FAILED,
            f"{connection.connector_id} returned an incomplete authorization response. Reconnect it in Settings.",
            reconnect_required=True,
        )
    credentials = {**credentials, **refreshed, "refresh_token": refreshed.get("refresh_token", refresh_token)}
    from app.services.connector_vault import encrypt_credentials
    connection.encrypted_credentials = encrypt_credentials(credentials)
    connection.expires_at = now + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
    connection.status = ConnectorStatus.CONNECTED
    return credentials


async def _credentials_and_connection_for(connector_id: str, *, force_refresh: bool = False):
    identity = get_execution_identity()
    async with get_session_factory()() as session:
        connection = await session.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.organization_id == identity.organization_id,
                ConnectorConnection.user_id == identity.user_id,
                ConnectorConnection.connector_id == connector_id,
                ConnectorConnection.status == ConnectorStatus.CONNECTED,
            )
        )
        if connection is None:
            raise ConnectorNotConnectedError(f"Connect {connector_id} in Settings before using this tool.")
        credentials = decrypt_credentials(connection.encrypted_credentials)
        if not credentials or not credentials.get("access_token"):
            raise ConnectorProviderError(
                connector_id, "AUTH_MISSING",
                f"The {connector_id} connection needs to be reconnected.",
                status_code=401, reconnect_required=True,
            )
        credentials = await refresh_if_needed(connection, credentials, force=force_refresh)
        await session.commit()
        return credentials


async def credentials_for(connector_id: str) -> dict[str, Any]:
    credentials = await _credentials_and_connection_for(connector_id)
    return credentials


async def refresh_connector_credentials(connector_id: str) -> None:
    """Force a one-time credential refresh for the current execution identity.

    This recovery entrypoint reuses the same scoped database lookup, encryption,
    status updates, and provider-error contract as ordinary connector requests
    instead of maintaining a second global vault.
    """
    await _credentials_and_connection_for(connector_id, force_refresh=True)


def _rewrite_provider_url(url: str) -> str:
    """When ONSITE48_GOOGLE_API_BASE is set, rewrite Google API hostnames to the fake."""
    base = (os.getenv("ONSITE48_GOOGLE_API_BASE") or "").rstrip("/")
    if not base:
        return url
    for host in (
        "https://gmail.googleapis.com",
        "https://www.googleapis.com",
        "http://gmail.googleapis.com",
        "http://www.googleapis.com",
    ):
        if url.startswith(host):
            return base + url[len(host):]
    return url


async def provider_request(
    connector_id: str,
    method: str,
    url: str,
    *,
    context: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> httpx.Response:
    """Perform an authenticated provider request with one auth-refresh retry.

    When ``ONSITE48_GOOGLE_API_BASE`` is set, Google API origins are rewritten
    to that base (eval fakes). ``ONSITE48_FAKE_GOOGLE_TOKEN`` skips vault/DB.
    """
    url = _rewrite_provider_url(url)
    fake_token = os.getenv("ONSITE48_FAKE_GOOGLE_TOKEN", "").strip()
    if fake_token and os.getenv("ONSITE48_GOOGLE_API_BASE"):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {fake_token}"
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.request(method, url, headers=headers, **kwargs)
            except Exception as exc:
                from app.services.provider_errors import provider_error_from_exception
                raise provider_error_from_exception(connector_id, exc, context=context) from None
        if response.status_code >= 400:
            raise provider_error_from_status(connector_id, response, context=context)
        return response

    credentials = await credentials_for(connector_id)
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {credentials['access_token']}"

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code == 401:
                credentials = await _credentials_and_connection_for(connector_id, force_refresh=True)
                headers["Authorization"] = f"Bearer {credentials['access_token']}"
                response = await client.request(method, url, headers=headers, **kwargs)
        except Exception as exc:
            from app.services.provider_errors import provider_error_from_exception
            raise provider_error_from_exception(connector_id, exc, context=context) from None

    if response.status_code >= 400:
        raise provider_error_from_status(connector_id, response, context=context)
    return response


async def provider_json_request(
    connector_id: str,
    method: str,
    url: str,
    *,
    context: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> Any:
    """``provider_request`` plus strict JSON decoding of the success body."""
    response = await provider_request(connector_id, method, url, context=context, **kwargs)
    return provider_json(response, connector_id, context=context)


class GitHubListRepositoriesTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="github_list_repositories",
            description="List the signed-in user's GitHub repositories.",
            parameters={"limit": {"type": "integer", "description": "Maximum repositories to return, 1-100."}, "count": {"type": "boolean", "description": "Return the exact visible repository count instead of listing repositories."}},
        )

    async def execute(self, limit: int = 20, count: bool = False, **_: Any) -> dict[str, Any]:
        response = await provider_request(
            "github", "GET", "https://api.github.com/user/repos",
            params={"per_page": 1 if count else max(1, min(int(limit), 100)), "sort": "updated"},
            headers={"Accept": "application/vnd.github+json"},
        )
        if count:
            import re
            last_page = re.search(r"[?&]page=(\d+)>; rel=\"last\"", response.headers.get("Link", ""))
            exact_count = int(last_page.group(1)) if last_page else len(provider_json(response, "github"))
            return {"output": f"You have access to {exact_count} GitHub repositories.", "count": exact_count, "repositories": [], "exact": True}
        repos = [
            {
                "name": item["full_name"],
                "description": item.get("description"),
                "url": item["html_url"],
                "private": item["private"],
            }
            for item in provider_json(response, "github")
        ]
        if not repos:
            text = (
                "You have no repositories visible to this GitHub connection "
                "(or none matched the request)."
            )
        else:
            lines = [
                f"{i}. {r['name']}" + (" (private)" if r.get("private") else "")
                for i, r in enumerate(repos, 1)
            ]
            word = "repository" if len(repos) == 1 else "repositories"
            header = f"You have access to {len(repos)} GitHub {word} (showing up to the requested limit):"
            text = header + "\n" + "\n".join(lines)
        return {"output": text, "count": len(repos), "repositories": repos}


class GitHubCreateIssueTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="github_create_issue",
            description="Create a GitHub issue in a repository. Requires explicit user confirmation.",
            parameters={
                "repository": {"type": "string", "description": "Repository in owner/name form."},
                "title": {"type": "string", "description": "Issue title."},
                "body": {"type": "string", "description": "Issue body in Markdown."},
            },
            required=["repository", "title"],
            irreversible=True,
        )

    async def execute(self, repository: str, title: str, body: str = "", **_: Any) -> dict[str, Any]:
        response = await provider_request(
            "github", "POST", f"https://api.github.com/repos/{repository}/issues",
            context={"resource": "repository", "identifier": repository},
            json={"title": title, "body": body},
            headers={"Accept": "application/vnd.github+json"},
        )
        data = provider_json(response, "github")
        return {"issue_number": data["number"], "url": data["html_url"], "title": data["title"]}


class GitHubGetRepositoryTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(name="github_get_repository", description="Get metadata for an exact GitHub repository in owner/name form.", parameters={"repository": {"type": "string", "description": "Repository in owner/name form."}}, required=["repository"], resources=["github_repository"], operations=["get"], idempotency="read_only", exactness="exact")

    async def execute(self, repository: str, **_: Any) -> dict[str, Any]:
        response = await provider_request("github", "GET", f"https://api.github.com/repos/{repository}", context={"resource": "repository", "identifier": repository if "repository" == "repository" else (str(issue_number) if "repository" == "issue" else path)}, headers={"Accept": "application/vnd.github+json"})
        data = provider_json(response, "github")
        return {"name": data.get("full_name"), "description": data.get("description"), "url": data.get("html_url"), "private": data.get("private"), "default_branch": data.get("default_branch"), "open_issues": data.get("open_issues_count"), "stars": data.get("stargazers_count")}


class GitHubListIssuesTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(name="github_list_issues", description="List issues for an exact GitHub repository.", parameters={"repository": {"type": "string", "description": "Repository in owner/name form."}, "state": {"type": "string", "description": "Issue state: open, closed, or all.", "default": "open"}, "limit": {"type": "integer", "description": "Maximum issues, 1-100."}}, required=["repository"], resources=["github_issue"], operations=["list", "search"], idempotency="read_only", exactness="paginated")

    async def execute(self, repository: str, state: str = "open", limit: int = 30, **_: Any) -> dict[str, Any]:
        response = await provider_request("github", "GET", f"https://api.github.com/repos/{repository}/issues", context={"resource": "repository", "identifier": repository}, params={"state": state if state in {"open", "closed", "all"} else "open", "per_page": max(1, min(int(limit), 100))}, headers={"Accept": "application/vnd.github+json"})
        issues = [{"number": item.get("number"), "title": item.get("title"), "state": item.get("state"), "url": item.get("html_url"), "user": (item.get("user") or {}).get("login"), "pull_request": "pull_request" in item} for item in provider_json(response, "github")]
        return {"issues": issues, "count": len(issues)}


class GitHubUpdateIssueTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(name="github_update_issue", description="Update an existing GitHub issue title, body, labels, assignee, or state. Requires explicit user confirmation.", parameters={"repository": {"type": "string", "description": "Repository in owner/name form."}, "issue_number": {"type": "integer", "description": "Issue number."}, "title": {"type": "string"}, "body": {"type": "string"}, "state": {"type": "string", "description": "open or closed."}, "labels": {"type": "array", "items": {"type": "string"}}, "assignee": {"type": "string"}}, required=["repository", "issue_number"], irreversible=True, resources=["github_issue"], operations=["update"], idempotency="write", exactness="exact")

    async def execute(self, repository: str, issue_number: int, title: str | None = None, body: str | None = None, state: str | None = None, labels: list[str] | None = None, assignee: str | None = None, **_: Any) -> dict[str, Any]:
        payload = {key: value for key, value in {"title": title, "body": body, "state": state, "labels": labels, "assignee": assignee}.items() if value is not None}
        if not payload: raise ConnectorProviderError("github", "INVALID_ARGUMENT", "Provide at least one issue field to update.")
        response = await provider_request("github", "PATCH", f"https://api.github.com/repos/{repository}/issues/{int(issue_number)}", context={"resource": "issue", "identifier": str(issue_number)}, json=payload, headers={"Accept": "application/vnd.github+json"})
        data = provider_json(response, "github")
        return {"issue_number": data.get("number"), "title": data.get("title"), "state": data.get("state"), "url": data.get("html_url"), "status": "updated"}


class GitHubCommentIssueTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(name="github_comment_issue", description="Add a comment to a GitHub issue or pull request. Requires explicit user confirmation.", parameters={"repository": {"type": "string"}, "issue_number": {"type": "integer"}, "body": {"type": "string"}}, required=["repository", "issue_number", "body"], irreversible=True, resources=["github_issue"], operations=["comment"], idempotency="write", exactness="exact")

    async def execute(self, repository: str, issue_number: int, body: str, **_: Any) -> dict[str, Any]:
        response = await provider_request("github", "POST", f"https://api.github.com/repos/{repository}/issues/{int(issue_number)}/comments", context={"resource": "issue", "identifier": str(issue_number)}, json={"body": body}, headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json"})
        data = provider_json(response, "github")
        return {"comment_id": data.get("id"), "url": data.get("html_url"), "status": "commented"}


class GitHubGetFileTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(name="github_get_file", description="Read a file from an exact GitHub repository and branch or commit.", parameters={"repository": {"type": "string"}, "path": {"type": "string"}, "ref": {"type": "string", "default": ""}}, required=["repository", "path"], resources=["github_file"], operations=["get", "read"], idempotency="read_only", exactness="exact")

    async def execute(self, repository: str, path: str, ref: str = "", **_: Any) -> dict[str, Any]:
        params = {"ref": ref} if ref else None
        response = await provider_request("github", "GET", f"https://api.github.com/repos/{repository}/contents/{path}", context={"resource": "file", "identifier": path}, params=params, headers={"Accept": "application/vnd.github+json"})
        data = provider_json(response, "github")
        import base64 as _base64
        content = _base64.b64decode(data.get("content", "").encode()).decode("utf-8", errors="replace") if data.get("encoding") == "base64" else data.get("content", "")
        return {"path": data.get("path"), "sha": data.get("sha"), "content": content, "url": data.get("html_url")}


class GitHubUpdateFileTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(name="github_update_file", description="Create or update a file in a GitHub repository. Requires explicit user confirmation and the current file SHA when updating an existing file.", parameters={"repository": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}, "message": {"type": "string"}, "branch": {"type": "string", "default": ""}, "sha": {"type": "string", "description": "Current file SHA for an existing file."}}, required=["repository", "path", "content", "message"], irreversible=True, resources=["github_file"], operations=["create", "update"], idempotency="write", exactness="exact")

    async def execute(self, repository: str, path: str, content: str, message: str, branch: str = "", sha: str | None = None, **_: Any) -> dict[str, Any]:
        import base64 as _base64
        payload: dict[str, Any] = {"message": message, "content": _base64.b64encode(content.encode()).decode()}
        if branch: payload["branch"] = branch
        if sha: payload["sha"] = sha
        response = await provider_request("github", "PUT", f"https://api.github.com/repos/{repository}/contents/{path}", context={"resource": "file", "identifier": path}, json=payload, headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json"})
        data = provider_json(response, "github")
        return {"path": (data.get("content") or {}).get("path", path), "commit_sha": (data.get("commit") or {}).get("sha"), "url": (data.get("content") or {}).get("html_url"), "status": "updated" if sha else "created"}


class GmailSearchMessagesTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_search_messages",
            description=(
                "Search received Gmail messages and return one paginated page of message metadata. "
                "Do not use for drafts or exact mailbox counts; use dedicated draft/count tools."
            ),
            parameters={"query": {"type": "string", "description": "Gmail search query."}, "limit": {"type": "integer", "description": "Messages per page, 1-100."}, "page_token": {"type": "string", "description": "Gmail nextPageToken from the previous page."}, "exact": {"type": "boolean", "description": "Continue pagination until the requested number is collected or Gmail is exhausted."}},
            required=["query"],
            resources=["gmail_message"],
            operations=["search", "list", "get"],
            excludes_operations=["count"],
            exactness="paginated",
            supports_pagination=True,
        )

    async def execute(self, query: str, limit: int = 50, page_token: str | None = None, exact: bool = False, **_: Any) -> dict[str, Any]:
        requested = max(1, min(int(limit), 100))
        message_ids: list[str] = []
        next_page_token = page_token
        pages_scanned = 0
        # Hard ceiling raised to satisfy typical "give me 11 / 25 / 50 emails"
        # requests without an unbounded loop. Gmail returns up to 100 ids per
        # page, so 20 pages covers 2,000 messages.
        while len(message_ids) < requested and pages_scanned < 20:
            params: dict[str, Any] = {
                "q": query,
                "maxResults": requested - len(message_ids) if exact else requested,
            }
            if next_page_token:
                params["pageToken"] = next_page_token
            search = await provider_request(
                "gmail", "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params=params,
            )
            payload = provider_json(search, "gmail")
            message_ids.extend(item["id"] for item in payload.get("messages", []) if item.get("id"))
            next_page_token = payload.get("nextPageToken")
            pages_scanned += 1
            if not exact or not next_page_token or len(message_ids) >= requested:
                break
        message_ids = message_ids[:requested]

        async def load_message(message_id: str) -> dict[str, Any]:
            message = await provider_request(
                "gmail", "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
            )
            data = provider_json(message, "gmail")
            headers_by_name = {header["name"].lower(): header["value"] for header in data.get("payload", {}).get("headers", [])}
            return {
                "id": message_id,
                "snippet": data.get("snippet", ""),
                "from": headers_by_name.get("from"),
                "subject": headers_by_name.get("subject"),
                "date": headers_by_name.get("date"),
            }

        messages = await asyncio.gather(*(load_message(message_id) for message_id in message_ids))

        # Structured result list — this is what allows follow-ups like
        # "where are the other 5?" to resolve against the prior response.
        if not messages:
            text = f'No Gmail messages matched query "{query}".'
        else:
            lines = []
            for index, message in enumerate(messages, start=1):
                subject = message.get("subject") or "(no subject)"
                sender = message.get("from") or "(unknown sender)"
                when = message.get("date") or ""
                snippet = (message.get("snippet") or "").strip()
                line = f"{index}. {subject} — from {sender}"
                if when:
                    line += f" ({when})"
                if snippet:
                    line += f"\n   {snippet[:200]}"
                lines.append(line)
            text = f"Found {len(messages)} Gmail message(s) for \"{query}\":\n" + "\n".join(lines)
        if next_page_token and len(messages) >= requested:
            text += f"\nMore messages are available; continue with page_token={next_page_token}."
        return {
            "resource": "email_messages",
            "operation": "search",
            "output": text,
            "messages": messages,          # <-- structured list preserved end-to-end
            "requested_count": requested,
            "returned_count": len(messages),
            "next_page_token": next_page_token,
            "has_more": bool(next_page_token),
            "pages_scanned": pages_scanned,
            "exact_request": bool(exact),
        }


class GmailListDraftsTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_list_drafts",
            description=(
                "List drafts in the signed-in user's Gmail mailbox. Reads draft metadata only; "
                "never use this tool to send or create a draft."
            ),
            parameters={
                "limit": {"type": "integer", "description": "Drafts per page, 1-100."},
                "page_token": {"type": "string", "description": "Gmail nextPageToken from the previous page."},
            },
            required=[],
            irreversible=False,
            idempotency="read_only",
            approval_required=False,
            output_schema={
                "type": "object",
                "required": ["resource", "operation", "drafts", "returned_count", "has_more"],
            },
            resources=["gmail_draft"],
            operations=["list", "get"],
            exactness="paginated",
            supports_pagination=True,
        )

    async def execute(self, limit: int = 50, page_token: str | None = None, **_: Any) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 100))
        params: dict[str, Any] = {"maxResults": page_size}
        if page_token:
            params["pageToken"] = page_token
        response = await provider_request(
            "gmail",
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            params=params,
        )
        payload = provider_json(response, "gmail")
        drafts: list[dict[str, Any]] = []
        for item in payload.get("drafts", []) or []:
            draft_id = item.get("id")
            message_id = (item.get("message") or {}).get("id")
            if not draft_id or not message_id:
                continue
            message_response = await provider_request(
                "gmail",
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                params={"format": "metadata", "metadataHeaders": ["To", "Cc", "Subject", "Date"]},
            )
            data = provider_json(message_response, "gmail")
            headers = {header["name"].lower(): header["value"] for header in data.get("payload", {}).get("headers", [])}
            drafts.append({
                "draft_id": draft_id,
                "message_id": message_id,
                "to": headers.get("to"),
                "cc": headers.get("cc"),
                "subject": headers.get("subject"),
                "date": headers.get("date"),
                "snippet": data.get("snippet", ""),
            })
        next_page_token = payload.get("nextPageToken")
        return {
            "resource": "email_drafts",
            "operation": "list",
            "drafts": drafts,
            "requested_limit": page_size,
            "returned_count": len(drafts),
            "next_page_token": next_page_token,
            "has_more": bool(next_page_token),
        }


class GmailCountDraftsTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_count_drafts",
            description=(
                "Count all drafts in the signed-in user's Gmail mailbox. Returns an exact count, "
                "does not expose draft bodies, and must be used for 'how many drafts' questions."
            ),
            parameters={},
            required=[],
            irreversible=False,
            idempotency="read_only",
            approval_required=False,
            max_attempts=1,
            output_schema={
                "type": "object",
                "required": ["resource", "operation", "count", "exact"],
            },
            resources=["gmail_draft"],
            operations=["count"],
            exactness="exact",
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        page_token: str | None = None
        count = 0
        pages = 0
        while True:
            params: dict[str, Any] = {"maxResults": 100}
            if page_token:
                params["pageToken"] = page_token
            response = await provider_request(
                "gmail",
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                params=params,
            )
            payload = provider_json(response, "gmail")
            count += len(payload.get("drafts", []) or [])
            pages += 1
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
            if pages >= 1000:
                raise ConnectorProviderError("gmail", "COUNT_LIMIT", "Gmail draft count exceeded the safe pagination limit.")
        return {
            "resource": "email_drafts",
            "operation": "count",
            "count": count,
            "exact": True,
            "pages_scanned": pages,
        }


class GmailSendMessageTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_send_message",
            description="Send an email from the signed-in user's mailbox. Requires explicit user confirmation.",
            parameters={
                "draft_id": {"type": "string", "description": "Existing Gmail draft ID to send, when sending a previously created draft."},
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text email body."},
            },
            required=["to", "subject", "body"],
            irreversible=True,
            resources=["gmail_message"],
            operations=["send"],
            exactness="exact",
        )

    async def execute(self, to: str = "", subject: str = "", body: str = "", draft_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not to or not subject or not body:
            if not draft_id:
                raise ConnectorProviderError(
                    "gmail",
                    "MISSING_ARGUMENTS",
                    "Sending a new message needs a recipient, subject, and body. Provide them, or supply draft_id to send an existing draft.",
                )
        message = EmailMessage()
        if to:
            message["To"] = to
        if subject:
            message["Subject"] = subject
        message.set_content(body or "")
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        # A send is a non-idempotent write. It must never be retried on a
        # transport failure or a 5xx, because the provider may already have
        # accepted the message; `provider_request` only retries an explicit 401
        # after re-reading credentials, and a 401 means nothing was sent.
        payload: dict[str, Any] = {"raw": raw}
        if draft_id:
            endpoint = "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send"
            payload = {"id": draft_id}
            context: Optional[Mapping[str, Any]] = {"resource": "draft", "identifier": draft_id}
        else:
            endpoint = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
            context = {"resource": "message", "identifier": to}
        data = await provider_json_request(
            "gmail", "POST", endpoint,
            json=payload,
            context=context,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if not isinstance(data, dict):
            raise ConnectorProviderError(
                "gmail", "MALFORMED_RESPONSE",
                "Gmail returned an unexpected send response. Check the Sent folder before retrying.",
            )
        result = {"message_id": data.get("id"), "thread_id": data.get("threadId"), "status": "sent"}
        if draft_id:
            result["draft_id"] = draft_id
        return result


class GmailCreateDraftTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_create_draft",
            description="Create a reversible Gmail draft in the signed-in user's mailbox. This never sends the email and runs when the user explicitly requests a draft.",
            parameters={
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text email body."},
            },
            required=["to", "subject", "body"],
            irreversible=False,
            idempotency="non_idempotent_write",
            approval_required=False,
            max_attempts=1,
            resources=["gmail_draft"],
            operations=["create"],
            exactness="exact",
        )

    async def execute(self, to: str, subject: str, body: str, **_: Any) -> dict[str, Any]:
        if not isinstance(to, str) or "@" not in to or "\n" in to or "\r" in to:
            raise ConnectorProviderError("gmail", "INVALID_ARGUMENT", "A valid recipient email address is required.")
        if not isinstance(subject, str) or not subject.strip() or "\n" in subject or "\r" in subject:
            raise ConnectorProviderError("gmail", "INVALID_ARGUMENT", "A non-empty, single-line email subject is required.")
        if not isinstance(body, str) or not body.strip():
            raise ConnectorProviderError("gmail", "INVALID_ARGUMENT", "A non-empty email body is required.")
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        response = await provider_request(
            "gmail",
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            json={"message": {"raw": raw}},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        data = provider_json(response, "gmail")
        return {"draft_id": data.get("id"), "message_id": data.get("message", {}).get("id"), "status": "draft_created"}


class GmailDeleteDraftTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_delete_draft",
            description="Permanently delete one Gmail draft by its draft ID. This is irreversible.",
            parameters={"draft_id": {"type": "string", "description": "The Gmail draft ID to delete."}},
            required=["draft_id"],
            idempotency="non_idempotent_write",
            irreversible=True,
            resources=["gmail_draft"],
            operations=["delete"],
            output_schema={"type": "object", "required": ["deleted"]},
        )

    async def execute(self, draft_id: str, **_: Any) -> dict[str, Any]:
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise ConnectorProviderError("gmail", "INVALID_ARGUMENT", "A valid draft ID is required.")
        response = await provider_request(
            "gmail",
            "DELETE",
            f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{draft_id}",
            headers={"Accept": "application/json"},
        )
        return {"deleted": True, "draft_id": draft_id}


class GmailDeleteMessageTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_delete_message",
            description="Permanently delete a Gmail message by its ID. Requires explicit user confirmation. Use with caution as this cannot be undone.",
            parameters={
                "message_id": {"type": "string", "description": "The Gmail message ID to delete."},
            },
            required=["message_id"],
            irreversible=True,
            resources=["gmail_message"],
            operations=["delete"],
            exactness="exact",
        )

    async def execute(self, message_id: str, **_: Any) -> dict[str, Any]:
        if not isinstance(message_id, str) or not message_id.strip():
            raise ConnectorProviderError("gmail", "INVALID_ARGUMENT", "A valid message ID is required.")
        response = await provider_request(
            "gmail",
            "DELETE",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            headers={"Accept": "application/json"},
        )
        return {"message_id": message_id, "status": "deleted"}


class GmailTrashMessageTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="gmail_trash_message",
            description="Move one Gmail message to Trash (recoverable for 30 days) by message ID.",
            parameters={"message_id": {"type": "string"}},
            required=["message_id"],
            idempotency="non_idempotent_write",
            irreversible=True,
            operations=["delete"],
            resources=["gmail_message"],
            output_schema={"type": "object", "required": ["trashed"]},
        )

    async def execute(self, message_id: str, **_: Any) -> dict[str, Any]:
        if not isinstance(message_id, str) or not message_id.strip():
            raise ConnectorProviderError("gmail", "INVALID_ARGUMENT", "A valid message ID is required.")
        response = await provider_request(
            "gmail",
            "POST",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/trash",
            headers={"Accept": "application/json"},
        )
        return {"trashed": True, "message_id": message_id}


class GoogleCalendarListEventsTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="google_calendar_list_events",
            description="List upcoming events from the signed-in user's primary Google Calendar.",
            parameters={
                "time_min": {"type": "string", "description": "Lower bound for event end time in RFC3339 format."},
                "time_max": {"type": "string", "description": "Upper bound for event start time in RFC3339 format."},
                "max_results": {"type": "integer", "description": "Maximum events to return, 1-50."},
            },
            required=["time_min", "time_max"],
            resources=["calendar_event"],
            operations=["list", "get"],
            idempotency="read_only",
            exactness="paginated",
        )

    async def execute(self, time_min: str, time_max: str, max_results: int = 10, **_: Any) -> dict[str, Any]:
        params = {
            "calendarId": "primary",
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": max(1, min(int(max_results), 50)),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        response = await provider_request(
            "google_calendar", "GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params=params,
            headers={"Accept": "application/json"},
        )
        items = []
        for item in provider_json(response, "google_calendar").get("items", []):
            items.append({
                "id": item.get("id"),
                "summary": item.get("summary"),
                "start": (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date"),
                "end": (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date"),
                "location": item.get("location"),
                "html_link": item.get("htmlLink"),
            })
        if not items:
            text = f"No Google Calendar events found between {time_min} and {time_max}."
        else:
            lines = []
            for index, event in enumerate(items, start=1):
                title = event.get("summary") or "(no title)"
                start = event.get("start") or "?"
                end = event.get("end") or "?"
                location = event.get("location") or ""
                line = f"{index}. {title} · {start} → {end}"
                if location:
                    line += f" @ {location}"
                lines.append(line)
            text = f"Found {len(items)} Google Calendar event(s):\n" + "\n".join(lines)
        return {"output": text, "events": items}


class GoogleCalendarCreateEventTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="google_calendar_create_event",
            description="Create a Google Calendar event. Requires explicit user confirmation.",
            parameters={
                "summary": {"type": "string", "description": "Event title."},
                "start": {"type": "string", "description": "Start time in RFC3339 format, or YYYY-MM-DD for an all-day event."},
                "end": {"type": "string", "description": "End time in RFC3339 format, or the exclusive YYYY-MM-DD end date for an all-day event."},
                "all_day": {"type": "boolean", "description": "Create an all-day event when true.", "default": False},
                "location": {"type": "string", "description": "Event location.", "default": ""},
                "notes": {"type": "string", "description": "Event description/notes.", "default": ""},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee emails.", "default": []},
            },
            required=["summary", "start", "end"],
            irreversible=True,
            resources=["calendar_event"],
            operations=["create"],
            idempotency="write",
            exactness="exact",
        )

    async def execute(self, summary: str, start: str, end: str, all_day: bool = False, location: str = "", notes: str = "", attendees: list[str] | None = None, **_: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"date": start} if all_day else {"dateTime": start},
            "end": {"date": end} if all_day else {"dateTime": end},
        }
        if location:
            body["location"] = location
        if notes:
            body["description"] = notes
        if attendees:
            body["attendees"] = [{"email": email} for email in attendees]
        response = await provider_request(
            "google_calendar", "POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            json=body,
            params={"sendUpdates": "all" if attendees else "none"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        data = provider_json(response, "google_calendar")
        return {
            "id": data.get("id"),
            "summary": data.get("summary"),
            "start": (data.get("start") or {}).get("dateTime") or (data.get("start") or {}).get("date"),
            "end": (data.get("end") or {}).get("dateTime") or (data.get("end") or {}).get("date"),
            "html_link": data.get("htmlLink"),
            "status": "created",
        }


class GoogleCalendarUpdateEventTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="google_calendar_update_event",
            description="Rename or update an existing Google Calendar event. Requires explicit user confirmation and an exact event ID.",
            parameters={
                "event_id": {"type": "string", "description": "Exact Google Calendar event ID."},
                "summary": {"type": "string", "description": "New event title."},
                "start": {"type": "string", "description": "New start time in RFC3339 format."},
                "end": {"type": "string", "description": "New end time in RFC3339 format."},
                "location": {"type": "string", "description": "New location."},
                "notes": {"type": "string", "description": "New event notes."},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "Replacement attendee email list."},
            },
            required=["event_id"],
            irreversible=True,
            resources=["calendar_event"],
            operations=["update"],
            idempotency="write",
            exactness="exact",
        )

    async def execute(self, event_id: str, summary: str | None = None, start: str | None = None, end: str | None = None, location: str | None = None, notes: str | None = None, attendees: list[str] | None = None, **_: Any) -> dict[str, Any]:
        if not any(value is not None for value in (summary, start, end, location, notes, attendees)):
            raise ConnectorProviderError("google_calendar", "INVALID_ARGUMENT", "Provide at least one field to update, such as the new event name.")
        body: dict[str, Any] = {}
        if summary is not None: body["summary"] = summary
        if start is not None: body["start"] = {"dateTime": start}
        if end is not None: body["end"] = {"dateTime": end}
        if location is not None: body["location"] = location
        if notes is not None: body["description"] = notes
        if attendees is not None: body["attendees"] = [{"email": email} for email in attendees]
        response = await provider_request(
            "google_calendar", "PATCH", f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}", context={"resource": "event", "identifier": event_id},
            json=body, params={"sendUpdates": "all" if attendees is not None else "none"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        data = provider_json(response, "google_calendar")
        return {"id": data.get("id"), "summary": data.get("summary"), "start": (data.get("start") or {}).get("dateTime") or (data.get("start") or {}).get("date"), "end": (data.get("end") or {}).get("dateTime") or (data.get("end") or {}).get("date"), "html_link": data.get("htmlLink"), "status": "updated"}


class GoogleCalendarDeleteEventTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="google_calendar_delete_event",
            description="Delete an existing Google Calendar event. Requires explicit user confirmation and an exact event ID.",
            parameters={"event_id": {"type": "string", "description": "Exact Google Calendar event ID."}},
            required=["event_id"],
            irreversible=True,
            resources=["calendar_event"],
            operations=["delete"],
            idempotency="write",
            exactness="exact",
        )

    async def execute(self, event_id: str, **_: Any) -> dict[str, Any]:
        await provider_request(
            "google_calendar", "DELETE", f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}", context={"resource": "event", "identifier": event_id},
            params={"sendUpdates": "all"}, headers={"Accept": "application/json"},
        )
        return {"id": event_id, "status": "deleted"}


class SlackListChannelsTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="slack_list_channels",
            description="List public channels in the signed-in user's Slack workspace.",
            parameters={"limit": {"type": "integer", "description": "Maximum channels, 1-100."}},
        )

    async def execute(self, limit: int = 100, **_: Any) -> dict[str, Any]:
        credentials = await credentials_for("slack")
        data = await request_json(
            "slack", "GET", "https://slack.com/api/conversations.list",
            access_token=credentials["access_token"],
            params={"limit": max(1, min(int(limit), 100)), "exclude_archived": "true"},
        )
        if not isinstance(data, dict) or not data.get("ok"):
            # Slack answers 200 with `ok: false` for auth, permission and
            # not-found failures. Treating that as a plain RuntimeError hid the
            # status class the runtime needs to decide between "reconnect" and
            # "not found".
            raise _slack_api_error("slack_list_channels", data)
        return {"channels": [{"id": channel["id"], "name": channel["name"], "topic": (channel.get("topic") or {}).get("value", "")} for channel in (data.get("channels") or [])]}


class SlackSendMessageTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="slack_send_message",
            description="Send a Slack message. Requires explicit user confirmation.",
            parameters={"channel": {"type": "string", "description": "Slack channel ID."}, "text": {"type": "string", "description": "Message text."}},
            required=["channel", "text"],
            irreversible=True,
        )

    async def execute(self, channel: str, text: str, **_: Any) -> dict[str, Any]:
        credentials = await credentials_for("slack")
        data = await request_json(
            "slack", "POST", "https://slack.com/api/chat.postMessage",
            access_token=credentials["access_token"],
            json={"channel": channel, "text": text},
            context={"resource": "channel", "identifier": channel},
        )
        if not isinstance(data, dict) or not data.get("ok"):
            raise _slack_api_error("slack_send_message", data, context={"resource": "channel", "identifier": channel})
        return {"channel": data.get("channel"), "timestamp": data.get("ts"), "status": "sent"}


#: Slack's `ok: false` error strings mapped onto the shared contract. Slack
#: uses string codes rather than HTTP statuses, so without this the caller had
#: no way to know that `invalid_auth` means reconnect and `ratelimited` means wait.
_SLACK_ERROR_CODES: dict[str, tuple[str, bool, bool]] = {
    # error name -> (contract code, retryable, reconnect_required)
    "invalid_auth": (ProviderErrorCode.AUTH_EXPIRED, False, True),
    "not_authed": (ProviderErrorCode.AUTH_MISSING, False, True),
    "account_inactive": (ProviderErrorCode.AUTH_EXPIRED, False, True),
    "token_revoked": (ProviderErrorCode.AUTH_EXPIRED, False, True),
    "token_expired": (ProviderErrorCode.AUTH_EXPIRED, False, True),
    "missing_scope": (ProviderErrorCode.PERMISSION_DENIED, False, True),
    "no_permission": (ProviderErrorCode.PERMISSION_DENIED, False, False),
    "access_denied": (ProviderErrorCode.PERMISSION_DENIED, False, False),
    "channel_not_found": (ProviderErrorCode.NOT_FOUND, False, False),
    "user_not_found": (ProviderErrorCode.NOT_FOUND, False, False),
    "message_not_found": (ProviderErrorCode.NOT_FOUND, False, False),
    "is_archived": (ProviderErrorCode.CONFLICT, False, False),
    "ratelimited": (ProviderErrorCode.RATE_LIMITED, True, False),
    "service_unavailable": (ProviderErrorCode.PROVIDER_UNAVAILABLE, True, False),
    "fatal_error": (ProviderErrorCode.PROVIDER_UNAVAILABLE, True, False),
    "internal_error": (ProviderErrorCode.PROVIDER_ERROR, False, False),
}

_SLACK_USER_MESSAGES: dict[str, str] = {
    "invalid_auth": "Slack rejected this connection's authorization. Reconnect the account in Settings.",
    "not_authed": "This Slack connection is not authenticated. Reconnect it in Settings.",
    "account_inactive": "This Slack workspace is inactive. Ask a workspace owner to reactivate it.",
    "token_revoked": "This Slack authorization was revoked. Reconnect the account in Settings.",
    "token_expired": "This Slack authorization expired. Reconnect the account in Settings.",
    "missing_scope": "Slack has not granted the permission this action needs. Reconnect it in Settings to grant it.",
    "no_permission": "Slack denied permission for this operation.",
    "access_denied": "Slack denied permission for this operation.",
    "channel_not_found": "Slack could not find that channel.",
    "user_not_found": "Slack could not find that user.",
    "message_not_found": "Slack could not find that message.",
    "is_archived": "That Slack channel is archived and cannot be used.",
    "ratelimited": "Slack rate-limited the request. Please retry shortly.",
    "service_unavailable": "Slack is temporarily unavailable. This is usually temporary.",
    "fatal_error": "Slack is temporarily unavailable. This is usually temporary.",
    "internal_error": "Slack could not complete the request.",
}


def _slack_api_error(
    tool_name: str,
    payload: Any,
    *,
    context: Optional[Mapping[str, Any]] = None,
) -> ConnectorProviderError:
    """Translate a Slack `ok: false` body into the shared provider contract."""
    error_name = ""
    retry_after: int | None = None
    if isinstance(payload, Mapping):
        raw_name = payload.get("error")
        error_name = str(raw_name).strip() if raw_name else ""
        # Slack reports the wait window in headers, and repeats it as
        # `retry_after` in the body. Only digits are trusted.
        raw_retry = payload.get("retry_after")
        try:
            candidate = int(str(raw_retry).strip())
        except (TypeError, ValueError):
            candidate = -1
        if candidate >= 0:
            retry_after = min(candidate, 3600)
    code, retryable, reconnect_required = _SLACK_ERROR_CODES.get(
        error_name, (ProviderErrorCode.PROVIDER_ERROR, False, False)
    )
    message = _SLACK_USER_MESSAGES.get(
        error_name, "Slack could not complete the request. Please retry."
    )
    if context and context.get("identifier") and code == ProviderErrorCode.NOT_FOUND:
        message = f"Slack could not find {context['resource']} {context['identifier']}."
    logger.info("slack.api_error", tool=tool_name, slack_error=error_name[:60], code=str(code))
    return ConnectorProviderError(
        "slack",
        str(code),
        message,
        retryable=retryable,
        reconnect_required=reconnect_required,
        retry_after_seconds=retry_after,
        context=context,
    )


async def _atlassian_cloud_id(access_token: str) -> str:
    try:
        resources = await request_json(
            "atlassian", "GET", "https://api.atlassian.com/oauth/token/accessible-resources",
            access_token=access_token,
        )
    except ConnectorProviderError as exc:
        # No Atlassian site for this account is a permission/selection problem,
        # not an unknown provider fault, and the user needs to be told that.
        if exc.code == ProviderErrorCode.NOT_FOUND or exc.status_code in (400, 403):
            raise ConnectorProviderError(
                "atlassian",
                ProviderErrorCode.PERMISSION_DENIED,
                "No Atlassian site is available for this account. Reconnect Atlassian and grant site access.",
                reconnect_required=True,
            ) from None
        raise
    if not isinstance(resources, list) or not resources:
        # Authorized, but no site is visible. The message tells the user to
        # reconnect, so `reconnect_required` must be set: a flag the runtime
        # branches on that disagrees with the sentence the user reads is worse
        # than either alone.
        raise ConnectorProviderError(
            "atlassian",
            ProviderErrorCode.PERMISSION_DENIED,
            "No Atlassian sites are available for this account. Reconnect Atlassian and grant site access.",
            reconnect_required=True,
        )
    first = resources[0]
    site_id = first.get("id") if isinstance(first, Mapping) else None
    if not site_id:
        raise ConnectorProviderError(
            "atlassian",
            ProviderErrorCode.MALFORMED_RESPONSE,
            "Atlassian returned an unexpected site list. Reconnect Atlassian and try again.",
        )
    return str(site_id)


class AtlassianSearchIssuesTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="atlassian_search_issues",
            description="Search Jira issues in the signed-in user's Atlassian site.",
            parameters={"jql": {"type": "string", "description": "Jira Query Language expression."}, "limit": {"type": "integer", "description": "Maximum results, 1-50."}},
            required=["jql"],
        )

    async def execute(self, jql: str, limit: int = 10, **_: Any) -> dict[str, Any]:
        credentials = await credentials_for("atlassian")
        cloud_id = await _atlassian_cloud_id(credentials["access_token"])
        data = await request_json(
            "atlassian", "GET",
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search",
            access_token=credentials["access_token"],
            params={"jql": jql, "maxResults": max(1, min(int(limit), 50))},
        )
        issues = []
        for item in (data.get("issues") or []) if isinstance(data, Mapping) else []:
            fields = item.get("fields") or {}
            issues.append({
                "key": item.get("key"),
                "summary": fields.get("summary"),
                "status": (fields.get("status") or {}).get("name"),
                "assignee": ((fields.get("assignee") or {}).get("displayName")),
            })
        return {"issues": issues}


class AtlassianCreateIssueTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="atlassian_create_issue",
            description="Create a Jira issue in the signed-in user's Atlassian site. Requires explicit user confirmation.",
            parameters={
                "project": {"type": "string", "description": "Project key, e.g. PROJ."},
                "summary": {"type": "string", "description": "Issue title."},
                "description": {"type": "string", "description": "Issue body text.", "default": ""},
                "issue_type": {"type": "string", "description": "Issue type name, e.g. Task.", "default": "Task"},
            },
            required=["project", "summary"],
            irreversible=True,
        )

    async def execute(self, project: str, summary: str, description: str = "", issue_type: str = "Task", **_: Any) -> dict[str, Any]:
        credentials = await credentials_for("atlassian")
        cloud_id = await _atlassian_cloud_id(credentials["access_token"])
        payload = {
            "fields": {
                "project": {"key": project},
                "summary": summary,
                "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": description or summary}]}]},
                "issuetype": {"name": issue_type},
            }
        }
        data = await request_json(
            "atlassian", "POST",
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue",
            access_token=credentials["access_token"],
            json=payload,
            context={"resource": "project", "identifier": project},
        )
        if not isinstance(data, Mapping):
            raise ConnectorProviderError(
                "atlassian", "MALFORMED_RESPONSE",
                "Atlassian returned an unexpected create-issue response. Check the project before retrying.",
            )
        return {"key": data.get("key"), "id": data.get("id"), "url": data.get("self")}
