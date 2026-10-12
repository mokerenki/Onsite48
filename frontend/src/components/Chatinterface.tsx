// frontend/src/components/Chatinterface.tsx

"use client";

import React from "react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  ArrowUp,
  Ban,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Copy,
  Download,
  Loader2,
  Paperclip,
  Presentation,
  XCircle,
  Pause,
  Play,
  Square,
  Edit,
  RefreshCw,
  Quote,
  X,
  List,
  ArrowDown,
  Monitor,
  Smartphone,
  ExternalLink,
} from "lucide-react";

import { StateInspector } from './StateInspector';
import { AgentComputerWorkspace } from './AgentComputerWorkspace';
import { MarkdownReportViewer } from './MarkdownReportViewer';
import { ArtifactDownloadButton } from './ArtifactDownloadButton';
import { FolderOpen } from 'lucide-react';
import { authHeaders, useAuth } from './AuthProvider';
import { MessageContent } from './MessageContent';
import { useBrowserSession } from '../hooks/useBrowserSession';
import AuthorizedActions from './AuthorizedActions';
import type { BrowserSessionStatus } from '../types/browser-session';
import { ExecutionTimeline, type ExecutionTimelineItem } from './ExecutionTimeline';
import {
  appendBounded,
  narrowCostMetrics,
  narrowPlanSteps,
  narrowReportArtifacts,
  narrowResponseMode,
  narrowStepResults,
  narrowTaskStatus,
  normalizeStreamEvent,
  type PlanStepRecord,
  type ReportArtifactRecord,
  type StepResultRecord,
  type StreamedEvent,
} from '../lib/streamEvent';
import { SupportIds } from './SupportIds';
import ManusHome from './ManusHome';
import { looksLikeBrowserTask } from '../lib/taskIntent';

// ─── Types ───────────────────────────────────────────────────────

type PlanStep = PlanStepRecord;

type StepResult = StepResultRecord;

interface Verification {
  status?: string;
  score?: number;
  feedback?: string | Record<string, unknown>;
}

function verificationFeedback(value: Verification["feedback"]): string {
  if (typeof value === "string") return value;
  if (!value) return "The completed plan was checked against its expected outcome.";
  if (typeof value === "object" && value !== null) {
    // Structured verifier feedback renders as readable text:
    // a message when one exists, a joined issue list otherwise.
    // A raw JSON blob is the last resort, pretty-printed.
    if (typeof value.message === "string" && value.message) return value.message;
    if (Array.isArray(value.issues) && value.issues.length) {
      const issues = value.issues.filter((issue): issue is string => typeof issue === "string");
      if (issues.length) return issues.join("; ");
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return "Verification returned structured feedback.";
    }
  }
  return "Verification returned structured feedback.";
}

export interface RecentTaskSummary {
  id: string;
  title: string;
  status?: "queued" | "running" | "paused" | "waiting_for_approval" | "completed" | "partial" | "failed" | "cancelled";
  domain?: string;
  updatedAt?: string;
  turnCount?: number;
  iconKey?: string;
  iconColor?: string;
  category?: string;
}

interface ConversationTurn {
  id: string;
  index: number;
  user: string;
  answer?: string | null;
  artifact?: ReportArtifact | null;
  createdAt?: string;
  title?: string;
}

type ReportArtifact = ReportArtifactRecord;

interface UploadedAttachment {
  id: string;
  name: string;
  size: number;
  extraction_status?: "ready" | "unavailable";
  extracted_text_characters?: number;
  extraction_warning?: string | null;
}

export interface ChatInterfaceHandle {
  /** Clears the current thread and returns to the empty-state composer. */
  reset: () => void;
}

interface ChatInterfaceProps {
  /** Parent-owned sidebar history setter. Functional updates prevent live events from overwriting loaded tasks. */
  onTasksChange?: React.Dispatch<React.SetStateAction<RecentTaskSummary[]>>;
  onActiveTaskChange?: (id: string | null) => void;
  activeTaskId?: string | null;
  activeTaskTitle?: string | null;
}

type CurrentStatus =
  | "idle"
  | "connecting"
  | "running"
  | "paused"
  | "completed"
  | "partial"
  | "cancelled"
  | "failed"
  | "disconnected"
  | "error";

interface PersistedTaskDetail {
  thread_id: string;
  status: RecentTaskSummary["status"];
  final_answer?: string | null;
  cost_metrics?: { total_cost_usd?: number };
  execution_metadata?: { conversation?: Array<{ turn_id?: string; role?: string; content?: string; at?: string }> };
  artifacts?: unknown;
  events?: Array<{ type: string; payload: StreamedEvent }>;
}

const THREAD_ID_KEY = "onsite48_thread_id";

// Rebuild conversation turns from the persisted event log.
// Used when a task is restored: the log holds every task_start
// (the user prompt) and task_complete (the final answer), so a
// restored transcript stays contiguous even when a turn streamed
// live but conversation metadata has not archived it yet.
function conversationTurnsFromEvents(events: Array<{ type: string; payload: StreamedEvent }>): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  let pending: ConversationTurn | null = null;
  let index = 0;
  for (const event of events) {
    const payload = event.payload;
    if (!payload || typeof payload !== "object") continue;
    if (event.type === "task_start" && typeof payload.message === "string" && payload.message) {
      if (pending) turns.push(pending);
      pending = {
        id: `${payload.thread_id || "thread"}-event-${index}`,
        index,
        user: payload.message,
        createdAt: payload.timestamp,
        title: payload.message.split(/[.!?]/)[0].slice(0, 72),
      };
      index += 1;
    } else if (event.type === "answer_delta" && pending) {
      const delta = typeof payload.content === "string" ? payload.content : "";
      if (delta) pending.answer = `${pending.answer || ""}${delta}`;
    } else if (event.type === "task_complete" && pending) {
      const finalAnswer = typeof payload.final_answer === "string" ? payload.final_answer : "";
      if (finalAnswer) pending.answer = finalAnswer;
      turns.push(pending);
      pending = null;
    }
  }
  if (pending) turns.push(pending);
  return turns;
}

function statusPillClasses(status: string) {
  switch (status) {
    case "completed":
      return "bg-green-100 text-green-700";
    case "partial":
      return "bg-amber-100 text-amber-700";
    case "failed":
    case "error":
      return "bg-red-100 text-red-700";
    case "paused":
      return "bg-yellow-100 text-yellow-700";
    default:
      return "bg-blue-100 text-blue-700";
  }
}

function statusDotClass(status: string) {
  if (status === "running") return "bg-emerald-400 animate-pulse";
  if (status === "paused" || status === "partial") return "bg-amber-400";
  return "bg-blue-400";
}

function statusLabel(status: string) {
  if (status === "running") return "Running";
  if (status === "paused") return "Paused";
  if (status === "partial") return "Partial result";
  if (status === "completed") return "Completed";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
  if (status === "connecting") return "Starting…";
  if (status === "disconnected") return "Reconnecting…";
  if (status === "error") return "Error";
  return status;
}

// Browser session states arrive as wire enums (AUTHORIZING, ACTIVE,
// TAKEN_OVER). Every user-facing surface shows the human sentence
// instead of the raw enum.
function browserStatusLabel(status: string) {
  switch (status) {
    case "AUTHORIZING":
    case "authorizing":
      return "Connecting…";
    case "AUTHORIZED":
    case "authorized":
      return "Ready";
    case "ACTIVE":
    case "active":
      return "Live";
    case "RUNNING":
    case "running":
      return "Working…";
    case "TAKEN_OVER":
    case "takeover":
      return "You're in control";
    case "PAUSED_FOR_USER":
    case "paused":
      return "Paused";
    case "STOPPED":
      return "Stopped";
    default:
      return status;
  }
}

// ─── Structured error display ──────────────────────────────

// A task_error carries a machine code (rate_limited, AUTH_EXPIRED,
// tool_execution_failed, …). Every code maps to an actionable
// sentence; unknown codes fall back to the backend message plus the
// trace id to quote. No error path renders a bare "Something went
// wrong" any more.
function structuredErrorDisplay(input: {
  errorCode?: string | null;
  message?: string | null;
  toolName?: string | null;
}): { title: string; detail: string; isToolError: boolean } {
  const code = (input.errorCode || "").toLowerCase();
  const message = (input.message || "").trim();
  if (code.includes("rate_limit")) {
    return { title: "Model rate limit hit", detail: message || "Model rate limit hit. Retry in 30s.", isToolError: false };
  }
  if (code.includes("timeout")) {
    return { title: "Task timed out", detail: message || "The task exceeded its execution time limit. Retry with a narrower request.", isToolError: false };
  }
  if (code.includes("auth") && (code.includes("expired") || code.includes("missing") || code.includes("refresh") || code.includes("reconnect"))) {
    return { title: "Session expired", detail: "Session expired. Please re-authenticate.", isToolError: false };
  }
  if (code.includes("tool") && (code.includes("fail") || code.includes("error"))) {
    const tool = input.toolName || "tool";
    return {
      title: `Tool ${tool} failed`,
      detail: `Tool ${tool} failed: ${message || "unexpected error"}. Retry?`,
      isToolError: true,
    };
  }
  return { title: "Task failed", detail: message || "This task could not be completed.", isToolError: false };
}

// Tool errors name the failing tool in their message ("The
// 'shell_exec' step failed."). Extract it so the error card can
// say which tool crashed; the active plan step is the hint.
function errorToolName(message: string, planHint?: string | null): string | null {
  const hint = (planHint || "").trim();
  if (hint) return hint;
  const match = /'([A-Za-z0-9_]+)' step failed/.exec(message);
  return match ? match[1] : null;
}

