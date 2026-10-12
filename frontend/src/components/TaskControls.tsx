// frontend/src/components/TaskControls.tsx

"use client";

import { Pause, Play, Edit, Square, RefreshCw, Loader2 } from 'lucide-react';
import { useState } from 'react';

interface TaskControlsProps {
  threadId: string;
  status: 'idle' | 'running' | 'paused' | 'completed' | 'failed';
  onPause: (threadId: string) => Promise<void>;
  onResume: (threadId: string) => Promise<void>;
  onStop: (threadId: string) => Promise<void>;
  onModify: (threadId: string) => void;
  onRefresh: (threadId: string) => Promise<void>;
}

export function TaskControls({
  threadId,
  status,
  onPause,
  onResume,
  onStop,
  onModify,
  onRefresh,
}: TaskControlsProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handlePause = async () => {
    setLoading(true);
    setError(null);
    try {
      await onPause(threadId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to pause task');
    } finally {
      setLoading(false);
    }
  };

  const handleResume = async () => {
    setLoading(true);
    setError(null);
    try {
      await onResume(threadId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resume task');
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    setError(null);
    try {
      await onStop(threadId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop task');
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = async () => {
    setLoading(true);
    setError(null);
    try {
      await onRefresh(threadId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh task');
    } finally {
      setLoading(false);
    }
  };

  const statusColors = {
    idle: 'text-text-muted',
    running: 'text-green-400',
    paused: 'text-yellow-400',
    completed: 'text-blue-400',
    failed: 'text-red-400',
  };

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1">
        <span className={`w-2 h-2 rounded-full ${statusColors[status]}`} />
        <span className="text-sm text-text-secondary capitalize">{status}</span>
      </div>

      <div className="flex gap-1 ml-2">
        {status === 'running' && (
          <button
            onClick={handlePause}
            disabled={loading}
            className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-secondary hover:text-text-primary transition-colors"
            title="Pause task"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Pause className="w-4 h-4" />}
          </button>
        )}

        {status === 'paused' && (
          <>
            <button
              onClick={handleResume}
              disabled={loading}
              className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-secondary hover:text-text-primary transition-colors"
              title="Resume task"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            </button>
            <button
              onClick={() => onModify(threadId)}
              className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-secondary hover:text-text-primary transition-colors"
              title="Modify plan"
            >
              <Edit className="w-4 h-4" />
            </button>
          </>
        )}

        {(status === 'running' || status === 'paused') && (
          <button
            onClick={handleStop}
            disabled={loading}
            className="p-1.5 rounded-lg hover:bg-red-500/15 text-text-secondary hover:text-red-400 transition-colors"
            title="Stop task"
          >
            <Square className="w-4 h-4" />
          </button>
        )}

        <button
          onClick={handleRefresh}
          disabled={loading}
          className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-secondary hover:text-text-primary transition-colors"
          title="Refresh state"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
        </button>
      </div>

      {error && (
        <span className="text-xs text-red-400 ml-2">{error}</span>
      )}
    </div>
  );
}