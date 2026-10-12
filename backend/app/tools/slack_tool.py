"""
SlackTool - Post messages to Slack via incoming webhook or bot token.
"""

import os
from typing import Any, Dict, Optional

import structlog

from app.services.provider_errors import ProviderError, request_json
from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.slack")


class SlackTool(BaseTool):
    def __init__(self):
        super().__init__()
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.bot_token = os.getenv("SLACK_BOT_TOKEN", "")
        self.channel = os.getenv("SLACK_CHANNEL", "#general")

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="slack",
            description="Post a message to Slack via webhook or bot token.",
            parameters={
                "message": {"type": "string", "description": "Message text to post"},
                "channel": {"type": "string", "description": "Override destination channel"},
                "blocks": {"type": "array", "description": "Optional Slack Block Kit JSON array"},
            },
            required=["message"],
            irreversible=True,
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        message = kwargs.get("message", "")
        channel = kwargs.get("channel") or self.channel
        blocks = kwargs.get("blocks")
        if not message and not blocks:
            return {"output": "Error: message or blocks are required."}
        if self.webhook_url:
            return await self._post_webhook(message, blocks)
        if self.bot_token:
            return await self._post_bot(channel, message, blocks)
        return {"output": "Error: Slack not configured (SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN)."}

    async def _post_webhook(self, message: str, blocks: Optional[list]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"text": message}
        if blocks:
            payload["blocks"] = blocks
        try:
            await request_json(
                "slack_webhook", "POST", self.webhook_url, json=payload, timeout=10.0
            )
            return {"output": "Message posted to Slack webhook."}
        except ProviderError as exc:
            # The webhook URL is itself a credential: it is the secret, and
            # `httpx`'s error text embeds the full request URL. Returning
            # `str(exc)` here previously published the webhook into the
            # transcript, the durable task log and the user's chat.
            logger.error("slack.webhook_failed", code=exc.code, status=exc.status_code)
            return {"output": f"Error posting to Slack: {exc.message}", "failure": exc.as_failure()}
        except Exception as exc:
            logger.error("slack.webhook_failed", error=type(exc).__name__, exc_info=True)
            return {"output": "Error posting to Slack: the request could not be completed."}

    async def _post_bot(self, channel: str, message: str, blocks: Optional[list]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"channel": channel, "text": message}
        if blocks:
            payload["blocks"] = blocks
        try:
            data = await request_json(
                "slack", "POST", "https://slack.com/api/chat.postMessage",
                access_token=self.bot_token, json=payload, timeout=10.0,
                context={"resource": "channel", "identifier": channel},
            )
            if not isinstance(data, dict) or not data.get("ok"):
                from app.tools.connector_tools import _slack_api_error

                raise _slack_api_error("slack", data, context={"resource": "channel", "identifier": channel})
            return {"output": f"Message posted to Slack channel {channel}."}
        except ProviderError as exc:
            logger.error("slack.bot_failed", code=exc.code, status=exc.status_code)
            return {"output": f"Error posting to Slack: {exc.message}", "failure": exc.as_failure()}
        except Exception as exc:
            logger.error("slack.bot_failed", error=type(exc).__name__, exc_info=True)
            return {"output": "Error posting to Slack: the request could not be completed."}