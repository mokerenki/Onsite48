"""
WhatsApp Tool - Send and receive messages via Twilio WhatsApp Business API
"""

import os
import structlog
from typing import Dict, Any, Optional, List
import httpx
from app.services.provider_errors import (
    ProviderError,
    provider_error_from_exception,
    provider_error_from_status,
    provider_json,
)
from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.whatsapp")

class WhatsAppTool(BaseTool):
    def __init__(self):
        super().__init__()
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.from_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages"
        self.auth = (self.account_sid, self.auth_token)

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="whatsapp",
            description="Send and receive WhatsApp messages via Twilio.",
            parameters={
                "action": {
                    "type": "string",
                    "enum": ["send_message", "send_template"],
                    "description": "The operation to perform"
                },
                "to": {
                    "type": "string",
                    "description": "Recipient phone number (in E.164 format, e.g., +1234567890)"
                },
                "body": {
                    "type": "string",
                    "description": "Message content (for send_message action)"
                },
                "template_sid": {
                    "type": "string",
                    "description": "Template SID for send_template action"
                },
                "template_variables": {
                    "type": "object",
                    "description": "Variables for template (key-value pairs)"
                },
                "media_url": {
                    "type": "string",
                    "description": "URL to media file (image, video, audio, document)"
                }
            },
            required=["action", "to"],
            irreversible=True,
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute WhatsApp operation."""
        action = kwargs.get("action")
        to = kwargs.get("to")
        
        if not to:
            return {"error": "Recipient phone number (to) is required"}
        
        if action == "send_message":
            return await self._send_message(to, kwargs.get("body"), kwargs.get("media_url"))
        elif action == "send_template":
            return await self._send_template(to, kwargs.get("template_sid"), kwargs.get("template_variables", {}))
        else:
            return {"error": f"Unknown action: {action}"}

    async def _send_message(self, to: str, body: str, media_url: Optional[str] = None) -> Dict[str, Any]:
        """Send a WhatsApp message."""
        if not body and not media_url:
            return {"error": "Body or media_url is required"}
        
        data = {
            "To": f"whatsapp:{to}",
            "From": f"whatsapp:{self.from_number}",
            "Body": body or ""
        }
            
        if media_url:
            data["MediaUrl"] = media_url
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.base_url, auth=self.auth, data=data)
        except Exception as exc:
            # The account SID is the first path segment of base_url, so an
            # httpx error string here is a credential fragment. The class
            # name is logged; the message is assembled by the contract.
            error = provider_error_from_exception("whatsapp", exc)
            logger.error("whatsapp.send_failed", to=to, code=error.code, error=type(exc).__name__)
            return {"error": error.message, "failure": error.as_failure()}
        if response.status_code >= 400:
            error = provider_error_from_status("whatsapp", response)
            logger.error("whatsapp.send_failed", to=to, code=error.code, status=error.status_code)
            return {"error": error.message, "failure": error.as_failure()}
        try:
            result = provider_json(response, "whatsapp")
        except ProviderError as error:
            return {"error": error.message, "failure": error.as_failure()}

        return {
            "status": "success",
            "sid": result.get("sid"),
            "to": result.get("to"),
            "from": result.get("from"),
            "message": "Message sent successfully"
        }

    async def _send_template(self, to: str, template_sid: str, template_variables: Dict[str, str] = {}) -> Dict[str, Any]:
        """Send a WhatsApp template message."""
        if not template_sid:
            return {"error": "Template SID is required"}
        
        data = {
            "To": f"whatsapp:{to}",
            "From": f"whatsapp:{self.from_number}",
            "ContentSid": template_sid
        }
            
        if template_variables:
            data["ContentVariables"] = template_variables
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.base_url, auth=self.auth, data=data)
        except Exception as exc:
            error = provider_error_from_exception("whatsapp", exc)
            logger.error("whatsapp.template_send_failed", to=to, template_sid=template_sid, code=error.code, error=type(exc).__name__)
            return {"error": error.message, "failure": error.as_failure()}
        if response.status_code >= 400:
            error = provider_error_from_status("whatsapp", response)
            logger.error("whatsapp.template_send_failed", to=to, code=error.code, status=error.status_code)
            return {"error": error.message, "failure": error.as_failure()}
        try:
            result = provider_json(response, "whatsapp")
        except ProviderError as error:
            return {"error": error.message, "failure": error.as_failure()}

        return {
            "status": "success",
            "sid": result.get("sid"),
            "to": result.get("to"),
            "from": result.get("from"),
            "message": "Template message sent successfully"
        }