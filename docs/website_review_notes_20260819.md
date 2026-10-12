# Onsite48 Public Website Review Notes — 19 August 2026

## Homepage: https://onsite48.world/

The homepage positions Synth as a **private-beta, secure, always-on autonomous agent workforce for business**. It frames the core product as proactive operational support: a morning briefing, alerts on threats and opportunities, and a chat interface for research, drafting, analysis, and follow-up work.

### Stated solution pages

- `/solutions/marketing`
- `/solutions/sales`
- `/solutions/finance`
- `/solutions/product`
- `/solutions/personal` — labeled “executive workflow”
- `/solutions/healthcare`

### Stated product and comparison pages

- `/pages/feature`
- `/pricing`
- `/data-processing`
- `/vs-openclaw` or an equivalent comparison route to be verified from site links
- `/vs-chatgpt`
- `/vs-trixta`

### Homepage marketing claims to verify page by page

- Proactive daily briefings and alerts.
- Six business-function solution areas: Marketing, Sales, Finance, Product, Executive Workflow, and Healthcare Admin.
- “200+ connectors, live,” including examples such as WhatsApp, YouTube, GitHub, Xero, Instagram, Asana, Slack, Figma, PayPal, Shopify, Gmail, Google Workspace, HubSpot, Meta Ads, Microsoft 365, Notion, Sage, Salesforce, SARS eFiling, Stripe, TikTok, Yoco, and iKhokha.
- Plans advertised at $15, $35, and $170 monthly, described as including different credit allowances, research, daily briefings/alerts, slides, 20 concurrent tasks, scheduled-task limits, and Cloud Computer on the highest public tier.
- Enterprise-grade security/compliance, with a linked data-processing page.

## Source

Browser review of the public homepage on 19 August 2026. This document records website claims, not independent verification of product availability or technical implementation.

## Verified routing status

On 19 August 2026, the public URLs linked from the homepage for `/solutions/marketing` and `/compare/vsOpenclaw` both returned Netlify’s **Page not found** response. This means the site currently advertises those pages in its own navigation/footer, but visitors cannot access their standalone content at the advertised paths. The same status should be checked for the remaining solution and comparison URLs before describing them as live public pages.

This creates an important distinction for the final review: the homepage contains detailed claims about Marketing and a footer link titled “vs. Openclaw,” but the standalone versus page itself does not currently present public content.

The linked **Features** route (`/pages/feature`) and standalone **Pricing** route (`/pricing`) were also verified as Netlify 404s on 19 August 2026. The product description and pricing information are therefore available only in the homepage content that was reviewed, not through the standalone routes advertised in the primary navigation.

The homepage-linked **Data Processing** route (`/data-processing`) and **Terms** route (`/terms`) also returned Netlify 404 responses on 19 August 2026. Accordingly, the homepage’s assertions of enterprise-grade security, compliance, and a linked data-processing explanation are not supported by accessible standalone public documentation at this time.

A second solution route (`/solutions/sales`) and a second comparison route (`/compare/vsChatgpt`) were likewise verified as Netlify 404s. Together with the prior checks, this indicates a broader public deployment/routing issue rather than an isolated problem with the vsOpenClaw page.

The Finance (`/solutions/finance`) and Product (`/solutions/product`) solution routes were each verified as Netlify 404s. All four tested business-solution pages therefore fail to render public standalone content despite being linked from the homepage.

The Executive Workflow (`/solutions/personal`) and Healthcare Admin (`/solutions/healthcare`) routes were also verified as Netlify 404s. All six business-function solution pages linked on the homepage have now been individually checked and none renders a public standalone page.

The final advertised comparison URL (`/compare/vsTrixta`) also returned Netlify 404. The trailing-slash variant of vsOpenClaw (`/compare/vsOpenclaw/`) was separately checked and produced the same 404. The public homepage is currently the only verified live product-marketing page among the routes advertised in its main navigation and footer.