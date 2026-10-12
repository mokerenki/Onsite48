"""Context-local task identity for user-scoped tool execution."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionIdentity:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    task_thread_id: str


_current_identity: ContextVar[ExecutionIdentity | None] = ContextVar("onsite48_execution_identity", default=None)


def set_execution_identity(identity: ExecutionIdentity) -> Token:
    return _current_identity.set(identity)


def reset_execution_identity(token: Token) -> None:
    try:
        _current_identity.reset(token)
    except ValueError:
        # Async-generator finalizers can run in a copied context after a
        # client disconnects. The original token cannot be reset there; clear
        # the current context instead of letting cleanup kill the task stream.
        _current_identity.set(None)


def get_execution_identity() -> ExecutionIdentity:
    identity = _current_identity.get()
    if identity is None:
        raise RuntimeError("Connector tools can only run inside an authenticated Onsite48 task.")
    return identity