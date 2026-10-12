# Chat UI and Sidebar Fix Report

## Duplicate answer root cause

The chat completion handler appended the completed answer to `conversationTurns` and also assigned the same answer to `finalAnswer`. The renderer intentionally displays both collections: archived conversation turns first, followed by the active task's Answer card. This produced two identical answers for one task.

The completion handler now keeps the completed task in the single active Answer card. Durable history remains available through task restoration and later follow-up handling, but the current answer is not duplicated into the archived-turn collection during the same render lifecycle.

## New Task root cause

The reset function cleared React state but did not remove `onsite48_thread_id` from local storage. The home page also auto-selected the remembered thread whenever `activeTaskId` was null. Pressing New Task therefore reset the chat and immediately selected the previous task again.

New Task now clears the persisted thread, sets the parent selection to null, and enables a suppression flag that prevents automatic task selection until the user explicitly selects a sidebar task or a newly submitted task creates a new thread.

## Sidebar redesign

Recent task metadata now supports `iconKey`, `iconColor`, and `category`. The sidebar has expanded semantic icon coverage for research, development, communication, browser work, calendar, marketing, documents, spreadsheets, data, people, work/connectors, shopping, travel, health, education, home, security, automation, and general chat. Explicit metadata takes precedence over title heuristics, while legacy tasks continue to receive an icon through safe fallback classification.

Task rows now use larger rounded icon tiles, show an optional category/domain label, preserve status dots, and include the status/category in the icon tooltip.

## Validation

Validation completed after these changes: frontend TypeScript passed, all 5 frontend regression tests passed, and the Next.js production build completed successfully with all 16 app pages generated. The prior backend baseline remains 247 passing tests.