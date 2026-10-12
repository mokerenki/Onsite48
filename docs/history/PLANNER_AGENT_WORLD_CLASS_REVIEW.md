# Planner Agent World-Class Engineering Review

**Scope:** Supplied `planner.py` from `pasted_content.txt`, compared with the repository’s Gmail connector tools, intent contracts, executor contracts, and existing tests.

**Primary issue reviewed:** The user asks, “How many email drafts do I have?”, but the agent returns the ten latest received emails.

## Executive conclusion

The behavior is not primarily a model-quality problem. It is a **capability and intent-contract failure**.

The planner has a Gmail message-search capability, but it does not have a first-class Gmail draft-list or draft-count capability. The generic search tool defaults to `limit=10`, returns received-message metadata, and is described broadly enough that the model can select it for any email-related question. “Drafts” is never converted into a provider query or a dedicated operation. The planner therefore has no reliable way to answer the requested question.

The production fix should not be “make the system prompt stronger.” The correct fix is:

1. Add a dedicated Gmail drafts capability.
2. Add a typed Gmail intent model that distinguishes `received_messages`, `sent_messages`, `drafts`, `threads`, and `count`.
3. Add deterministic compilation for draft-count and draft-list requests before model planning.
4. Make the tool return a typed count/list result rather than formatted message text only.
5. Add semantic plan validation that rejects a received-message tool when the user requested drafts.
6. Add answer synthesis rules that prevent the model from changing “count” into “list the latest N items.”
7. Add end-to-end tests from prompt → plan → tool arguments → result → final answer.

## 1. Exact root cause of the draft-count bug

### 1.1 The supplied planner has no draft-count intent

The supplied file defines:

- `_GMAIL_INBOX_PATTERN` around lines 212–223.
- `_gmail_inbox_plan()` around lines 414–440.
- `_GMAIL_DRAFT_PATTERN` and `_GMAIL_DRAFT_INTENT_PATTERN` around lines 443–450.
- `_gmail_draft_plan()` around lines 476–535.
- `_gmail_send_plan()` around lines 538–598.

The draft-specific logic only recognizes requests to **create** a draft. It does not recognize requests to list or count existing drafts.

`_GMAIL_DRAFT_PATTERN` matches verbs such as `write`, `create`, `make`, `compose`, and `draft` followed by `draft`, `email`, or `message`. In the question “How many email drafts do I have?”, there is no draft-creation verb. The pattern therefore does not create a draft plan, which is correct, but the planner has no alternative draft-read plan.

`_GMAIL_INBOX_PATTERN` also does not model “count my drafts.” It is oriented around `email`, `inbox`, `gmail`, `unread`, `latest`, `check`, `read`, `show`, and `list`. Even if the model later notices the word “drafts,” the deterministic layer has not represented the requested resource.

### 1.2 The fallback tool is semantically wrong

The repository registers `GmailSearchMessagesTool` with this contract:

```python
name="gmail_search_messages"
description="Search the signed-in user's Gmail mailbox and return one page of message metadata."
parameters={
    "query": "Gmail search query.",
    "limit": "Messages per page, 1-100.",
    "page_token": "Gmail nextPageToken from the previous page.",
}