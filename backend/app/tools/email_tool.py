"""
EmailTool - Send emails via SMTP.
"""

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Any, Dict

import structlog

from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.email")


class EmailTool(BaseTool):
    def __init__(self):
        super().__init__()
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.from_address = os.getenv("SMTP_FROM", self.smtp_user)

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="email",
            description="Send an email via SMTP. Requires SMTP_USER/SMTP_PASSWORD env vars. Automatically sorting, prioritizing, and labeling incoming customer or support inquiries.",
            parameters={
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body (plain text)"},
                "cc": {"type": "array", "items": {"type": "string"}, "description": "Optional CC addresses"},
            },
            required=["to", "subject", "body"],
            irreversible=True,
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        cc = kwargs.get("cc", [])

        if not to:
            return {"output": "Error: 'to' is required."}
        if not self.smtp_user or not self.smtp_password:
            return {"output": "Error: SMTP credentials not configured (SMTP_USER / SMTP_PASSWORD)."}

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.from_address or self.smtp_user
        msg["To"] = to
        if cc:
            msg["Cc"] = ", ".join(cc)

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_address or self.smtp_user, [to, *cc], msg.as_string())
            return {"output": f"Email sent to {to}."}
        except Exception as exc:
            logger.error("email.send_failed", to=to, error=str(exc))
            return {"output": f"Error sending email: {exc}"}