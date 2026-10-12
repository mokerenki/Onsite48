"""Declarative connector manifests for the Onsite48 marketplace.

A manifest describes what Synth may present and what credentials the operator must
configure. It never loads third-party code or executes an MCP server directly;
that keeps marketplace discovery separate from the controlled connector runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ConnectorManifest:
    id: str
    name: str
    version: str
    category: str
    description: str
    auth_type: Literal["oauth2", "api_key"]
    setup_url: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    required_environment: tuple[str, ...] = field(default_factory=tuple)
    status: Literal["supported", "contract_ready", "planned"] = "contract_ready"

    def public(self) -> dict:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        result["required_environment"] = list(self.required_environment)
        return result


BUILT_IN_MANIFESTS: tuple[ConnectorManifest, ...] = (
    ConnectorManifest("gmail", "Gmail", "1.0", "communication", "Read email context and prepare approval-gated draft or send actions.", "oauth2", "https://console.cloud.google.com/apis/credentials", ("search mail", "draft message", "approval-gated send"), ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"), "supported"),
    ConnectorManifest("google_calendar", "Google Calendar", "1.0", "productivity", "List calendar events and create approval-gated calendar events.", "oauth2", "https://console.cloud.google.com/apis/credentials", ("list events", "create event"), ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"), "supported"),
    ConnectorManifest("slack", "Slack", "1.0", "communication", "Read approved channel context and prepare approval-gated workspace messages.", "oauth2", "https://api.slack.com/apps", ("list channels", "read channel context", "approval-gated send"), ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"), "supported"),
    ConnectorManifest("github", "GitHub", "1.0", "development", "Read repositories and prepare approval-gated issue creation.", "oauth2", "https://github.com/settings/developers", ("list repositories", "approval-gated create issue"), ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"), "supported"),
    ConnectorManifest("hubspot", "HubSpot", "1.0", "marketing", "Marketing and CRM signal ingestion contract for contacts, campaigns, and forms.", "oauth2", "https://developers.hubspot.com/docs/api/creating-an-app", ("ingest contact and campaign signals", "create approval proposal"), ("HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET"), "contract_ready"),
    ConnectorManifest("google_analytics", "Google Analytics", "1.0", "analytics", "Marketing performance signal ingestion contract for GA4 properties.", "oauth2", "https://console.cloud.google.com/apis/credentials", ("ingest campaign performance", "detect traffic anomalies"), ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"), "contract_ready"),
    ConnectorManifest("mailchimp", "Mailchimp", "1.0", "marketing", "Campaign and audience monitoring contract.", "api_key", "https://mailchimp.com/developer/marketing/guides/quick-start/", ("ingest campaign metrics", "draft campaign follow-ups"), ("MAILCHIMP_API_KEY",), "contract_ready"),
    ConnectorManifest("brand24", "Brand24", "1.0", "marketing", "Brand-mention and sentiment monitoring contract.", "api_key", "https://developers.brand24.com/", ("ingest mentions", "detect reputation spikes"), ("BRAND24_API_KEY",), "contract_ready"),
    ConnectorManifest("meta_ads", "Meta Ads", "1.0", "marketing", "Paid-campaign monitoring contract. Access may require Meta app review and business verification.", "oauth2", "https://developers.facebook.com/docs/marketing-apis/", ("ingest ad metrics", "detect campaign anomalies"), ("META_APP_ID", "META_APP_SECRET"), "contract_ready"),
    ConnectorManifest("atlassian", "Atlassian (Rovo)", "1.0", "productivity", "Connect Atlassian Cloud to search Jira issues and Confluence content.", "oauth2", "https://developer.atlassian.com/console/myapps/", ("search issues", "create issue"), ("ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET"), "supported"),
)


def marketplace_manifests() -> list[dict]:
    return [manifest.public() for manifest in BUILT_IN_MANIFESTS]