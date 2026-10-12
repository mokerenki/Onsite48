# Task Persistence and Transcript Corrective Release

This corrective release addresses three related product failures: tasks missing after a refresh, paused work appearing as a red failure in the sidebar, and earlier answers disappearing from the active transcript after a follow-up.

## What Changed

| Issue | Corrective behavior |
|---|---|
| Two competing task-history concepts | The live WebSocket stream no longer writes a second Redis task-history mirror. PostgreSQL `TaskThread` and `TaskEvent` records are the sole user-facing task history and restoration source. Redis remains limited to low-level progress/checkpoint support. |
| Informational errors turning tasks red | Generic protocol errors no longer update a durable task to `failed`. Only a terminal `task_error` can do this. Explicitly paused work is protected from late terminal stream events. |
| Paused sidebar icon was red | Pause, resume, cancellation, and approval-wait transitions now synchronize the sidebar summary immediately. Paused and waiting-for-approval work uses the yellow pause state; only failed and cancelled work use red. |
| Earlier answers vanished | The transcript restoration logic now retains every stored user/assistant pair, rather than removing the newest completed pair for a separate render path. The user can scroll through all prior responses in the same thread. |
| Conversation history was truncated too aggressively | Durable task metadata now retains the most recent 100 message turns, rather than 20. |

## Install on Windows

1. Stop the current containers, preserving all task data:

   ```powershell
   docker compose down
   ```

   Do not include `-v`.

2. Extract the corrective ZIP into a fresh folder and copy your existing `.env` beside `docker-compose.yml`.

3. Start the updated source:

   ```powershell
   docker compose config
   docker compose up --build -d
   ```

4. Open `http://localhost`, sign in, and perform the acceptance checks below.

## Acceptance Checks

1. Start a task such as `Who are you?`, wait for the full answer, then ask `What time is it?` in the same thread.
2. Scroll to the top of the transcript. The first answer must remain visible and the transcript must not force you back down unless you return near the bottom.
3. Refresh the browser. Both the prior task and its complete multi-turn transcript must reappear in the sidebar and workspace.
4. Start a longer task, use **Pause**, and confirm its sidebar icon is yellow. Refresh the page and verify it remains paused rather than red.
5. Resume or cancel the task and verify the status changes to running or cancelled respectively.

## Verification Performed

The backend task-event, WebSocket, and persistence modules passed Python compilation. The focused backend suite passed **13 tests**, including new regressions for status authority and long conversation retention. The Next.js production build passed with the revised transcript and sidebar synchronization code.