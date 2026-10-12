// frontend/src/components/TaskReplay.tsx

"use client";

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Play,
  Pause,
  SkipForward,
  SkipBack,
  ChevronRight,
  ChevronDown,
  Clock,
  DollarSign,
  Loader2,
  CheckCircle2,
  XCircle,
  Sparkles,
  RefreshCw,
} from 'lucide-react';

interface Event {
  type: string;
  timestamp: string;
  data: any;
  step_index: number | null;
}

interface TaskDetail {
  thread_id: string;
  task: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  total_cost_usd: number;
  tool_calls_count: number;
  events_count: number;
  timeline: Event[];
  summary: string | null;
}

interface TaskReplayProps {
  threadId: string;
  onClose: () => void;
}

export function TaskReplay({ threadId, onClose }: TaskReplayProps) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentEventIndex, setCurrentEventIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set());
  
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  const fetchTask = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/history/tasks/${threadId}`);
      if (!response.ok) throw new Error('Failed to fetch task');
      const data = await response.json();
      setTask(data);
      setCurrentEventIndex(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load task');
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  useEffect(() => {
    fetchTask();
  }, [fetchTask]);

  // Playback timer
  useEffect(() => {
    if (isPlaying && task) {
      timerRef.current = setInterval(() => {
        setCurrentEventIndex(prev => {
          if (prev >= (task?.timeline.length || 0) - 1) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 1000 / playbackSpeed);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [isPlaying, playbackSpeed, task]);

  const togglePlay = () => {
    if (currentEventIndex >= (task?.timeline.length || 0) - 1) {
      setCurrentEventIndex(0);
    }
    setIsPlaying(!isPlaying);
  };

  const goToEvent = (index: number) => {
    setCurrentEventIndex(Math.max(0, Math.min(index, (task?.timeline.length || 0) - 1)));
  };

  const toggleEventExpanded = (index: number) => {
    setExpandedEvents(prev => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleString();
    } catch {
      return dateStr;
    }
  };

  const formatDuration = (seconds: number | null) => {
    if (!seconds) return 'N/A';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.round(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
  };

  const getEventIcon = (type: string) => {
    switch (type) {
      case 'task_start': return <Play className="w-4 h-4 text-green-400" />;
      case 'task_complete': return <CheckCircle2 className="w-4 h-4 text-emerald-400" />;
      case 'task_failed': return <XCircle className="w-4 h-4 text-red-400" />;
      case 'plan_generated': return <Sparkles className="w-4 h-4 text-purple-400" />;
      case 'tool_call': return <ChevronRight className="w-4 h-4 text-blue-400" />;
      case 'verification': return <CheckCircle2 className="w-4 h-4 text-yellow-400" />;
      default: return <Clock className="w-4 h-4 text-text-muted" />;
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <Loader2 className="w-6 h-6 animate-spin text-brand-400" />
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="p-4 text-red-400 text-sm">
        {error || 'Task not found'}
        <button onClick={fetchTask} className="ml-4 text-brand-400 hover:underline">
          Retry
        </button>
      </div>
    );
  }

  const totalEvents = task.timeline.length;
  const currentEvent = task.timeline[currentEventIndex];

  return (
    <div className="bg-onsite48-background rounded-xl border border-onsite48-border overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-onsite48-border bg-onsite48-surface-light">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-text-primary">Task Replay</h2>
            <p className="text-sm text-text-muted truncate max-w-md">{task.task}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
          >
            <XCircle className="w-5 h-5" />
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-4 mt-2 text-xs text-text-muted">
          <span>Status: {task.status}</span>
          <span>Created: {formatDate(task.created_at)}</span>
          {task.duration_seconds && <span>Duration: {formatDuration(task.duration_seconds)}</span>}
          <span className="flex items-center gap-1">
            <DollarSign className="w-3 h-3" />
            ${task.total_cost_usd.toFixed(6)}
          </span>
          <span>{task.events_count} events</span>
        </div>
      </div>

      {/* Controls */}
      <div className="p-4 border-b border-onsite48-border bg-onsite48-surface-light/50">
        <div className="flex items-center gap-3">
          <button
            onClick={() => goToEvent(0)}
            className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
            disabled={currentEventIndex === 0}
          >
            <SkipBack className="w-4 h-4" />
          </button>
          
          <button
            onClick={togglePlay}
            className="p-2 rounded-full bg-brand-600 hover:bg-brand-700 text-white transition-colors"
          >
            {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
          </button>
          
          <button
            onClick={() => goToEvent(Math.min(currentEventIndex + 1, totalEvents - 1))}
            className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
            disabled={currentEventIndex >= totalEvents - 1}
          >
            <SkipForward className="w-4 h-4" />
          </button>
          
          <div className="flex items-center gap-2 ml-4">
            <span className="text-xs text-text-muted">Speed:</span>
            <select
              value={playbackSpeed}
              onChange={(e) => setPlaybackSpeed(Number(e.target.value))}
              className="bg-onsite48-surface rounded-lg px-2 py-1 text-xs border border-onsite48-border focus:border-brand-500 outline-none"
            >
              <option value={0.5}>0.5x</option>
              <option value={1}>1x</option>
              <option value={2}>2x</option>
              <option value={4}>4x</option>
            </select>
          </div>
          
          <div className="flex-1 text-center text-sm text-text-muted">
            {currentEventIndex + 1} / {totalEvents}
          </div>
          
          <button
            onClick={() => setCurrentEventIndex(totalEvents - 1)}
            className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
        
        {/* Progress bar */}
        <div className="mt-2 h-1 bg-onsite48-surface-hover rounded-full overflow-hidden">
          <div
            className="h-full bg-brand-500 transition-all duration-200"
            style={{ width: `${(currentEventIndex / Math.max(totalEvents - 1, 1)) * 100}%` }}
          />
        </div>
      </div>

      {/* Current Event */}
      {currentEvent && (
        <div className="p-4 border-b border-onsite48-border">
          <div className="flex items-start gap-3">
            <div className="mt-0.5">{getEventIcon(currentEvent.type)}</div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-text-primary">
                  {currentEvent.type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                </span>
                <span className="text-xs text-text-muted">{formatDate(currentEvent.timestamp)}</span>
                {currentEvent.step_index !== null && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-onsite48-surface-hover text-text-muted">
                    Step {currentEvent.step_index + 1}
                  </span>
                )}
              </div>
              <div className="mt-1">
                <pre className="text-xs text-text-secondary bg-onsite48-surface-light p-3 rounded-lg overflow-auto max-h-48">
                  {JSON.stringify(currentEvent.data, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Event Timeline */}
      <div className="max-h-96 overflow-y-auto p-2">
        {task.timeline.map((event, index) => (
          <div
            key={index}
            className={`flex items-start gap-2 p-2 rounded-lg cursor-pointer transition-colors ${
              index === currentEventIndex
                ? 'bg-brand-500/10 border border-brand-500/30'
                : 'hover:bg-onsite48-surface-hover'
            }`}
            onClick={() => goToEvent(index)}
          >
            <div className="mt-0.5">{getEventIcon(event.type)}</div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-text-primary">
                  {event.type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                </span>
                <span className="text-xs text-text-muted">
                  {new Date(event.timestamp).toLocaleTimeString()}
                </span>
                {event.step_index !== null && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-onsite48-surface-hover text-text-muted">
                    #{event.step_index + 1}
                  </span>
                )}
              </div>
              {event.data?.step && (
                <p className="text-xs text-text-secondary truncate">{event.data.step}</p>
              )}
              {event.data?.tool && (
                <span className="text-xs text-blue-400">{event.data.tool}</span>
              )}
            </div>
            {index === currentEventIndex && (
              <ChevronRight className="w-4 h-4 text-brand-400" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}