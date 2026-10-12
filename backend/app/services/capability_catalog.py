"""Deterministic capability registration shared by every Onsite48 runtime.

This module deliberately centralizes business connector registration.  A runtime
may still omit surfaces that physically cannot exist (for example a user-owned
browser bridge in a headless worker), but interactive and autonomous work must
not silently differ in their business connector or custom MCP capabilities.
"""

from __future__ import annotations

from app.tools.connector_tools import (
    AtlassianCreateIssueTool,
    AtlassianSearchIssuesTool,
    GitHubCreateIssueTool,
    GitHubGetFileTool,
    GitHubGetRepositoryTool,
    GitHubListIssuesTool,
    GitHubListRepositoriesTool,
    GitHubCommentIssueTool,
    GitHubUpdateFileTool,
    GitHubUpdateIssueTool,
    GmailCountDraftsTool,
    GmailCreateDraftTool,
    GmailDeleteDraftTool,
    GmailDeleteMessageTool,
    GmailTrashMessageTool,
    GmailListDraftsTool,
    GmailSearchMessagesTool,
    GmailSendMessageTool,
    GoogleCalendarCreateEventTool,
    GoogleCalendarDeleteEventTool,
    GoogleCalendarListEventsTool,
    GoogleCalendarUpdateEventTool,
    SlackListChannelsTool,
    SlackSendMessageTool,
)
from app.tools.custom_connector_tools import (
    CustomApiGetTool,
    CustomApiMutationTool,
    CustomConnectorListTool,
    CustomMcpCallTool,
    CustomMcpListToolsTool,
)
from app.tools.registry import ToolRegistry


def register_business_capabilities(registry: ToolRegistry) -> ToolRegistry:
    """Register all tenant-scoped connector and custom-MCP capabilities.

    The policy engine—not runtime registration—decides whether an individual run
    can invoke a mutating capability. This prevents scheduled workflows from
    losing planning context while retaining hard-stop and delegated-authority
    enforcement in the executor.
    """
    for tool in (
        GitHubListRepositoriesTool(),
        GitHubCreateIssueTool(),
        GitHubGetRepositoryTool(),
        GitHubListIssuesTool(),
        GitHubUpdateIssueTool(),
        GitHubCommentIssueTool(),
        GitHubGetFileTool(),
        GitHubUpdateFileTool(),
        GmailSearchMessagesTool(),
        GmailListDraftsTool(),
        GmailCountDraftsTool(),
        GmailSendMessageTool(),
        GmailCreateDraftTool(),
        GmailDeleteDraftTool(),
        GmailDeleteMessageTool(),
        GmailTrashMessageTool(),
        GoogleCalendarListEventsTool(),
        GoogleCalendarCreateEventTool(),
        GoogleCalendarUpdateEventTool(),
        GoogleCalendarDeleteEventTool(),
        AtlassianSearchIssuesTool(),
        AtlassianCreateIssueTool(),
        SlackListChannelsTool(),
        SlackSendMessageTool(),
        CustomConnectorListTool(),
        CustomApiGetTool(),
        CustomApiMutationTool(),
        CustomMcpListToolsTool(),
        CustomMcpCallTool(),
    ):
        registry.register(tool, source="business_connector")
    return registry