# Onsite48 Connector Onboarding Guide

**Audience:** Onsite48 operators deploying the product through Docker Desktop on Windows.  
**Author:** Manus AI  
**Last updated:** 12 August 2026

## 1. Purpose and Current Product Boundaries

This guide explains how to create the provider credentials Onsite48 needs, put them in the local `.env` file, and verify each connection from the Connector Marketplace. It deliberately separates **supported connectors** from **contract-ready connectors**. A contract-ready entry appears in the Marketplace and has a defined credential contract, but its provider-specific ingestion and agent-action adapter is not yet implemented. Do not market a contract-ready connector as live automation.

| Connector | Marketplace state | Credential method | What works in this release |
|---|---:|---|---|
| Gmail | **Supported** | Google OAuth 2.0 | Search email and prepare approval-gated sends. |
| Slack | **Supported** | Slack OAuth 2.0 | List channels, read approved context, and prepare approval-gated messages. |
| GitHub | **Supported** | GitHub OAuth 2.0 | List repositories and prepare approval-gated issue creation. |
| Salesforce | Connection catalog only | Salesforce OAuth 2.0 | The OAuth connection can be configured, but a dedicated agent tool adapter is not exposed yet. |
| Google Analytics 4 | **Contract-ready** | Google OAuth 2.0 | Credential contract and marketing data model only; GA4 signal ingestion is not yet live. |
| HubSpot | **Contract-ready** | HubSpot OAuth 2.0 | Credential contract and marketing/CRM signal contract only; no live adapter yet. |
| Meta Ads | **Contract-ready** | Meta OAuth 2.0 | Credential contract and paid-media monitoring contract only; no live adapter yet. |
| Mailchimp | **Contract-ready** | API key | Credential contract and campaign/audience monitoring contract only; no live adapter yet. |
| Brand24 | **Contract-ready** | API key | Credential contract and brand-signal monitoring contract only; no live adapter yet. |

> **Security rule:** Put client secrets and API keys only in `.env`, never in chat, screenshots, Git, frontend code, or an unencrypted document. Onsite48 encrypts persisted connector credentials with `CONNECTOR_ENCRYPTION_KEY`; keep that key stable and backed up securely. Changing it without a migration makes stored connector credentials unreadable.

## 2. Local Redirect URI Map

Your local Docker deployment is served through Nginx at `http://localhost`, not ports `3000` or `8000`. Redirect URIs must match the provider configuration exactly, including scheme, hostname, path, case, and any trailing slash behavior.[1]

| Purpose | Exact local URI | Configure it where |
|---|---|---|
| Google sign-in for the Onsite48 user account | `http://localhost/api/auth/oauth/google/callback` | Google OAuth client’s authorized redirect URIs |
| GitHub sign-in for the Onsite48 user account | `http://localhost/api/auth/oauth/github/callback` | GitHub OAuth app callback URL |
| Gmail connector | `http://localhost/api/connectors/oauth/callback/gmail` | Google OAuth client’s authorized redirect URIs |
| Slack connector | `http://localhost/api/connectors/oauth/callback/slack` | Slack app → **OAuth & Permissions** → Redirect URLs |
| Salesforce connector | `http://localhost/api/connectors/oauth/callback/salesforce` | Salesforce external client/connected app callback URL |
| Future HubSpot connector | `http://localhost/api/connectors/oauth/callback/hubspot` | HubSpot app Auth settings |
| Future Meta Ads connector | `http://localhost/api/connectors/oauth/callback/meta_ads` | Meta app → Facebook Login settings |
| Future Google Analytics connector | `http://localhost/api/connectors/oauth/callback/google_analytics` | Google OAuth client’s authorized redirect URIs |

Set the connector callback base once in `.env`:

```dotenv
# Local Docker Desktop deployment
OAUTH_REDIRECT_BASE_URL=http://localhost/api/connectors/oauth/callback

# Existing first-party account sign-in
FRONTEND_URL=http://localhost
CORS_ORIGINS=http://localhost