function looksLikeAutomationIntent(prompt: string): boolean {
  return /^every\s+(?:day|daily|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d{1,2}:\d{2}\s*(?:,|-)/i.test(prompt);
}

// The backend mints thread ids as UUIDs. A new task's local placeholder
// must match that shape: it is persisted and used for follow-ups until
// the socket delivers the server-generated id on task_start.
function newThreadId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `thread-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const ChatInterface = forwardRef<ChatInterfaceHandle, ChatInterfaceProps>(function ChatInterface(
  { onTasksChange, onActiveTaskChange, activeTaskId, activeTaskTitle },
  ref
) {
  const { accessToken, refresh } = useAuth();
  const [task, setTask] = useState("");
  const [queuedMessage, setQueuedMessage] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<UploadedAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [currentStatus, setCurrentStatus] = useState<
    "idle" | "connecting" | "running" | "paused" | "completed" | "partial" | "cancelled" | "failed" | "disconnected" | "error"
  >("idle");

  const [userMessage, setUserMessage] = useState<string | null>(null);
  const userMessageRef = useRef<string | null>(null);
  const [conversationTurns, setConversationTurns] = useState<ConversationTurn[]>([]);
  const [plan, setPlan] = useState<PlanStep[] | null>(null);
  const [stepResults, setStepResults] = useState<Record<number, StepResult>>({});
  const [verification, setVerification] = useState<Verification | null>(null);
  const [finalAnswer, setFinalAnswer] = useState<string | null>(null);
  const [streamingAnswer, setStreamingAnswer] = useState("");
  const [responseMode, setResponseMode] = useState<"quick" | "web_research" | "autonomous" | null>(null);
  const [reportArtifact, setReportArtifact] = useState<ReportArtifact | null>(null);
  const [websiteArtifact, setWebsiteArtifact] = useState<ReportArtifact | null>(null);
  const [websitePreview, setWebsitePreview] = useState<{ preview_id: string; preview_url: string; project_name: string; expires_at?: number } | null>(null);
  const [previewViewport, setPreviewViewport] = useState<"desktop" | "mobile">("desktop");
  const [previewReloadKey, setPreviewReloadKey] = useState(0);
  const [costMetrics, setCostMetrics] = useState<{ total_cost_usd?: number } | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [errorId, setErrorId] = useState<string | null>(null);
  // The trace covering the current task, echoed by the backend on every event.
  const [traceId, setTraceId] = useState<string | null>(null);
  const [errorRetryable, setErrorRetryable] = useState(false);
  // Machine code + actionable hint from structured task_error payloads.
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [errorSuggestion, setErrorSuggestion] = useState<string | null>(null);
  const [copiedTraceId, setCopiedTraceId] = useState(false);
  const [userIntervention, setUserIntervention] = useState<{ reason: string; url: string; title: string; handoff_options: string[]; handoff_surface: "agent_computer" | "onsite48_bridge" } | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [selectedQuote, setSelectedQuote] = useState<string | null>(null);
  const [showPlanDetails, setShowPlanDetails] = useState(true);
  const [showConversationNavigator, setShowConversationNavigator] = useState(false);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [showJumpLatest, setShowJumpLatest] = useState(false);

  const [pendingConfirmation, setPendingConfirmation] = useState<{
    task_id: string;
    thread_id: string;
    tool_name: string;
    tool_args: Record<string, unknown>;
    step_description: string;
    step_index: number;
    task_scoped_read_only_available?: boolean;
  } | null>(null);

  // The parent owns the only sidebar task list. Before this proxy existed,
  // the chat kept an empty private list and overwrote the loaded history with
  // a lone running task whenever the first live task_start event arrived.
  const setRecentTasks = useCallback((update: React.SetStateAction<RecentTaskSummary[]>) => {
    onTasksChange?.(update);
  }, [onTasksChange]);
  const [showInspector, setShowInspector] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [showReportReader, setShowReportReader] = useState(false);
  const { session: browserSession, events: browserSessionEvents, create: createBrowserSession, control: controlBrowserSession, loadEvents: loadBrowserSessionEvents, setSession: setBrowserSession } = useBrowserSession(accessToken);

  const wsRef = useRef<WebSocket | null>(null);
  const pendingAutomationRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerTextareaRef = useRef<HTMLTextAreaElement>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handshakeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keepaliveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const queuedMessageTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queuedRetryRef = useRef(false);
  const queuedThreadIdRef = useRef<string | null>(null);
  const accessTokenRef = useRef<string | null>(accessToken);
  const refreshingSocketAuthRef = useRef(false);
  // WebSocket construction is asynchronous. Keep the socket in this ref as
  // soon as it is created so simultaneous effects/actions cannot replace a
  // CONNECTING socket with competing live connections.
  const socketSequenceRef = useRef(0);
  // Keep the latest connection routine without making the socket-owner effect
  // depend on callback identity. A render caused by task/sidebar state must
  // never tear down a healthy browser connection.
  const connectWebSocketRef = useRef<() => void>(() => {});
  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const turnRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const shouldAutoScrollRef = useRef(true);
  const threadIdRef = useRef<string | null>(null);
  const isPausedRef = useRef(false);
  // True only while the task is paused *waiting for a clarification answer*.
  //
  // A clarification is the one pause the user is meant to answer by typing, and
  // typing a message on any paused task resets to a brand-new task (see
  // `submitTask`). That is correct for an approval or browser-handoff pause, where
  // a new request genuinely supersedes the old one, and wrong here: the answer
  // "puzzle game, browser, single player" would be dispatched as a fresh task that
  // has never heard of "Build a game". This ref is what lets the answer resume the
  // paused thread instead, and it is cleared on every other pause.
  const clarificationPauseRef = useRef(false);
  const restoreSequenceRef = useRef(0);
  const acceptsTaskEventsRef = useRef(true);

  const hasStarted = currentStatus !== "idle";

  useEffect(() => {
    accessTokenRef.current = accessToken;
  }, [accessToken]);

  // ─── Reset Function ─────────────────────────────────────────────

  const reset = useCallback(() => {
    // Clear all state
    restoreSequenceRef.current += 1;
    acceptsTaskEventsRef.current = false;
    setTask("");
    setQueuedMessage(null);
    setAttachments([]);
    setCurrentStatus("idle");
    setUserMessage(null);
    userMessageRef.current = null;
    setConversationTurns([]);
    setPlan(null);
    setStepResults({});
    setVerification(null);
    setFinalAnswer(null);
    setStreamingAnswer("");
    setResponseMode(null);
    setReportArtifact(null);
    setWebsiteArtifact(null);
    setWebsitePreview(null);
    setCostMetrics(null);
    setErrorMessage(null);
    setErrorId(null);
    setTraceId(null);
    setErrorRetryable(false);
    setErrorCode(null);
    setErrorSuggestion(null);
    setCopiedTraceId(false);
    setSelectedQuote(null);
    setShowConversationNavigator(false);
    setActiveTurnId(null);
    setShowJumpLatest(false);
    setPendingConfirmation(null);
    setLoading(false);
    setShowInspector(false);
    setShowWorkspace(false);
    threadIdRef.current = null;
    // A new task must not be resurrected by the parent selection effect or by
    // the browser's previous-thread persistence on the next render.
    localStorage.removeItem(THREAD_ID_KEY);
    isPausedRef.current = false;
    // The thread is gone, so a pending clarification answer can no longer be
    // resumed against it. Leaving the flag set would make the next unrelated
    // message try to resume a thread that no longer exists.
    clarificationPauseRef.current = false;
    queuedThreadIdRef.current = null;
    onActiveTaskChange?.(null);
    
    // Close and reconnect WebSocket to clear any pending events
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (handshakeTimerRef.current) {
      clearTimeout(handshakeTimerRef.current);
      handshakeTimerRef.current = null;
    }
    if (keepaliveTimerRef.current) {
      clearInterval(keepaliveTimerRef.current);
      keepaliveTimerRef.current = null;
    }
    if (queuedMessageTimeoutRef.current) {
      clearTimeout(queuedMessageTimeoutRef.current);
      queuedMessageTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close(1000, "Starting a new task");
      wsRef.current = null;
    }
    // The mount-only socket owner intentionally does not rerun on state
    // updates. Reconnect explicitly after a user-initiated reset.
    window.setTimeout(() => connectWebSocketRef.current(), 0);

    console.log("ChatInterface reset - ready for new task");
  }, [onActiveTaskChange]);

  useImperativeHandle(ref, () => ({ reset }), [reset]);

  useEffect(() => {
    if (!browserSession) return;
    void loadBrowserSessionEvents();
    const timer = window.setInterval(() => void loadBrowserSessionEvents(), 2500);
    return () => window.clearInterval(timer);
  }, [browserSession?.id, loadBrowserSessionEvents]);

  // ─── Connection ────────────────────────────────────────────────

  const safeSocketSend = useCallback((payload: Record<string, unknown>, failureMessage = "The connection closed before your request could be sent. Please try again."): boolean => {
    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setIsConnected(false);
      setLoading(false);
      setCurrentStatus("disconnected");
      setErrorMessage(failureMessage);
      return false;
    }
    try {
      socket.send(JSON.stringify(payload));
      return true;
    } catch (error) {
      // readyState can change after the OPEN check. Never leave the composer in
      // "Starting…" when that race causes InvalidStateError. Closing this
      // socket routes cleanup through onclose, which schedules a clean retry.
      setIsConnected(false);
      setLoading(false);
      setCurrentStatus("disconnected");
      setErrorMessage(failureMessage);
      try {
        socket.close();
      } catch {
        if (wsRef.current === socket) wsRef.current = null;
      }
      return false;
    }
  }, []);

  const recoverSocketAuthentication = useCallback(async (): Promise<boolean> => {
    if (refreshingSocketAuthRef.current) return false;
    refreshingSocketAuthRef.current = true;
    try {
      const refreshed = await refresh();
      // AuthProvider persists the fresh access token synchronously before its
      // React state update; use it for the next socket attempt immediately.
      const refreshedToken = window.sessionStorage.getItem("onsite48_access_token");
      if (refreshed && refreshedToken) {
        accessTokenRef.current = refreshedToken;
        return true;
      }
      setQueuedMessage(null);
      setLoading(false);
      setCurrentStatus("failed");
      setErrorMessage("Your sign-in session expired before Onsite48 could send this task. Please sign in again and resubmit it.");
      return false;
    } finally {
      refreshingSocketAuthRef.current = false;
    }
  }, [refresh]);

  const connectWebSocket = useCallback(() => {
    // Don't reconnect if we're paused
    if (isPausedRef.current) {
      console.debug("Skipping WebSocket reconnect - task is paused");
      return;
    }
    // A 4401 close initiates an httpOnly-cookie token refresh. Creating another
    // socket while that refresh is in flight creates competing reconnect loops.
    if (refreshingSocketAuthRef.current) return;

    const existingSocket = wsRef.current;
    if (existingSocket && (
      existingSocket.readyState === WebSocket.CONNECTING
      || existingSocket.readyState === WebSocket.OPEN
      || existingSocket.readyState === WebSocket.CLOSING
    )) {
      return;
    }
    const token = accessTokenRef.current;
    if (!token) {
      setIsConnected(false);
      // A queued task has not been sent yet. Recover the short-lived access
      // token before creating any connection, instead of letting it time out.
      void recoverSocketAuthentication().then((recovered) => {
        if (recovered) reconnectTimerRef.current = setTimeout(connectWebSocket, 0);
      });
      return;
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socketSequence = socketSequenceRef.current + 1;
    socketSequenceRef.current = socketSequence;
    // Authentication is passed as a negotiated subprotocol instead of a URL
    // query string so short-lived JWTs are not written into reverse-proxy logs.
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws`, ["onsite48.v1", token]);
    wsRef.current = ws;
    // A proxy or network can leave a browser WebSocket in CONNECTING without a
    // close event. The timer belongs to this socket only; later sockets cannot
    // clear it or inherit its timeout.
    const handshakeTimer = setTimeout(() => {
      if (wsRef.current === ws && ws.readyState === WebSocket.CONNECTING) {
        console.warn("Onsite48 WebSocket handshake timed out", { socketSequence });
        ws.close(4000, "Handshake timeout");
      }
    }, 10_000);
    handshakeTimerRef.current = handshakeTimer;

    ws.onopen = () => {
      clearTimeout(handshakeTimer);
      if (handshakeTimerRef.current === handshakeTimer) handshakeTimerRef.current = null;
      setIsConnected(true);
      setCurrentStatus((previous) => (previous === "disconnected" && !queuedMessage ? "idle" : previous));
      reconnectAttemptRef.current = 0;
      if (keepaliveTimerRef.current) clearInterval(keepaliveTimerRef.current);
      // Keep reverse proxies and idle network devices from silently expiring an
      // otherwise healthy task connection. The server replies with a pong.
      // 10s (not 25s) so a dropped connection is detected quickly: a
      // network blip reconnects in ~1-2s instead of idling for half a
      // minute before the first retry.
      keepaliveTimerRef.current = setInterval(() => {
        safeSocketSend({ action: "ping" }, "The agent connection was interrupted while keeping it active.");
      }, 10_000);
      console.log("WebSocket connected");
    };

    ws.onmessage = (event) => {
      let data: StreamedEvent;
      const normalized = normalizeStreamEvent(event.data);
      if (!normalized.ok) {
        // Two very different problems land here, so they are reported
        // differently. An unknown type is a version mismatch and is ignored;
        // anything else is a payload this build cannot trust and is surfaced,
        // because silently dropping it looks like the agent went quiet.
        if (normalized.reason.startsWith("unknown event type")) {
          console.debug("Onsite48 ignored an unrecognised event", normalized.reason);
          return;
        }
        console.error("Onsite48 received an invalid WebSocket event", normalized.reason);
        setErrorMessage("The agent connection returned an invalid response. Reconnect and retry the task.");
        setErrorRetryable(true);
        setLoading(false);
        return;
      }
      data = normalized.event;
      // Every event carries the trace for the task it belongs to. Captured here,
      // before the type switch, so a failure that arrives on an event type this
      // component does not handle still leaves the user with something to quote.
      if (data.trace_id) setTraceId(String(data.trace_id));
      if (data.type === "browser_session_state" && data.session_id) {
        setBrowserSession((previous) => previous && previous.id === data.session_id ? {
          ...previous,
          status: (data.status as BrowserSessionStatus) || previous.status,
          current_url: data.current_url ?? previous.current_url,
          current_observation: data.current_observation || previous.current_observation,
          tab_ids: Array.isArray(data.tab_ids) ? data.tab_ids : previous.tab_ids,
          primary_tab_id: data.primary_tab_id ?? previous.primary_tab_id,
        } : previous);
        return;
      }

      // A single authenticated socket can still receive a late terminal event
      // from a task that was replaced locally. Once a new task has started,
      // never let the old thread pause, fail, or overwrite its workspace.
      if (
        (data.type !== "task_start" && !acceptsTaskEventsRef.current)
        || (data.type !== "task_start"
        && data.thread_id
        && threadIdRef.current
        && data.thread_id !== threadIdRef.current)
      ) {
        console.debug("Ignoring stale task event", { incoming: data.thread_id, active: threadIdRef.current, type: data.type });
        return;
      }

      // ─── IGNORE EVENTS WHEN PAUSED ─────────────────────────────
      if (isPausedRef.current) {
        // Only process pause/resume/error events
        if (data.type === "task_paused" || data.type === "task_resumed" || data.type === "error") {
          // Let these through
        } else {
          console.debug("Ignoring event while paused:", data.type);
          return;
        }
      }

      switch (data.type) {
        case "task_start": {
          acceptsTaskEventsRef.current = true;
          isPausedRef.current = false;
          setWebsitePreview(null);
          setWebsiteArtifact(null);
          setUserIntervention(null);
          setShowWorkspace(false);
          setShowInspector(false);
          setPendingConfirmation(null);
          setCurrentStatus("running");
          const startMode = narrowResponseMode(data.response_mode);
          if (startMode) setResponseMode(startMode);
          setStreamingAnswer("");
          if (data.thread_id) {
            threadIdRef.current = data.thread_id;
            localStorage.setItem(THREAD_ID_KEY, data.thread_id);
            onActiveTaskChange?.(data.thread_id);
            setRecentTasks((prev) => {
              const existing = prev.find((task) => task.id === data.thread_id);
              const current = {
                id: data.thread_id as string,
                title: existing?.title || data.message || "Untitled task",
                status: "running" as const,
                domain: existing?.domain || data.domain || "general",
                updatedAt: data.timestamp,
                turnCount: (existing?.turnCount || 0) + (data.is_follow_up ? 1 : existing ? 0 : 1),
              };
              const next = [current, ...prev.filter((task) => task.id !== data.thread_id)].slice(0, 50);
              return next;
            });
          }
          break;
        }
        case "fast_mode": {
          const fastMode = narrowResponseMode(data.mode);
          if (fastMode) setResponseMode(fastMode);
          break;
        }
        case "answer_delta": {
          const delta = typeof data.content === "string" ? data.content : "";
          if (delta) setStreamingAnswer((previous) => appendBounded(previous, delta));
          break;
        }
        case "planner_output": {
          // Validated here rather than cast: a plan is rendered as a list of
          // steps, so a payload that is not an array of step-shaped records
          // would render `undefined` into the workspace.
          if (Array.isArray(data.content)) {
            const steps = data.content.filter(
              (step): step is PlanStep =>
                Boolean(step) && typeof step === "object" && typeof (step as PlanStep).description === "string"
            );
            setPlan(steps.length ? steps : null);
          } else {
            setPlan(null);
          }
          break;
        }
        case "executor_output": {
          // `content` is rendered verbatim as tool output, so it is validated
          // to a record with a numeric index and text fields before it reaches
          // state. Anything else is dropped instead of stored.
          const result = data.content;
          if (result && typeof result === "object" && !Array.isArray(result) && typeof (result as StepResult).step_index === "number") {
            const raw = result as Record<string, unknown>;
            const stepResult: StepResult = {
              step: typeof raw.step === "string" ? raw.step : "",
              tool: typeof raw.tool === "string" ? raw.tool : null,
              output: typeof raw.output === "string" ? raw.output : JSON.stringify(raw.output ?? null),
              step_index: raw.step_index as number,
            };
            setStepResults((prev) => ({ ...prev, [stepResult.step_index]: stepResult }));
          }
          break;
        }
        case "executor_output_delta": {
          // Streaming tool output: appended to the step's output as it
          // grows, so the plan accordion shows progress while a step runs.
          const delta = data.content;
          if (delta && typeof delta === "object" && !Array.isArray(delta) && typeof (delta as StepResult).step_index === "number") {
            const raw = delta as Record<string, unknown>;
            const index = raw.step_index as number;
            const outputDelta = typeof raw.output === "string" ? raw.output : "";
            setStepResults((prev) => {
              const existing = prev[index];
              return {
                ...prev,
                [index]: {
                  step: typeof raw.step === "string" ? raw.step : (existing?.step ?? ""),
                  tool: typeof raw.tool === "string" ? raw.tool : (existing?.tool ?? null),
                  output: `${existing?.output ?? ""}${outputDelta}`,
                  step_index: index,
                },
              };
            });
          }
          break;
        }
        case "cost_update": {
          // Running cost total, emitted on every model call. The header
          // cost pill updates live so the spend is visible during a task.
          const metrics = narrowCostMetrics(data.cost_metrics);
          if (metrics) setCostMetrics(metrics);
          break;
        }
        case "verifier_output": {
          if (data.content && typeof data.content === "object" && !Array.isArray(data.content)) {
            setVerification(data.content as Verification);
          } else {
            setVerification(null);
          }
          break;
        }
        case "website_preview": {
          if (
            data.preview_id
            && typeof data.preview_url === "string"
            && data.preview_url.startsWith("/api/web-previews/")
          ) {
            setWebsitePreview({
              preview_id: data.preview_id,
              preview_url: data.preview_url,
              project_name: data.project_name || "Website preview",
              expires_at: data.expires_at,
            });
            setPreviewViewport("desktop");
          }
          break;
        }
        case "artifact_created": {
          const artifact = narrowReportArtifacts(data.artifact)?.[0];
          if (artifact?.artifact_type === "generated_website_project") {
            setWebsiteArtifact(artifact);
          } else if (artifact) {
            setReportArtifact(artifact);
          }
          break;
        }
        case "needs_confirmation": {
          setPendingConfirmation({
            task_id: data.task_id || "",
            thread_id: data.thread_id || threadIdRef.current || "",
            tool_name: data.tool_name || "",
            tool_args: data.tool_args || {},
            step_description: data.step_description || "",
            step_index: data.step_index ?? 0,
            task_scoped_read_only_available: Boolean(data.task_scoped_read_only_available),
          });
          setLoading(false);
          if (data.thread_id) {
            setRecentTasks((previous) => {
              const next = previous.map((item) => item.id === data.thread_id ? { ...item, status: "waiting_for_approval" as const, updatedAt: data.timestamp } : item);
              return next;
            });
          }
          break;
        }
        case "needs_user_intervention": {
          const handoffSurface = data.handoff_surface === "agent_computer" ? "agent_computer" : "onsite48_bridge";
          const interventionReason = data.reason || "The target site requires a human verification step.";
          const interventionOptions = Array.isArray(data.handoff_options) ? data.handoff_options : [];
          setUserIntervention({
            reason: interventionReason,
            url: data.url || "",
            title: data.title || "Target site",
            handoff_options: interventionOptions,
            handoff_surface: handoffSurface,
          });
          // A Bridge pause is a deliberate, resumable checkpoint—not an empty
          // answer. Put its next action in the conversation immediately so the
          // user sees why Onsite48 stopped even before opening the handoff panel.
          if (handoffSurface === "onsite48_bridge") {
            setFinalAnswer(
              `Onsite48 is ready to continue with your selected local-browser tab, but it needs your attention first. ${interventionReason}\n\n` +
              "Focus the intended page, open Onsite48 Bridge, choose Sync this tab, approve the site permission if Chrome asks, then return here and press Resume. Onsite48 will not substitute another tab."
            );
            setResponseMode("autonomous");
          }
          setCurrentStatus("paused");
          setLoading(false);
          setPendingConfirmation(null);
          // A local Onsite48 Bridge challenge belongs in the user's own browser.
          // An Agent Computer challenge preserves that isolated context and
          // opens the manual takeover workspace instead.
          setShowWorkspace(handoffSurface === "agent_computer");
          setShowInspector(false);
          isPausedRef.current = true;
          // A browser-handoff pause is answered in the browser, not by typing,
          // so a later message is a new request rather than an answer to this one.
          clarificationPauseRef.current = false;
          if (data.thread_id) {
            setRecentTasks((previous) => previous.map((item) => item.id === data.thread_id ? { ...item, status: "paused" as const, updatedAt: data.timestamp } : item));
          }
          break;
        }
        case "needs_clarification": {
          const question = data.question || "Please clarify the request before Onsite48 continues.";
          setFinalAnswer(question);
          setCurrentStatus("paused");
          setLoading(false);
          setPendingConfirmation(null);
          setUserIntervention(null);
          isPausedRef.current = true;
          // The next message is an answer to `question`, so it must resume this
          // thread rather than start a new one. Guarded on a thread id: without
          // one there is nothing to resume and the flag would make the next
          // message send a resume for a thread the backend cannot find.
          clarificationPauseRef.current = Boolean(data.thread_id || threadIdRef.current);
          if (data.thread_id) {
            setRecentTasks((previous) => previous.map((item) => item.id === data.thread_id ? { ...item, status: "paused" as const, updatedAt: data.timestamp } : item));
          }
          break;
        }
        case "task_paused": {
          // Task was paused - update UI
          setCurrentStatus("paused");
          setLoading(false);
          setPendingConfirmation(null);
          // Update state if available
if (data.state) {
          const restoredPlan = narrowPlanSteps(data.state.plan);
          if (restoredPlan) setPlan(restoredPlan);
          const restoredResults = narrowStepResults(data.state.results);
          if (restoredResults) {
            setStepResults(restoredResults);
          }
          }
          if (data.thread_id) {
            setRecentTasks((previous) => {
              const next = previous.map((item) => item.id === data.thread_id ? { ...item, status: "paused" as const, updatedAt: data.timestamp } : item);
              return next;
            });
          }
          console.log("Task paused:", data.thread_id);
          break;
        }
        case "task_resumed": {
          // Task was resumed
          isPausedRef.current = false;
          setCurrentStatus("running");
          setLoading(true);
          setPendingConfirmation(null);
          setUserIntervention(null);
          setFinalAnswer(null);
          if (data.thread_id) {
            setRecentTasks((previous) => {
              const next = previous.map((item) => item.id === data.thread_id ? { ...item, status: "running" as const, updatedAt: data.timestamp } : item);
              return next;
            });
          }
          console.log("Task resumed:", data.thread_id);
          break;
        }
        case "task_complete": {
          setCurrentStatus((narrowTaskStatus(data.status) ?? "completed") as CurrentStatus);
          setShowPlanDetails(false);
          if (data.thread_id) {
            setRecentTasks((prev) => {
              const next = prev.map((task) => task.id === data.thread_id ? {
                ...task,
                status: ((data.status as RecentTaskSummary["status"]) || "completed"),
                updatedAt: data.timestamp,
              } : task);
              return next;
            });
          }
          const completedAnswer = typeof data.final_answer === "string" ? data.final_answer : "";
          // Keep the active task's answer in the single live answer surface below.
          // Archiving it into conversationTurns here as well caused the same
          // response to render twice: once in the archived transcript and once
          // in the current Answer card. Earlier turns are restored/archived only
          // when a later follow-up or task restoration needs them.
          setFinalAnswer(completedAnswer || null);
          setStreamingAnswer("");
          const completedMode = narrowResponseMode(data.response_mode);
          if (completedMode) setResponseMode(completedMode);
          const completedArtifacts = narrowReportArtifacts(data.artifacts) ?? narrowReportArtifacts(data.artifact) ?? [];
          setReportArtifact(completedArtifacts.find((artifact) => artifact.artifact_type === "generated_markdown_report" || artifact.content_type === "text/markdown") ?? null);
          setWebsiteArtifact(completedArtifacts.find((artifact) => artifact.artifact_type === "generated_website_project") ?? null);
          setCostMetrics(narrowCostMetrics(data.cost_metrics));
          const completedPlan = narrowPlanSteps(data.plan);
          if (completedPlan) setPlan(completedPlan);
          if (data.verification) setVerification(data.verification as unknown as Verification);
          setLoading(false);
          setPendingConfirmation(null);
          isPausedRef.current = false;
          break;
        }
        case "task_error": {
          setCurrentStatus("failed");
          if (data.thread_id) {
            setRecentTasks((prev) => {
              const next = prev.map((task) => task.id === data.thread_id ? { ...task, status: "failed" as const, updatedAt: data.timestamp } : task);
              return next;
            });
          }
          setErrorMessage(data.message || data.error || "This task failed before it could be completed.");
          setErrorId(data.error_id || null);
          setErrorCode(data.error_code || null);
          setErrorSuggestion(data.suggestion || null);
          setErrorRetryable(Boolean(data.retryable));
          setStreamingAnswer("");
          setLoading(false);
          setPendingConfirmation(null);
          isPausedRef.current = false;
          break;
        }
        case "error": {
          setCurrentStatus("failed");
          setErrorMessage(data.message || data.error || "The agent connection reported an error. Retry the task.");
          setErrorId(data.error_id || null);
          setErrorCode(data.error_code || null);
          setErrorSuggestion(data.suggestion || null);
          setErrorRetryable(Boolean(data.retryable));
          setStreamingAnswer("");
          setLoading(false);
          setPendingConfirmation(null);
          isPausedRef.current = false;
          break;
        }
        default:
          break;
      }
    };

    ws.onclose = async (closeEvent) => {
      // Ignore a stale socket that was superseded by a newer connection.
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      clearTimeout(handshakeTimer);
      if (handshakeTimerRef.current === handshakeTimer) handshakeTimerRef.current = null;
      console.warn("Onsite48 WebSocket closed", {
        socketSequence,
        code: closeEvent.code,
        reason: closeEvent.reason || "no close reason supplied",
        wasClean: closeEvent.wasClean,
      });
      if (keepaliveTimerRef.current) {
        clearInterval(keepaliveTimerRef.current);
        keepaliveTimerRef.current = null;
      }
      setIsConnected(false);
      if (isPausedRef.current) return;

      // Access tokens are deliberately short-lived. Refresh once through the
      // httpOnly session cookie before retrying a 4401-authenticated socket.
      if (closeEvent.code === 4401 && !refreshingSocketAuthRef.current) {
        const recovered = await recoverSocketAuthentication();
        if (recovered) reconnectTimerRef.current = setTimeout(connectWebSocket, 0);
        return;
      }

      setCurrentStatus((previous) => (previous === "running" || previous === "connecting" ? "disconnected" : previous));
      const delay = Math.min(1000 * 2 ** reconnectAttemptRef.current, 30000);
      reconnectAttemptRef.current = Math.min(reconnectAttemptRef.current + 1, 5);
      reconnectTimerRef.current = setTimeout(connectWebSocket, delay);
    };

    ws.onerror = () => {
      // onclose owns all retry decisions; this handler only records context.
      console.warn("Onsite48 WebSocket transport error", { socketSequence });
      setIsConnected(false);
    };
  }, [onActiveTaskChange, onTasksChange, recoverSocketAuthentication, safeSocketSend]);

  // Keep the latest callback available to the long-lived connection owner.
  // This effect deliberately does not own a socket or perform cleanup.
  useEffect(() => {
    connectWebSocketRef.current = connectWebSocket;
  }, [connectWebSocket]);

  // A chat workspace owns one socket for its entire mounted lifetime. The
  // previous effect depended on a callback that could be recreated by React
  // state/prop updates; its cleanup then called close() on a healthy socket.
  // That produced browser-originated 1001/1005 disconnects before queued
  // requests could reach the backend.
  useEffect(() => {
    connectWebSocketRef.current();
    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (handshakeTimerRef.current) {
        clearTimeout(handshakeTimerRef.current);
        handshakeTimerRef.current = null;
      }
      if (keepaliveTimerRef.current) {
        clearInterval(keepaliveTimerRef.current);
        keepaliveTimerRef.current = null;
      }
      if (queuedMessageTimeoutRef.current) {
        clearTimeout(queuedMessageTimeoutRef.current);
        queuedMessageTimeoutRef.current = null;
      }
      const socket = wsRef.current;
      wsRef.current = null;
      if (socket && socket.readyState < WebSocket.CLOSING) {
        socket.close(1000, "Chat workspace unmounted");
      }
    };
  }, []);

  const trackTranscriptScroll = useCallback(() => {
    const transcript = transcriptRef.current;
    if (!transcript) return;
    const atLatest = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 96;
    shouldAutoScrollRef.current = atLatest;
    setShowJumpLatest(!atLatest && (loading || currentStatus === "running"));
  }, [currentStatus, loading]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript || !shouldAutoScrollRef.current) return;
    // Do not use scrollIntoView: it may choose an outer page ancestor and
    // make long completed answers feel impossible to read from the top.
    transcript.scrollTop = transcript.scrollHeight;
  }, [plan, stepResults, finalAnswer, streamingAnswer, conversationTurns, pendingConfirmation, errorMessage, currentStatus]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript || conversationTurns.length < 2) return;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible?.target instanceof HTMLElement) setActiveTurnId(visible.target.dataset.turnId || null);
    }, { root: transcript, threshold: [0.2, 0.6] });
    conversationTurns.forEach((turn) => {
      const node = turnRefs.current[turn.id];
      if (node) observer.observe(node);
    });
    return () => observer.disconnect();
  }, [conversationTurns]);

  const jumpToLatest = useCallback(() => {
    const transcript = transcriptRef.current;
    if (!transcript) return;
    shouldAutoScrollRef.current = true;
    transcript.scrollTo({ top: transcript.scrollHeight, behavior: "smooth" });
    setShowJumpLatest(false);
  }, []);

  // ─── Persisted task restoration ──────────────────────────────────

  const restoreTask = useCallback(async (threadId: string) => {
    if (!accessToken || threadIdRef.current === threadId) return;
    const restoreSequence = restoreSequenceRef.current + 1;
    restoreSequenceRef.current = restoreSequence;
    // A selected task owns its own UI state. Never let a paused/captcha state
    // from a different thread suppress socket events or keep its Computer panel
    // over the new transcript.
    isPausedRef.current = false;
    setUserIntervention(null);
    setPendingConfirmation(null);
    setShowWorkspace(false);
    setShowInspector(false);
    setLoading(true);
    setErrorMessage(null);
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(threadId)}`, {
        headers: authHeaders(accessToken),
        credentials: "include",
      });
      if (!response.ok) throw new Error("This task could not be restored.");
      const detail = await response.json() as PersistedTaskDetail;
      if (restoreSequence !== restoreSequenceRef.current) return;
      const conversation = detail.execution_metadata?.conversation || [];
      // Rebuild every durable user/assistant pair. The previous version popped
      // the newest completed turn out of this array to render it separately,
      // which made the latest answer disappear whenever a later turn arrived
      // or a task was restored from PostgreSQL.
      const completedTurns: ConversationTurn[] = [];
      let pendingTurn: ConversationTurn | null = null;
      let turnIndex = 0;
      for (const entry of conversation) {
        const content = typeof entry.content === "string" ? entry.content : "";
        if (!content) continue;
        if (entry.role === "user") {
          if (pendingTurn) completedTurns.push(pendingTurn);
          pendingTurn = { id: entry.turn_id || `${threadId}-legacy-${turnIndex}`, index: turnIndex, user: content, createdAt: entry.at, title: content.split(/[.!?]/)[0].slice(0, 72) };
          turnIndex += 1;
        } else if (entry.role === "assistant" && pendingTurn) {
          pendingTurn.answer = content;
          completedTurns.push(pendingTurn);
          pendingTurn = null;
        }
      }
      // Live turns reconstructed from the persisted event log.
      // Conversation metadata is authoritative; event-derived turns
      // fill the gaps — a turn that streamed live but was not yet
      // archived into conversation metadata — so a restored task
      // never loses its answers.
      const eventTurns = conversationTurnsFromEvents(detail.events || []);
      const mergedTurns: ConversationTurn[] = [...completedTurns];
      const seenPrompts = new Set(completedTurns.map((turn) => turn.user.trim().toLowerCase()));
      for (const turn of eventTurns) {
        const promptKey = turn.user.trim().toLowerCase();
        if (promptKey && !seenPrompts.has(promptKey)) {
          mergedTurns.push(turn);
          seenPrompts.add(promptKey);
        }
      }
      // A trailing turn without an answer was still running when the
      // task was interrupted. Treat it as the pending turn, exactly
      // like the conversation-metadata path above.
      if (!pendingTurn) {
        const lastMerged = mergedTurns[mergedTurns.length - 1];
        if (lastMerged && !lastMerged.answer) {
          pendingTurn = lastMerged;
          mergedTurns.pop();
        }
      }
      const plannerEvent = [...(detail.events || [])].reverse().find((event) => event.type === "planner_output");
      const resultEvents = (detail.events || []).filter((event) => event.type === "executor_output");
      const previewEvent = [...(detail.events || [])].reverse().find((event) => event.type === "website_preview");
      const results: Record<number, StepResult> = {};
      resultEvents.forEach((event) => {
        const result = event.payload?.content as StepResult | undefined;
        if (result && typeof result.step_index === "number") results[result.step_index] = result;
      });
      const failureEvent = [...(detail.events || [])].reverse().find((event) => event.type === "task_error" || event.type === "error");
      const completionEvents = (detail.events || []).filter((event) => event.type === "task_complete");
      const completionArtifacts = completionEvents.map((event) => {
        const payload = event.payload;
        if (payload?.artifact) return payload.artifact;
        return payload?.artifacts?.[0] || null;
      });
      // Persisted task detail is a REST response, so it is validated on the
      // same boundary as the socket: an artifact without an attachment id is
      // not renderable and must not become a dead download button.
      const completionArtifactRecords = completionArtifacts
        .map((artifact) => narrowReportArtifacts(artifact)?.[0] ?? null)
        .filter((artifact): artifact is ReportArtifact => artifact !== null);
      mergedTurns.forEach((turn, index) => {
        const artifact = completionArtifactRecords[index];
        if (artifact) turn.artifact = artifact;
      });
      const persistedArtifacts = narrowReportArtifacts(detail.artifacts) ?? [];
      const persistedReportArtifacts = persistedArtifacts.filter((artifact) => artifact.artifact_type === "generated_markdown_report" || artifact.content_type === "text/markdown");
      const persistedWebsiteArtifact = persistedArtifacts.find((artifact) => artifact.artifact_type === "generated_website_project") || null;
      const latestArtifact = completionArtifactRecords[completionArtifactRecords.length - 1] || persistedReportArtifacts[persistedReportArtifacts.length - 1] || null;

      threadIdRef.current = detail.thread_id;
      acceptsTaskEventsRef.current = true;
      localStorage.setItem(THREAD_ID_KEY, detail.thread_id);
      onActiveTaskChange?.(detail.thread_id);
      setConversationTurns(mergedTurns);
      // A lone final user turn means the task was interrupted or is still
      // running. Completed answers are already rendered above with their
      // exact user prompt and are never removed from the transcript.
      setUserMessage(pendingTurn?.user || null);
      setFinalAnswer(null);
       setReportArtifact(latestArtifact);
      setWebsiteArtifact(persistedWebsiteArtifact);
      const restoredPreview = previewEvent?.payload;
      setWebsitePreview(
        restoredPreview?.preview_id
        && typeof restoredPreview.preview_url === "string"
        && restoredPreview.preview_url.startsWith("/api/web-previews/")
          ? {
              preview_id: restoredPreview.preview_id,
              preview_url: restoredPreview.preview_url,
              project_name: restoredPreview.project_name || "Website preview",
              expires_at: restoredPreview.expires_at,
            }
          : null,
      );
      setPlan(Array.isArray(plannerEvent?.payload?.content) ? plannerEvent.payload.content : null);
      setShowPlanDetails(detail.status !== "completed");
      setStepResults(results);
      setCostMetrics(narrowCostMetrics(detail.cost_metrics));
      const restoredStatus = detail.status === "queued"
        ? "connecting"
        : detail.status === "waiting_for_approval"
          ? "paused"
          : (detail.status || "completed");
      setCurrentStatus(restoredStatus);
      isPausedRef.current = detail.status === "paused" || detail.status === "waiting_for_approval";
      setLoading(["queued", "running"].includes(detail.status || ""));
      setErrorMessage(failureEvent?.payload?.message || failureEvent?.payload?.error || null);
    } catch (error) {
      if (restoreSequence !== restoreSequenceRef.current) return;
      setCurrentStatus("failed");
      setLoading(false);
      setErrorMessage(error instanceof Error ? error.message : "This task could not be restored.");
    }
  }, [accessToken, onActiveTaskChange]);

  useEffect(() => {
    if (activeTaskId && activeTaskId !== threadIdRef.current) void restoreTask(activeTaskId);
  }, [activeTaskId, restoreTask]);

  // ─── Actions ───────────────────────────────────────────────────

  const captureSelectedQuote = useCallback(() => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (!transcriptRef.current?.contains(range.commonAncestorContainer)) return;
    const quote = selection.toString().replace(/\s+/g, " ").trim();
    if (quote) setSelectedQuote(quote.slice(0, 4_000));
  }, []);

  const insertSelectedQuote = useCallback(() => {
    if (!selectedQuote) return;
    const blockQuote = selectedQuote.split("\n").map((line) => `> ${line}`).join("\n");
    setTask((previous) => `${previous.trimEnd()}${previous.trim() ? "\n\n" : ""}Quoted context:\n${blockQuote}\n\n`);
    setSelectedQuote(null);
    window.getSelection()?.removeAllRanges();
    requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }, [selectedQuote]);

  const uploadFiles = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    setUploading(true);
    setErrorMessage(null);
    try {
      const uploaded = await Promise.all(files.map(async (file) => {
        const form = new FormData();
        form.append("file", file);
        const response = await fetch("/api/files/upload", {
          method: "POST",
          credentials: "include",
          headers: authHeaders(accessToken),
          body: form,
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail || `Could not upload ${file.name}.`);
        }
        return response.json() as Promise<UploadedAttachment>;
      }));
      setAttachments((previous) => [...previous, ...uploaded].slice(0, 5));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "File upload failed.");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }, [accessToken]);

  const removeAttachment = useCallback((attachmentId: string) => {
    setAttachments((previous) => previous.filter((attachment) => attachment.id !== attachmentId));
  }, []);

  const submitTask = useCallback(
    async (overrideMessage?: string) => {
      const trimmedMessage = (overrideMessage ?? task).trim();
      if (!trimmedMessage) return;
      restoreSequenceRef.current += 1;
      acceptsTaskEventsRef.current = false;

      // Automation configuration is a separate durable API contract from
      // task execution. Preview first; only an explicit "confirm" persists it.
      const confirmingAutomation = Boolean(pendingAutomationRef.current && /^(?:confirm|yes|create it|do it)\s*[.!?]?$/i.test(trimmedMessage));
      if (looksLikeAutomationIntent(trimmedMessage) || confirmingAutomation) {
        const automationMessage = pendingAutomationRef.current || trimmedMessage;
        setLoading(true);
        setCurrentStatus("connecting");
        setUserMessage(trimmedMessage);
        setTask("");
        try {
          const response = await fetch("/api/proactive/scheduled-tasks/from-chat", {
            method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...authHeaders(accessToken) },
            body: JSON.stringify({ message: automationMessage, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", confirm: confirmingAutomation }),
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(body.detail || "Could not interpret that automation request.");
          if (body.requires_confirmation) {
            pendingAutomationRef.current = automationMessage;
            const intent = body.intent || {};
            setFinalAnswer(`I can create **${intent.title}** for ${intent.schedule_type} runs at ${intent.schedule_policy?.local_time || "the requested time"} in ${intent.timezone}. The result will appear in this chat workspace. Reply **confirm** to create it.`);
          } else {
            pendingAutomationRef.current = null;
            setFinalAnswer(`Automation created: **${body.schedule?.title || automationMessage}**. It will run according to the confirmed schedule and deliver results in this workspace.`);
          }
          setCurrentStatus("completed");
        } catch (error) {
          setCurrentStatus("failed");
          setErrorMessage(error instanceof Error ? error.message : "Automation configuration failed.");
        } finally {
          setLoading(false);
        }
        return;
      }
      
      // A message typed while the task is paused for clarification is the
      // *answer* to the question the agent asked, so it resumes that thread
      // instead of replacing it. Every other pause still resets: an approval or
      // browser-handoff pause has nothing to answer with a message, and a new
      // request genuinely supersedes the old one.
      //
      // Read before `reset()`, which nulls `threadIdRef`. The flag is only
      // cleared on the send path below, so an answer queued while offline
      // (which returns early and re-enters here) is still recognised.
      const isClarificationAnswer =
        clarificationPauseRef.current && Boolean(threadIdRef.current);

      if (isPausedRef.current || currentStatus === "paused") {
        if (!isClarificationAnswer) {
          reset();
        }
      }
      const isFollowUp = Boolean(
        threadIdRef.current &&
        ["completed", "failed", "cancelled"].includes(currentStatus)
      );
      const isRetryRequest = Boolean(
        isFollowUp &&
        currentStatus === "failed" &&
        /^(?:please\s+)?(?:retry|try\s+(?:that|it)\s+again|run\s+(?:that|it)\s+again|redo\s+(?:that|it))\s*[.!?]?$/i.test(trimmedMessage)
      );

      if (isFollowUp && userMessage) {
        setConversationTurns((previous) => {
          const previousTurn = previous[previous.length - 1];
          if (previousTurn?.user === userMessage && previousTurn.answer === finalAnswer) return previous;
          return [
            ...previous,
            {
              id: `${threadIdRef.current || "thread"}-live-${Date.now()}`,
              index: previous.length,
              user: userMessage,
              answer: finalAnswer,
              artifact: reportArtifact,
              createdAt: new Date().toISOString(),
              title: userMessage.split(/[.!?]/)[0].slice(0, 72),
            },
          ];
        });
      }

      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        queuedRetryRef.current = isRetryRequest;
        // Preserve the thread_id for this queued message so it resumes the same
        // task when the socket reconnects, instead of minting a new one.
        if (threadIdRef.current) {
          queuedThreadIdRef.current = threadIdRef.current;
        }
        setQueuedMessage(trimmedMessage);
        setTask("");
        setLoading(true);
        setCurrentStatus("connecting");
        connectWebSocket();
        return;
      }

      // A clarification answer keeps the paused thread. `isFollowUp` cannot be
      // used for this: it requires a settled status ("completed"/"failed"/
      // "cancelled"), and a clarified task is PAUSED, so it would mint a new id.
      //
      // If a message was queued while offline, queuedThreadIdRef holds the
      // original thread_id so the retry resumes the same task instead of
      // creating a new one.
      const isQueuedFlush = Boolean(queuedThreadIdRef.current);
      const threadId = isFollowUp || isClarificationAnswer || isQueuedFlush
        ? (isQueuedFlush ? queuedThreadIdRef.current : threadIdRef.current) as string
        : newThreadId();
      threadIdRef.current = threadId;
      localStorage.setItem(THREAD_ID_KEY, threadId);
      isPausedRef.current = false;
      // Consumed by this send. Reached only once the socket is confirmed open,
      // so an answer queued while offline keeps its flag and is recognised when
      // the flush re-enters `submitTask`.
      clarificationPauseRef.current = false;
      // Clear the queued thread id after consuming it
      if (isQueuedFlush) {
        queuedThreadIdRef.current = null;
      }

      // Completed turns are archived as soon as their task_complete event
      // arrives. A follow-up therefore never has to move an earlier response
      // out of a separate live slot, which preserves strict chronology. A
      // clarification answer must NOT clear the transcript: the question the
      // user just answered is the turn immediately above their reply, and
      // clearing it would make the answer look like an unrelated first message.
      if (!isFollowUp && !isClarificationAnswer) {
        setConversationTurns([]);
      }

      setLoading(true);
      setCurrentStatus("connecting");
      userMessageRef.current = trimmedMessage;
      setUserMessage(trimmedMessage);
      setPlan(null);
      setShowPlanDetails(true);
      setStepResults({});
      setVerification(null);
    setFinalAnswer(null);
    setStreamingAnswer("");
    setResponseMode(null);
    setReportArtifact(null);
    setWebsiteArtifact(null);
    setWebsitePreview(null);
    setCostMetrics(null);
      setErrorMessage(null);
      setPendingConfirmation(null);
      setShowInspector(false);

      if (isRetryRequest) {
        safeSocketSend({
          action: "retry_task",
          thread_id: threadId,
        }, "The connection closed before the retry was sent. Reconnect and try again.");
        setTask("");
        return;
      }

      let taskBrowserSession = browserSession;
      if (looksLikeBrowserTask(trimmedMessage) && !taskBrowserSession) {
        try {
          // A new task has no TaskThread row yet, and the backend only
          // accepts a task_id that already exists. The session is created
          // unlinked and bound to its task by browser_session_id in the
          // run_task frame below.
          const created = await createBrowserSession([], [
            "bridge_read_active_tab", "bridge_inspect_active_tab", "bridge_scroll_active_tab", "bridge_observe_surface",
            "bridge_snapshot_active_tab", "bridge_capture_active_tab", "bridge_navigate_active_tab", "bridge_click_active_tab",
            "bridge_fill_active_tab", "bridge_list_tabs", "bridge_open_tab", "bridge_activate_tab", "bridge_open_task_tabs",
            "bridge_close_task_tabs", "bridge_control_media", "bridge_download_active_tab", "bridge_upload_active_tab",
          ], undefined, "task_workspace");
          taskBrowserSession = created?.session || null;
        } catch (error) {
          setLoading(false);
          setCurrentStatus("failed");
          setErrorMessage(error instanceof Error ? error.message : "Could not create a browser session.");
          return;
        }
      }

      safeSocketSend({
        // A clarification answer resumes the paused thread and carries the
        // answer as `message`. It cannot use `continue_task`: the backend
        // persists a clarification as PAUSED, which is an active status, so a
        // follow-up is rejected with "wait for the current task to finish".
        // `resume_task` is the one action that accepts a PAUSED task and already
        // re-enters the graph at the clarification step.
        action: isClarificationAnswer ? "resume_task" : isFollowUp ? "continue_task" : "run_task",
        message: trimmedMessage,
        ...(isFollowUp || isClarificationAnswer ? { thread_id: threadId } : {}),
        attachments: attachments.map((attachment) => attachment.id),
        ...(taskBrowserSession ? {
          browser_session_id: taskBrowserSession.id,
          // Existing Onsite48 Bridge pairing remains the compatibility
          // authorization path. Site permission and high-impact approval are
          // still enforced by the Bridge service and extension.
          browser_session_authorized: true,
          browser_session_origins: taskBrowserSession.requested_origins,
          browser_session_capabilities: taskBrowserSession.granted_capabilities.length ? taskBrowserSession.granted_capabilities : [
            "bridge_read_active_tab", "bridge_inspect_active_tab", "bridge_scroll_active_tab", "bridge_observe_surface",
            "bridge_snapshot_active_tab", "bridge_capture_active_tab", "bridge_navigate_active_tab", "bridge_click_active_tab",
            "bridge_fill_active_tab", "bridge_list_tabs", "bridge_open_tab", "bridge_activate_tab", "bridge_open_task_tabs",
            "bridge_close_task_tabs", "bridge_control_media", "bridge_download_active_tab", "bridge_upload_active_tab",
          ],
          browser_session_tab_id: taskBrowserSession.primary_tab_id,
        } : {}),
      });
      setTask("");
      setAttachments([]);
    },
    [task, attachments, currentStatus, userMessage, finalAnswer, reportArtifact, reset, connectWebSocket, safeSocketSend, browserSession, createBrowserSession, accessToken]
  );

  const retryFailedTask = useCallback(() => {
    if (!errorRetryable || currentStatus !== "failed") return;
    void submitTask("retry");
  }, [currentStatus, errorRetryable, submitTask]);

  // A queued task must fail visibly rather than trapping the interface in an
  // indefinite reconnect state when a network, proxy, or auth problem persists.
  useEffect(() => {
    if (!queuedMessage) return;
    if (queuedMessageTimeoutRef.current) clearTimeout(queuedMessageTimeoutRef.current);
    queuedMessageTimeoutRef.current = setTimeout(() => {
      setQueuedMessage(null);
      setLoading(false);
      setCurrentStatus("failed");
      setErrorMessage("Onsite48 could not establish an authenticated connection within 30 seconds, so this queued task was not sent. Check your connection, then submit it again.");
    }, 30_000);
    return () => {
      if (queuedMessageTimeoutRef.current) {
        clearTimeout(queuedMessageTimeoutRef.current);
        queuedMessageTimeoutRef.current = null;
      }
    };
  }, [queuedMessage]);

  // A user can send while the secure socket is reconnecting. The message is
  // delivered automatically as soon as the connection comes back.
  useEffect(() => {
    if (!queuedMessage || !isConnected) return;
    const message = queuedMessage;
    const retry = queuedRetryRef.current;
    queuedRetryRef.current = false;
    setQueuedMessage(null);
    if (retry && threadIdRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
      setCurrentStatus("connecting");
      setLoading(true);
      setErrorMessage(null);
      safeSocketSend({ action: "retry_task", thread_id: threadIdRef.current }, "The connection closed before the retry was sent. Reconnect and try again.");
    } else {
      submitTask(message);
    }
  }, [queuedMessage, isConnected, safeSocketSend, submitTask]);

  const confirmTool = useCallback(() => {
    if (!pendingConfirmation) return;
    if (safeSocketSend({
      action: "confirm_tool",
      task_id: pendingConfirmation.task_id,
      thread_id: pendingConfirmation.thread_id,
      tool_name: pendingConfirmation.tool_name,
      step_index: pendingConfirmation.step_index,
    }, "The connection closed before the approval was sent. Reconnect and try again.")) {
      setPendingConfirmation(null);
      setLoading(true);
    }
  }, [pendingConfirmation, safeSocketSend]);

  const allowReadOnlyForTask = useCallback(() => {
    if (!pendingConfirmation || !pendingConfirmation.task_scoped_read_only_available) return;
    if (safeSocketSend({
      action: "confirm_read_only_for_task",
      task_id: pendingConfirmation.task_id,
      thread_id: pendingConfirmation.thread_id,
      tool_name: pendingConfirmation.tool_name,
      step_index: pendingConfirmation.step_index,
    }, "The connection closed before the read-only approval was sent. Reconnect and try again.")) {
      setPendingConfirmation(null);
      setLoading(true);
    }
  }, [pendingConfirmation, safeSocketSend]);

  const rejectTool = useCallback(() => {
    if (!pendingConfirmation) return;
    if (safeSocketSend({
      action: "reject_tool",
      task_id: pendingConfirmation.task_id,
      thread_id: pendingConfirmation.thread_id,
      tool_name: pendingConfirmation.tool_name,
      step_index: pendingConfirmation.step_index,
    }, "The connection closed before the rejection was sent. Reconnect and try again.")) {
      setPendingConfirmation(null);
      setLoading(true);
    }
  }, [pendingConfirmation, safeSocketSend]);

  // ─── Pause Handler ─────────────────────────────────────────────

  const handlePause = useCallback(async () => {
    const threadId = threadIdRef.current;
    if (!threadId) {
      console.warn("No thread ID to pause");
      return;
    }

    // Set pause flag FIRST to prevent WebSocket events
    isPausedRef.current = true;
    // A locally-requested pause (abort, timeout) is not awaiting an answer, so
    // the next message is a new request.
    clarificationPauseRef.current = false;

    // Update UI immediately
    setCurrentStatus("paused");
    setLoading(false);
    setPendingConfirmation(null);

    try {
      const response = await fetch(`/api/tasks/${threadId}/pause`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(accessToken) },
        body: JSON.stringify({ reason: "User requested pause" }),
      });

      if (!response.ok) {
        const error = await response.json();
        // Revert if pause failed
        isPausedRef.current = false;
        setCurrentStatus("running");
        setLoading(true);
        throw new Error(error.detail || 'Failed to pause task');
      }

      setRecentTasks((previous) => {
        const next = previous.map((item) => item.id === threadId ? { ...item, status: "paused" as const, updatedAt: new Date().toISOString() } : item);
        onTasksChange?.(next);
        return next;
      });

      // Close the WebSocket to stop receiving events
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setIsConnected(false);

      console.log("Task paused successfully:", threadId);

    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to pause task');
      setCurrentStatus("running");
      setLoading(true);
    }
  }, [accessToken, onTasksChange]);

  // ─── Resume Handler ────────────────────────────────────────────

  const handleResume = useCallback(async () => {
    const threadId = threadIdRef.current;
    if (!threadId) return;
    isPausedRef.current = false;
    setCurrentStatus("connecting");
    setLoading(true);
    setErrorMessage(null);
    connectWebSocket();

    const startedAt = Date.now();
    const waitForConnection = async (): Promise<boolean> => {
      while (Date.now() - startedAt < 5000) {
        if (wsRef.current?.readyState === WebSocket.OPEN) return true;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      return false;
    };

    if (!(await waitForConnection()) || !wsRef.current) {
      setErrorMessage("The secure agent connection could not be restored.");
      setLoading(false);
      setCurrentStatus("paused");
      return;
    }
    if (!safeSocketSend({ action: "resume_task", thread_id: threadId }, "The connection closed before the task could resume. Please reconnect and try again.")) {
      setCurrentStatus("paused");
      return;
    }
    setRecentTasks((previous) => {
      const next = previous.map((item) => item.id === threadId ? { ...item, status: "running" as const, updatedAt: new Date().toISOString() } : item);
      onTasksChange?.(next);
      return next;
    });
    setCurrentStatus("running");
  }, [connectWebSocket, onTasksChange, safeSocketSend]);

  // ─── Stop Handler ──────────────────────────────────────────────

  const handleStop = useCallback(async () => {
    const threadId = threadIdRef.current;
    if (!threadId) return;

    setLoading(false);
    setCurrentStatus("cancelled");
    setErrorMessage("Task cancelled by user");
    setPendingConfirmation(null);
    isPausedRef.current = false;

    // Close WebSocket
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);

    // Remove from paused state if any
    try {
      await fetch(`/api/tasks/${threadId}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(accessToken) },
        body: JSON.stringify({}),
      });
    } catch (err) {
      // Ignore errors on stop
    }

    setRecentTasks((previous) => {
      const next = previous.map((item) => item.id === threadId ? { ...item, status: "cancelled" as const, updatedAt: new Date().toISOString() } : item);
      onTasksChange?.(next);
      return next;
    });
    console.log("Task stopped:", threadId);
  }, [accessToken, onTasksChange]);

  // ─── Reset and New Task ────────────────────────────────────────

  const handleNewTask = useCallback(() => {
    reset();
    setTask("");
  }, [reset]);

  // ─── Copy response text ─────────────────────────────────────────

  const copyResponseText = useCallback(async (text: string, messageId: string) => {
    if (!text.trim()) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedMessageId(messageId);
      window.setTimeout(() => {
        setCopiedMessageId((current) => current === messageId ? null : current);
      }, 1_600);
    } catch {
      setErrorMessage("Onsite48 could not copy this response. Check that your browser allows clipboard access and try again.");
    }
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey && !loading && !pendingConfirmation) {
        e.preventDefault();
        submitTask();
      }
    },
    [loading, pendingConfirmation, submitTask]
  );

  // ─── Render helpers ──────────────────────────────────────────────

  const completedCount = Object.keys(stepResults).length;
  const totalSteps = plan?.length ?? 0;
  const activeOperatorStep = plan?.[Math.min(completedCount, Math.max(0, totalSteps - 1))];
  const latestOperatorResult = completedCount > 0 ? stepResults[completedCount - 1] : null;
  const timelineItems: ExecutionTimelineItem[] = [
    ...(plan ? [{ id: "plan", phase: "plan", label: "Plan resolved", detail: `${plan.length} execution step${plan.length === 1 ? "" : "s"}`, status: "completed" as const }] : []),
    ...(plan || []).map((step, index) => ({
      id: `step-${index}`,
      phase: "execute",
      label: step.description,
      detail: step.tool_name || undefined,
      status: stepResults[index] ? "completed" as const : (loading && index === completedCount ? "active" as const : "pending" as const),
    })),
    ...(verification ? [{ id: "verification", phase: "verify", label: "Result verified", detail: verificationFeedback(verification.feedback), status: verification.status === "PASS" ? "completed" as const : "failed" as const }] : []),
  ];

  const composer = (
    <div className="relative">
      <input ref={fileInputRef} type="file" multiple className="hidden" onChange={uploadFiles} />
      {selectedQuote && (
        <div className="mb-2 flex items-start justify-between gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-800">
          <span className="min-w-0 truncate"><Quote className="mr-1 inline h-3.5 w-3.5" />Selected text is ready to quote into your prompt.</span>
          <button type="button" onClick={() => setSelectedQuote(null)} className="shrink-0 rounded p-0.5 hover:bg-brand-100" title="Discard selected quote"><X className="h-3.5 w-3.5" /></button>
        </div>
      )}
      {attachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {attachments.map((attachment) => (
            <span key={attachment.id} className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs text-blue-700">
              <Paperclip className="h-3 w-3 shrink-0" />
              <span className="max-w-40 truncate">{attachment.name}</span>
              <span title={attachment.extraction_warning || undefined} className={attachment.extraction_status === "ready" ? "text-emerald-700" : "text-slate-400"}>{attachment.extraction_status === "ready" ? "Ready" : "Binary file (not extracted)"}</span>
              <button type="button" onClick={() => removeAttachment(attachment.id)} className="rounded p-0.5 hover:bg-blue-100" title={`Remove ${attachment.name}`}><X className="h-3 w-3" /></button>
            </span>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2 rounded-2xl border border-gray-200 bg-gray-50 p-2.5 pl-4 shadow-[0_0_0_1px_rgba(99,102,241,0)] transition-shadow focus-within:border-transparent focus-within:shadow-[0_0_0_1.5px_#3b82f6]">
        <button
          type="button"
          title="Attach files"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading || attachments.length >= 5}
          className="mb-1 shrink-0 rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-onsite48-surface-hover hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
        </button>
        <textarea
          ref={composerTextareaRef}
          rows={1}
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={!!pendingConfirmation || (loading && currentStatus !== "connecting")}
          placeholder={isPausedRef.current ? "Task paused. Resume it or begin a new task." : currentStatus === "completed" ? "Ask a follow-up in this conversation…" : "Assign a task or type / for more"}
          className="max-h-40 flex-1 resize-none bg-transparent py-2 text-[15px] text-gray-900 placeholder-gray-400 outline-none disabled:opacity-60"
        />
        <button
          onClick={() => submitTask()}
          disabled={!task.trim() || !!pendingConfirmation || (loading && currentStatus !== "connecting")}
          title="Send"
          className="mb-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-gradient text-white transition-opacity disabled:opacity-30"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
        </button>
      </div>
      {!isConnected && !isPausedRef.current && (
        <p className="mt-2 text-center text-xs text-gray-400">{queuedMessage ? "Message queued — reconnecting…" : "Reconnecting…"}</p>
      )}
      {isPausedRef.current && (
        <p className="mt-2 text-center text-xs text-yellow-400">
          Task paused. Type a new message or click Resume to continue.
        </p>
      )}
    </div>
  );

  // ─── Empty state ───────────────────────────────────────────────

  if (!hasStarted) {
    return (
      <ManusHome
        composer={composer}
        onStart={(prompt) => submitTask(prompt)}
      />
    );
  }

  // ─── Active / completed thread ───────────────────────────────────

  const hasPlan = plan && plan.length > 0;
  const hasAnswer = streamingAnswer || finalAnswer;
  const showPlan = hasPlan && (currentStatus === "running" || currentStatus === "paused" || completedCount > 0);
  const answerText = finalAnswer || streamingAnswer;
  const liveObservation = browserSession?.current_observation?.snapshot as Record<string, any> | undefined;
  const liveDiff = browserSession?.current_observation?.diff as Record<string, any> | undefined;
  const liveChallenge = browserSession?.current_observation?.challenge as Record<string, any> | undefined;

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col bg-white px-4 pb-4 sm:px-6">
      {/* Thin status header */}
      <section className="mt-3 flex shrink-0 items-center justify-between rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-2">
        <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-900">{activeTaskTitle || userMessage || "Onsite48AI task"}</p>
            {activeTaskTitle && userMessage && activeTaskTitle !== userMessage && <details className="mt-1 text-xs text-slate-500"><summary className="cursor-pointer">Original request</summary><p className="mt-1 max-w-3xl whitespace-pre-wrap leading-5">{userMessage}</p></details>}
        </div>
        <div className="flex items-center gap-2 text-xs">
          {(currentStatus === "running" || currentStatus === "paused" || currentStatus === "completed" || currentStatus === "partial") && (
            <span className={`rounded-full px-2.5 py-1 font-medium ${statusPillClasses(currentStatus)}`}>
              {statusLabel(currentStatus)}
            </span>
          )}
          {typeof costMetrics?.total_cost_usd === "number" && (
            <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 font-medium tabular-nums text-slate-600" title="Running cost for this task">
              ${costMetrics.total_cost_usd.toFixed(6)}
            </span>
          )}
          {responseMode && (
            <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 font-medium text-slate-600">
              {responseMode === "quick" ? "Quick" : responseMode === "web_research" ? "Web" : "Autonomous"}
            </span>
          )}
        </div>
      </section>

      
      {/* My Browser authorize / connect CTA */}
      {(looksLikeBrowserTask(userMessage || "") || Boolean(browserSession)) && (!browserSession || !['active', 'running', 'authorized', 'ACTIVE', 'AUTHORIZED'].includes(String(browserSession.status || ''))) ? (
        <div className="mb-3">
          <AuthorizedActions
            taskTitle={userMessage || "This task"}
            origins={browserSession?.requested_origins || []}
            connected={Boolean(browserSession) || Boolean(accessToken)}
            authorized={Boolean(browserSession && ['active','running','authorized','ACTIVE','AUTHORIZED'].includes(String(browserSession.status || '')))}
            onAuthorize={async (origins) => {
              // The task row is created by the backend when the task runs,
              // so a session created here cannot reference it yet. It is
              // bound to its task by browser_session_id in the run_task frame.
              const created = await createBrowserSession(
                origins.length ? origins : ["https://"],
                ["observe", "navigate", "click", "type", "scroll", "screenshot", "takeover", "workspace"],
                undefined,
                "task_workspace",
              );
              if (created?.session) setBrowserSession(created.session);
            }}
          />
        </div>
      ) : null}

{browserSession && (
        <section className="mt-3 shrink-0 rounded-xl border border-indigo-200 bg-indigo-50/60 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-indigo-700">Browser session</p>
              <p className="truncate text-sm text-indigo-950">{browserSession.current_url || "Task browser workspace"}</p>
            </div>
            <span className="rounded-full bg-white px-2.5 py-1 text-xs font-medium text-indigo-700">{browserStatusLabel(String(browserSession.status || ""))}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {(browserSession.status === "running" || browserSession.status === "authorized") && (
              <button type="button" onClick={() => void controlBrowserSession("pause")} className="rounded-md border border-indigo-200 bg-white px-2.5 py-1 text-xs text-indigo-700">Pause</button>
            )}
            {(browserSession.status === "paused" || browserSession.status === "takeover") && (
              <button type="button" onClick={() => void controlBrowserSession("resume")} className="rounded-md border border-indigo-200 bg-white px-2.5 py-1 text-xs text-indigo-700">Resume</button>
            )}
            {browserSession.status === "running" && (
              <button type="button" onClick={() => void controlBrowserSession("takeover")} className="rounded-md border border-indigo-200 bg-white px-2.5 py-1 text-xs text-indigo-700">Take over</button>
            )}
            {!['completed', 'failed', 'cancelled', 'expired'].includes(browserSession.status) && (
              <button type="button" onClick={() => void controlBrowserSession("stop")} className="rounded-md border border-red-200 bg-white px-2.5 py-1 text-xs text-red-700">Stop browser</button>
            )}
          </div>
          {liveObservation && (
            <div className="mt-3 rounded-lg border border-indigo-100 bg-white/80 p-2.5 text-xs text-indigo-950">
              <div className="flex items-center justify-between gap-2"><span className="font-semibold">Live visible tab</span><span className="text-indigo-500">{liveObservation.observed_at ? new Date(liveObservation.observed_at).toLocaleTimeString() : "syncing"}</span></div>
              <p className="mt-1 truncate">{liveObservation.title || "Untitled page"} · {liveObservation.url || "No URL"}</p>
              <p className="mt-1 text-indigo-700">Digest {liveObservation.page_digest || "pending"} · {liveObservation.element_count ?? 0} visible elements</p>
              {Array.isArray(liveDiff?.changed_fields) && liveDiff.changed_fields.length > 0 && <p className="mt-1 text-amber-700">Changed: {liveDiff.changed_fields.join(", ")}</p>}
              {liveChallenge?.detected && <p className="mt-1 font-medium text-red-700">Manual handoff needed: {liveChallenge.reason || "browser challenge detected"}</p>}
            </div>
          )}
          {browserSessionEvents.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-indigo-700">Browser timeline ({browserSessionEvents.length})</summary>
              <ol className="mt-2 max-h-32 space-y-1 overflow-y-auto text-xs text-indigo-900">
                {browserSessionEvents.slice(-8).map((event) => <li key={`${event.sequence}-${event.type}`}><span className="font-mono text-indigo-500">#{event.sequence}</span> {event.type}</li>)}
              </ol>
            </details>
          )}
        </section>
      )}

      {conversationTurns.length >= 2 && <div className="absolute right-5 top-24 z-20">
        <button type="button" onClick={() => setShowConversationNavigator((value) => !value)} aria-expanded={showConversationNavigator} className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white/95 px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm hover:border-slate-300"><List className="h-3.5 w-3.5" /> Conversation <span className="text-slate-400">{conversationTurns.length}</span></button>
        {showConversationNavigator && <div className="mt-2 w-64 rounded-xl border border-slate-200 bg-white p-2 shadow-xl" role="navigation" aria-label="Conversation turns">{conversationTurns.map((turn, index) => <button key={turn.id} type="button" onClick={() => { turnRefs.current[turn.id]?.scrollIntoView({ behavior: "smooth", block: "start" }); setActiveTurnId(turn.id); setShowConversationNavigator(false); }} className={`flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left text-xs ${activeTurnId === turn.id ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50"}`}><span className="font-mono text-[10px] text-slate-400">{String(index + 1).padStart(2, "0")}</span><span className="line-clamp-2">{turn.title || turn.user}</span></button>)}</div>}
      </div>}
      {showJumpLatest && <button type="button" onClick={jumpToLatest} className="absolute bottom-24 right-5 z-20 inline-flex items-center gap-1.5 rounded-full bg-slate-900 px-3 py-2 text-xs font-medium text-white shadow-lg hover:bg-slate-700"><ArrowDown className="h-3.5 w-3.5" /> Jump to latest</button>}
      <div ref={transcriptRef} onScroll={trackTranscriptScroll} onMouseUp={captureSelectedQuote} onKeyUp={captureSelectedQuote} className="relative h-0 min-h-0 flex-1 overflow-y-scroll overscroll-contain py-6 pr-2" style={{ scrollbarGutter: "stable" }}>
        {selectedQuote && (
          <div className="sticky top-0 z-20 flex justify-end">
            <button
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={insertSelectedQuote}
              className="mb-4 inline-flex items-center gap-1.5 rounded-full bg-brand-600 px-3 py-1.5 text-xs font-medium text-white shadow-md transition-colors hover:bg-brand-700"
              title="Add the selected text to your next prompt"
            >
              <Quote className="h-3.5 w-3.5" /> Quote selection
            </button>
          </div>
        )}

        {/* Completed conversation turns */}
        {conversationTurns.map((turn) => (
          <div key={turn.id} id={`turn-${turn.id}`} data-turn-id={turn.id} ref={(node) => { turnRefs.current[turn.id] = node; }} className={`mb-10 rounded-xl transition ${activeTurnId === turn.id ? "ring-2 ring-blue-100 ring-offset-2" : ""}`}>
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-brand-600/90 px-4 py-2.5 text-[15px] text-white">
                {turn.user}
              </div>
            </div>
            {turn.answer && (
              <div className="mt-3 rounded-2xl border border-slate-200 bg-white p-5">
                <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-400">
                  <img src="/Onsite48-opt.png" alt="" className="h-3.5 w-3.5 object-contain" /> Answer
                </div>
                <MessageContent markdown={turn.answer} className="text-[15px]" />
                {turn.artifact && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                    <button
                      type="button"
                      onClick={() => {
                        setReportArtifact(turn.artifact || null);
                        setShowReportReader(true);
                      }}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-700"
                    >
                      <Presentation className="h-3.5 w-3.5" /> Open report
                    </button>
                    <ArtifactDownloadButton
                      name={turn.artifact.name}
                      downloadUrl={turn.artifact.download_url}
                      accessToken={accessToken}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {/* Current user message */}
        {userMessage && (
          <div className="mb-6 flex justify-end">
            <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-brand-600/90 px-4 py-2.5 text-[15px] text-white">
              {userMessage}
            </div>
          </div>
        )}

        {/* Attachments */}
        {attachments.length > 0 && (
          <div className="mb-4 flex flex-wrap gap-2">
            {attachments.map((attachment) => (
              <span key={attachment.id} className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs text-blue-700">
                <Paperclip className="h-3 w-3 shrink-0" />
                <span className="max-w-40 truncate">{attachment.name}</span>
                <span className={attachment.extraction_status === "ready" ? "text-emerald-700" : "text-slate-400"}>
                  {attachment.extraction_status === "ready" ? "Ready" : "Binary file (not extracted)"}
                </span>
              </span>
            ))}
          </div>
        )}

        {/* Plan and execution are grouped in one collapsible surface so the
            working process recedes and the final answer reads as the result. */}
        {(showPlan || answerText) && (
          <div className="mb-6 space-y-3">
            {showPlan && (
              <section className="rounded-2xl border border-slate-200 bg-slate-50/70">
                <button
                  type="button"
                  onClick={() => setShowPlanDetails((prev) => !prev)}
                  className="flex w-full items-center justify-between px-5 py-3.5 text-left"
                >
                  <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                    <Brain className="h-3.5 w-3.5" /> Plan
                  </span>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-slate-400">
                      {completedCount}/{totalSteps} steps
                    </span>
                    <ChevronDown className={`h-4 w-4 text-slate-400 transition-transform ${showPlanDetails ? "rotate-180" : ""}`} />
                  </div>
                </button>
                {showPlanDetails && (
                  <div>
                    <ExecutionTimeline items={timelineItems} />
                    <ol className="space-y-2.5 border-t border-slate-200/80 px-5 py-4">
                    {plan.map((step, i) => {
                      const result = stepResults[i];
                      const isNext = !result && i === completedCount && loading;
                      // The step currently executing renders its
                      // output inline (with a trailing cursor)
                      // instead of behind a details click, so live
                      // tool output is visible while it grows.
                      const isLiveStep = Boolean(result) && loading && (i === completedCount || i === completedCount - 1);
                      return (
                        <li key={i} className="flex items-start gap-3 text-sm">
                          {result ? (
                            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                          ) : isNext ? (
                            <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-brand-500" />
                          ) : (
                            <Circle className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" />
                          )}
                          <div className="min-w-0 flex-1">
                            <p className={result ? "text-slate-900" : "text-slate-600"}>
                              {step.description}
                              {step.tool_name && (
                                <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
                                  {step.tool_name}
                                </span>
                              )}
                            </p>
                            {result && result.output && (
                              isLiveStep ? (
                                <div className="mt-1.5 whitespace-pre-wrap rounded-lg bg-slate-50 p-2.5 text-xs leading-5 text-slate-600">
                                  {result.output}
                                  <span className="inline-block ml-1 h-3 w-0.5 animate-pulse bg-slate-400 align-middle" />
                                </div>
                              ) : (
                                <details className="mt-1.5 group">
                                  <summary className="flex cursor-pointer list-none items-center gap-1 text-xs text-slate-400 hover:text-slate-600">
                                    <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" />
                                    Output
                                  </summary>
                                  <p className="mt-1.5 whitespace-pre-wrap rounded-lg bg-slate-50 p-2.5 text-xs leading-5 text-slate-600">
                                    {result.output}
                                  </p>
                                </details>
                              )
                            )}
                          </div>
                        </li>
                      );
                    })}
                    </ol>
                  </div>
                )}
              </section>
            )}

            {/* Answer — distinct final-result surface, rendered below the collapsible plan/execution */}
            {answerText && (
              <section className="rounded-2xl border border-slate-200 border-l-4 border-l-brand-600 bg-white p-5 shadow-sm">
                <div className="mb-3 flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-xs font-semibold text-brand-700">
                    <img src="/Onsite48-opt.png" alt="" className="h-4 w-4 object-contain" /> Answer
                    {responseMode && (
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                        {responseMode === "quick" ? "Quick" : responseMode === "web_research" ? "Web" : "Autonomous"}
                      </span>
                    )}
                  </span>
                  <button
                    type="button"
                    onClick={() => void copyResponseText(answerText, "final-answer")}
                    aria-label="Copy response"
                    title="Copy response"
                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-400 opacity-0 transition hover:bg-slate-100 hover:text-slate-600 focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-slate-400 hover:opacity-100"
                  >
                    {copiedMessageId === "final-answer" ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
                    {copiedMessageId === "final-answer" ? "Copied" : "Copy"}
                  </button>
                </div>
                <MessageContent markdown={answerText} className="text-[15px]" />
                {streamingAnswer && !finalAnswer && (
                  <span className="inline-block ml-1 h-4 w-0.5 animate-pulse bg-slate-400 align-middle" />
                )}
                {reportArtifact && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      onClick={() => setShowReportReader(true)}
                      className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-700"
                      title={`Open ${reportArtifact.name} inside Onsite48AI`}
                    >
                      <FolderOpen className="h-4 w-4" /> Open report
                    </button>
                    <ArtifactDownloadButton
                      name={reportArtifact.name}
                      downloadUrl={reportArtifact.download_url}
                      accessToken={accessToken}
                    />
                  </div>
                )}
                {typeof costMetrics?.total_cost_usd === "number" && (
                  <p className="mt-2 text-xs text-slate-400">
                    Cost: ${costMetrics.total_cost_usd.toFixed(6)}
                  </p>
                )}
              </section>
            )}

            {/* Verification */}
        {verification && (
              <details className="group rounded-2xl border border-slate-200 bg-white text-xs text-slate-400">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3">
                    {verification.status === "PASS" ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                    ) : verification.status === "UNVERIFIED" ? (
                      <Circle className="h-3.5 w-3.5 text-amber-500" />
                  ) : (
                    <XCircle className="h-3.5 w-3.5 text-amber-500" />
                  )}
                  <span>
                    Verification: {verification.status || "unknown"}
                    {typeof verification.score === "number" && ` · ${verification.score}/100`}
                  </span>
                  <ChevronDown className="ml-auto h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                </summary>
                <div className="border-t border-slate-100 px-5 py-3 leading-5">
                  {verificationFeedback(verification.feedback)}
                </div>
          </details>
        )}
      </div>
    )}

    {websitePreview && (
      <section className="mb-4 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" aria-label="Website preview">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-900">{websitePreview.project_name}</p>
            <p className="text-xs text-slate-500">Isolated static preview · not published</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => setPreviewViewport("desktop")} aria-pressed={previewViewport === "desktop"} className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${previewViewport === "desktop" ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
              <Monitor className="h-3.5 w-3.5" /> Desktop
            </button>
            <button type="button" onClick={() => setPreviewViewport("mobile")} aria-pressed={previewViewport === "mobile"} className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${previewViewport === "mobile" ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
              <Smartphone className="h-3.5 w-3.5" /> Mobile
            </button>
            <button type="button" onClick={() => setPreviewReloadKey((value) => value + 1)} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50" title="Refresh preview">
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
            <a href={websitePreview.preview_url} target="_blank" rel="noreferrer" referrerPolicy="no-referrer" className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
              <ExternalLink className="h-3.5 w-3.5" /> Open
            </a>
          </div>
        </div>
        <div className="flex justify-center bg-slate-100 p-3">
          <iframe
            key={`${websitePreview.preview_id}-${previewViewport}-${previewReloadKey}`}
            title={`${websitePreview.project_name} ${previewViewport} preview`}
            src={`${websitePreview.preview_url}?refresh=${previewReloadKey}`}
            sandbox="allow-scripts allow-forms allow-popups"
            referrerPolicy="no-referrer"
            className="rounded-lg border border-slate-300 bg-white shadow-sm"
            style={{ width: previewViewport === "mobile" ? "min(100%, 390px)" : "100%", height: previewViewport === "mobile" ? 760 : 680 }}
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3">
          <p className="text-xs text-slate-500">Preview expires automatically. Public publishing is a separate, approval-gated action.</p>
          {websiteArtifact ? (
            <ArtifactDownloadButton name={websiteArtifact.name} downloadUrl={websiteArtifact.download_url} accessToken={accessToken} />
          ) : (
            <span className="text-xs text-slate-400">Project ZIP is being prepared</span>
          )}
        </div>
      </section>
    )}
    {websiteArtifact && !websitePreview && (
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
        <span className="text-sm font-medium text-slate-700">Website source project</span>
        <ArtifactDownloadButton name={websiteArtifact.name} downloadUrl={websiteArtifact.download_url} accessToken={accessToken} />
      </div>
    )}

    {/* Inline confirmation */}
        {pendingConfirmation && (
          <div className="mb-4 rounded-xl border border-amber-500/25 bg-amber-500/10 p-4">
            <p className="text-sm text-slate-900">
              Onsite48 wants to run{" "}
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-xs font-semibold text-amber-300">
                {pendingConfirmation.tool_name}
              </span>
            </p>
            <p className="mt-1 text-xs text-slate-600">{pendingConfirmation.step_description}</p>
            <details className="mt-2 rounded-lg border border-amber-500/20 bg-white/70 px-3 py-2">
              <summary className="cursor-pointer text-xs font-medium text-slate-600">Review exact action details</summary>
              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-4 text-slate-700">{JSON.stringify(pendingConfirmation.tool_args, null, 2)}</pre>
            </details>
            <div className="mt-3 flex gap-2">
              <button
                onClick={confirmTool}
                className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-700"
              >
                <Check className="h-3.5 w-3.5" /> Confirm
              </button>
              {pendingConfirmation.task_scoped_read_only_available && (
                <button
                  onClick={allowReadOnlyForTask}
                  className="flex items-center gap-1.5 rounded-lg border border-sky-500/35 bg-sky-500/10 px-3 py-1.5 text-sm font-medium text-sky-700 transition-colors hover:bg-sky-500/20"
                >
                  <Check className="h-3.5 w-3.5" /> Allow read-only
                </button>
              )}
              <button
                onClick={rejectTool}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50"
              >
                <Ban className="h-3.5 w-3.5" /> Reject
              </button>
            </div>
          </div>
        )}

        {/* Inline human intervention */}
        {userIntervention && (
          <div className="mb-4 rounded-xl border border-amber-500/25 bg-amber-500/10 p-4">
            <p className="text-sm font-semibold text-amber-900">
              {userIntervention.handoff_surface === "onsite48_bridge" ? "Selected browser tab needs attention" : "Human verification required"}
            </p>
            <p className="mt-1 text-sm text-amber-800/80">{userIntervention.reason}</p>
            {userIntervention.url && (
              <p className="mt-1.5 truncate text-xs text-amber-800/70">
                {userIntervention.title || userIntervention.url} · {userIntervention.url}
              </p>
            )}
            {userIntervention.handoff_options.length > 0 && (
              <div className="mt-3 rounded-lg border border-amber-500/20 bg-white/60 p-3 text-xs leading-5 text-amber-900/80">
                {userIntervention.handoff_options.map((option) => (
                  <p key={option} className="mt-1 first:mt-0">• {option}</p>
                ))}
              </div>
            )}
            {userIntervention.handoff_surface === "agent_computer" && (
              <button onClick={() => setShowWorkspace(true)} className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-amber-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-900">
                <FolderOpen className="h-3.5 w-3.5" /> Take over Agent Computer
              </button>
            )}
          </div>
        )}

        {/* Inline error */}
        {errorMessage && (
          <div className="mb-4 rounded-xl border border-red-500/25 bg-red-500/10 p-4">
            <p className="text-sm font-medium text-red-300">Something went wrong</p>
            <p className="mt-1 whitespace-pre-wrap text-sm text-red-200/80">{errorMessage}</p>
            <SupportIds errorId={errorId} traceId={traceId} />
            {errorRetryable && (
              <button
                type="button"
                onClick={retryFailedTask}
                className="mt-3 rounded-lg border border-red-300/40 px-3 py-1.5 text-xs font-semibold text-red-100 hover:bg-red-500/20"
              >
                Retry
              </button>
            )}
          </div>
        )}

        {/* Loading indicator */}
        {loading && !pendingConfirmation && currentStatus !== "paused" && (
          <div className="mt-4 flex items-center gap-2 text-xs text-slate-400">
            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${statusPillClasses(currentStatus)}`}>
              <Loader2 className="h-3 w-3 animate-spin" />
              {currentStatus === "connecting" ? "Starting…" : "Working…"}
            </span>
          </div>
        )}

        <div ref={transcriptEndRef} aria-hidden="true" />
      </div>

      {/* Minimal inline controls */}
      {(currentStatus === "running" || currentStatus === "paused" || currentStatus === "completed") && (
        <div className="shrink-0 border-t border-slate-200 bg-white px-2 py-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1">
              <span className={`w-1.5 h-1.5 rounded-full ${statusDotClass(currentStatus)}`} />
              <span className="text-xs text-slate-500 capitalize">
                {statusLabel(currentStatus)}
              </span>
            </div>
            <div className="flex items-center gap-0.5">
              {currentStatus === "running" && (
                <button onClick={handlePause} className="p-1.5 rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors" title="Pause">
                  <Pause className="w-3.5 h-3.5" />
                </button>
              )}
              {currentStatus === "paused" && (
                <button onClick={handleResume} className="p-1.5 rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors" title="Resume">
                  <Play className="w-3.5 h-3.5" />
                </button>
              )}
              {(currentStatus === "running" || currentStatus === "paused") && (
                <button onClick={handleStop} className="p-1.5 rounded-md text-slate-500 hover:text-red-600 hover:bg-red-50 transition-colors" title="Stop">
                  <Square className="w-3.5 h-3.5" />
                </button>
              )}
              {currentStatus === "paused" && (
                <button onClick={() => setShowInspector(!showInspector)} className="p-1.5 rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors" title="Modify plan">
                  <Edit className="w-3.5 h-3.5" />
                </button>
              )}
              <button onClick={handleNewTask} className="p-1.5 rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors" title="New task">
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
              {reportArtifact && (
                <button onClick={() => setShowReportReader(true)} className="p-1.5 rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors" title="Report">
                  <Download className="w-3.5 h-3.5" />
                </button>
              )}
              <button onClick={() => setShowWorkspace(!showWorkspace)} className="p-1.5 rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors" title="Agent computer">
                <FolderOpen className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* State Inspector Modal */}
      {showInspector && threadIdRef.current && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-onsite48-background rounded-xl border border-onsite48-border max-w-2xl w-full max-h-[80vh] overflow-y-auto p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Task Inspector</h2>
              <button
                onClick={() => setShowInspector(false)}
                className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-gray-400 hover:text-gray-900"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <StateInspector
              threadId={threadIdRef.current}
              onModifyStep={async (id, stepIndex, newStep) => {
                const response = await fetch(`/api/tasks/${id}/modify-step`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json', ...authHeaders(accessToken) },
                  body: JSON.stringify({ step_index: stepIndex, new_step: newStep }),
                });
                if (!response.ok) throw new Error('Failed to modify step');
                // Refresh inspector
              }}
            />
          </div>
        </div>
      )}

      {showReportReader && reportArtifact && (
        <MarkdownReportViewer
          name={reportArtifact.name}
          downloadUrl={reportArtifact.download_url}
          accessToken={accessToken}
          onClose={() => setShowReportReader(false)}
        />
      )}

      {/* Workspace Modal */}
      {showWorkspace && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="flex h-[80vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-onsite48-border bg-onsite48-background">
            <div className="flex shrink-0 items-center justify-between border-b border-onsite48-border bg-onsite48-surface-light p-3">
              <h2 className="text-sm font-semibold text-gray-900">Agent computer</h2>
              <button
                onClick={() => setShowWorkspace(false)}
                className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-gray-400 hover:text-gray-900"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {threadIdRef.current && <div className="min-h-0 flex-1"><AgentComputerWorkspace threadId={threadIdRef.current} onUserControlChange={(active) => { if (active) setCurrentStatus("paused"); }} /></div>}
          </div>
        </div>
      )}

      {/* Sticky composer once a thread has started */}
      <div className="shrink-0 bg-white pb-2 pt-4">
        {composer}
      </div>
    </div>
  );
});

export default ChatInterface;
