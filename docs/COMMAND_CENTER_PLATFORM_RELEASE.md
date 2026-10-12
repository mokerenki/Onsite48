# Onsite48 Command Center Platform Release

This release adds four connected product capabilities: **Onsite48 Bridge**, a permissioned **Source Library**, collaborative **Command Center** workspaces, and managed **proactive automations** with signed triggers and in-app notifications. It also corrects the account control so opening **Account** no longer signs a user out.

> **Security model:** Onsite48 Bridge is a user-owned local-browser companion, not an unrestricted remote browser takeover. The browser extension does not upload cookies, saved passwords, or session tokens. It acts only on the active user-authorized public tab after the agent has requested a human-approved operation.

## 1. Upgrade an Existing Windows Docker Installation

1. From the prior project folder, stop containers without deleting databases or volumes:

   ```powershell
   docker compose down
   ```

   Do **not** use `docker compose down -v`; that removes persisted accounts, tasks, library sources, and other workspace data.

2. Extract the supplied release ZIP into a fresh folder. Copy the existing `.env` file into the new `Onsite48_product` folder, next to `docker-compose.yml`. Never send `.env` contents in chat.

3. Confirm the public local origin remains correct:

   ```dotenv
   FRONTEND_URL=http://localhost
   CORS_ORIGINS=http://localhost
   COOKIE_SECURE=false
   ATTACHMENT_STORAGE_ROOT=/tmp/onsite48/uploads
   RUN_PROACTIVE_WORKER=false
   ```

   The API container keeps `RUN_PROACTIVE_WORKER=false`; the dedicated `proactive-worker` Compose service runs the durable schedules. This avoids two processes running the same schedule.

4. Rebuild and start. The backend performs Alembic migrations, including `20260813_04` for the new platform records:

   ```powershell
   docker compose config
   docker compose up --build -d
   docker compose ps
   ```

5. Wait until the database and Redis are healthy. Then verify:

   ```powershell
   Invoke-RestMethod http://localhost/api/health/detailed
   ```

## 2. User-Facing Workspaces

| Workspace | Purpose | Important boundary |
|---|---|---|
| **Source Library** | Indexes approved organization documents and lets agents retrieve relevant, titled excerpts. | Image-only and scanned documents remain honestly unavailable to text-only model routes. |
| **Command Center** | Shared marketing projects, work items, owners, status, comments, and visible handoffs. | It organizes proposed and approved work; it does not bypass external-action approval gates. |
| **Automations** | Daily briefing schedules and signed inbound event triggers. | A trigger creates a signal and notification; it never changes an external account by itself. |
| **Onsite48 Bridge** | Pairs the local browser extension for approved active-tab read, inspect, scroll, and navigation work. | It cannot read browser cookies or passwords, access every tab, or perform form submission/clicking. |

The Account button now opens an account menu. Choose **Sign out** deliberately from that menu. Notifications appear as a bell beside Account and have their own independently scrollable inbox.

## 3. Install Onsite48 Bridge in Chrome or Edge

1. In the release folder, open `extension/onsite48-bridge`.
2. Open `chrome://extensions` in Chrome or `edge://extensions` in Microsoft Edge.
3. Enable **Developer mode**.
4. Select **Load unpacked** and choose the `extension/onsite48-bridge` folder.
5. In Onsite48, open **Onsite48 Bridge** from the sidebar.
6. Select **Generate code**. Copy the displayed Device ID and one-time pairing code into the extension popup.
7. The code expires in ten minutes and is shown only once. Do not store it in `.env`, a shared document, or a message.
8. Keep the extension enabled only on devices you control. Use the Onsite48 Bridge workspace to disconnect a device instantly.

### Approved bridge operations

| Operation | What happens | Approval requirement |
|---|---|---|
| Read active tab | The extension returns bounded visible page text and tab metadata. | Required |
| Inspect active tab | The extension returns visible links, buttons, and form controls. | Required |
| Scroll active tab | The extension moves the currently active page viewport. | Required |
| Navigate active tab | The extension opens a public HTTP(S) address in the active tab. | Required |

The supplied extension intentionally does **not** click controls, fill forms, submit actions, access downloads, enumerate browser history, or export cookies. Those capabilities should not be added without a separate threat model, visible per-action approval, privacy review, and audit design.

## 4. Configure Proactive Work

### Daily briefing

Open **Marketing Operations** and select **Enable 08:00 daily**. The schedule is user-scoped, timezone-aware, and processed by the dedicated worker. It retries isolated failures with exponential backoff and moves repeatedly failing schedules to an explicit recovery state rather than silently blocking other users.

### Signed inbound trigger

Open **Automations**, create a trigger, and save the one-time secret at the sender. The sender posts an event to:

```text
http://localhost/api/proactive/triggers/<trigger-id>/ingest