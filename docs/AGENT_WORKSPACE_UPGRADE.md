# Onsite48 Agent Workspace Upgrade

This upgrade replaces the earlier **text-only, first-viewport** experience with an attachment-aware task runtime and a navigable isolated computer. It is designed as a practical step toward a high-quality autonomous-agent workspace while retaining clear safety boundaries.

## 1. What Changed

| Area | Previous behavior | Upgraded behavior |
|---|---|---|
| Attachments | Only selected plain-text extensions were read; other uploads silently reached the model without useful text. | Text, PDF, DOCX, PPTX, XLSX/XLSM, CSV, JSON, Markdown, and common code/text files are extracted at upload time and passed into the task context. |
| Upload transparency | A file could appear attached without showing whether it was usable by the agent. | Each attachment shows **Ready** when model-readable text was extracted, or **No text** with an honest explanation. |
| Attachment safety | File text had no explicit provenance framing. | Extracted material is labeled as user-provided, untrusted reference content. It is bounded to prevent a few large files from overwhelming task context. |
| REST task API | The authenticated task API did not provide a task-launch endpoint with attachment parity. | `POST /api/tasks/run` accepts the same attachment IDs, persists a task thread, and executes through the canonical event stream. |
| Chat transcript | Automatic scrolling could select an outer page instead of the transcript itself. | The transcript is an explicit bounded scroll container; automatic following changes only its own scroll position and stops when the user scrolls upward. |
| Agent computer | The workspace showed only the initial browser viewport. | It reports remote-browser position and offers isolated **Back**, **Scroll up**, **Scroll down**, refresh, and paginated page-reading capabilities. |
| Runtime resilience | The fallback model path could fail after one transient provider error. | The fallback path has bounded exponential retry and clearer diagnostic logging. |

> **Honest capability boundary:** The configured Kimi and DeepSeek request format is text-only. Image uploads are preserved but are marked **No text** rather than pretending that their pixels were analyzed. Scanned/image-only PDFs are likewise reported if no selectable text can be extracted. Add a deliberately configured vision pipeline before claiming image understanding.

## 2. Upgrade on Windows with Docker Desktop

1. Stop the existing stack from its current project folder. Do **not** add `-v`, because that removes persisted user data and attachments.

   ```powershell
   docker compose down
   ```

2. Extract the new ZIP into a new folder. Copy the existing `.env` file into the extracted `Onsite48_product` folder, beside `docker-compose.yml`. Never replace it with a sample file and never place credentials in chat.

3. Preserve or add this non-secret local setting in `.env`:

   ```dotenv
   ATTACHMENT_STORAGE_ROOT=/tmp/onsite48/uploads
   ```

   This is the established path inside the existing persistent Docker volume. Keeping it unchanged ensures that previously uploaded files remain visible after the application upgrade.

4. Rebuild the backend so its image installs the PDF and Office-document extractors:

   ```powershell
   docker compose config
   docker compose up --build -d
   docker compose ps
   ```

5. Wait for the backend to become healthy, then open **http://localhost**. Do not use the internal ports `3000` or `8000` in the browser.

## 3. Required Acceptance Tests

| Test | Steps | Expected result |
|---|---|---|
| Plain-text source | Upload a `.txt` or `.md` file containing a unique sentence. Wait for the chip to say **Ready**. Ask Synth to quote that sentence. | The answer uses the uploaded source. |
| Office/PDF source | Upload a text-bearing PDF, DOCX, PPTX, or XLSX file. Wait for **Ready**, then ask a question about a unique value in the file. | The answer uses the extracted document text. |
| Unreadable source | Upload an image or scanned image-only PDF. | The chip says **No text** and explains the limitation; Synth does not pretend it read the image. |
| Task continuation | Ask a task using an attachment, wait for completion, then send a follow-up asking about the same document. | The follow-up stays in the conversation thread and the task history remains durable. |
| Chat scrolling | Produce a long answer, scroll upward, and read from the top. | The transcript remains at the chosen position instead of forcing itself down. |
| Agent computer | Open a long public page in **Agent computer**. Click **Scroll down**, then **Scroll up**. | The remote viewport screenshot changes and the position label updates. |
| Refresh recovery | Refresh the browser after a completed attachment task. | The saved task reopens from the persistent history; the attachment record remains associated with it. |

## 4. REST Task API Contract

The durable REST endpoint is available at `POST /api/tasks/run`. It requires an authenticated `Authorization: Bearer <access-token>` header and accepts attachment IDs returned by `POST /api/files/upload`.

```json
{
  "message": "Summarize the attached campaign report and list its three most important risks.",
  "attachments": ["8eeef2c4-0dea-4c1d-82c4-73700e544ef5"],
  "domain": "marketing"
}