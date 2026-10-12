---
name: static-website-build
description: Build or update a website project, validate it, create an isolated live preview, and package its source for delivery.
---

# Website Build and Preview

Use this workflow for creating or editing websites. A request to create a site is not a request to browse an existing site; do not open Agent Computer or ask for browser-session permission just to build.

## Build
1. Create a conventional project in the task workspace. For a small static site, use `site/index.html`, `site/styles.css`, and optional `site/app.js`; for a larger frontend, preserve its framework's normal source and package files.
2. Keep project assets local and use relative asset paths where practical. Include a valid HTML5 skeleton, a descriptive title, a language attribute, and a responsive viewport meta tag.
3. For framework projects, install only the required dependencies and run the available build and test scripts. Fix build errors before previewing. Do not claim an SSR/API-backed application works as a static export if it needs a running server or backend that is not available.

## Verify and preview
4. For a static site or completed static export, call `preview_website` with the directory that contains its `index.html` (for example, `site`, `dist`, `build`, or `out`).
5. Review the HTML/local-asset audit and desktop/mobile browser smoke-check findings returned by the tool. Fix material failures and preview again. If Playwright/Chromium is unavailable, report that browser verification was skipped rather than claiming it passed.
6. Use the live preview URL to visually review the page at desktop and mobile widths. The preview is temporary, read-only, sandboxed, and not a public deployment.

## Deliver
7. Call `package_website` on the complete source project, not only the compiled output. The user should receive a downloadable ZIP with source files and a README. Include a separate production build output only when it materially helps the user.
8. State which build/tests/browser checks ran and their results. Link the live preview while it is still valid and attach the source ZIP.
9. Never deploy or publish as part of previewing. If the user explicitly requests publication, call `publish_website` only through the separate approval workflow. This requires a configured deployment webhook; if none is configured, explain that publication is unavailable and leave the preview/source package intact.
