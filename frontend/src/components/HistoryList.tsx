// frontend/src/components/HistoryList.tsx

"use client";

import { useState, useEffect, useCallback } from 'react';
import {
  Search,
  Filter,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Clock,
  DollarSign,
  CheckCircle2,
  XCircle,
  Loader2,
  Play,
  Download,
  Trash2,
  Eye,
} from 'lucide-react';

interface TaskSummary {
  thread_id: string;
  task: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  total_cost_usd: number;
  events_count: number;
  summary: string | null;
}

interface HistoryListProps {
  onSelectTask: (threadId: string) => void;
  onReplayTask: (threadId: string) => void;
  selectedTaskId?: string | null;
}

export function HistoryList({ onSelectTask, onReplayTask, selectedTaskId }: HistoryListProps) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<'created_at' | 'duration' | 'cost'>('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [expandedTask, setExpandedTask] = useState<string | null>(null);

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        limit: '100',
        user_id: 'dashboard',
        ...(statusFilter !== 'all' && { status: statusFilter }),
      });
      
      const response = await fetch(`/api/history/tasks?${params}`);
      if (!response.ok) throw new Error('Failed to fetch tasks');
      const data = await response.json();
      setTasks(data.tasks || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  const filteredTasks = tasks.filter(task =>
    task.task.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const sortedTasks = [...filteredTasks].sort((a, b) => {
    let aVal: string | number | Date;
    let bVal: string | number | Date;

    if (sortBy === 'created_at') {
      aVal = a.created_at;
      bVal = b.created_at;
    } else if (sortBy === 'duration') {
      aVal = a.duration_seconds ?? 0;
      bVal = b.duration_seconds ?? 0;
    } else {
      aVal = a.total_cost_usd;
      bVal = b.total_cost_usd;
    }

    if (sortOrder === 'asc') {
      return aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
    } else {
      return aVal > bVal ? -1 : aVal < bVal ? 1 : 0;
    }
  });

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

  const getStatusBadge = (status: string) => {
    const configs: Record<string, { color: string; icon: React.ReactNode }> = {
      completed: { color: 'bg-emerald-500/15 text-emerald-400', icon: <CheckCircle2 className="w-3 h-3" /> },
      failed: { color: 'bg-red-500/15 text-red-400', icon: <XCircle className="w-3 h-3" /> },
      running: { color: 'bg-blue-500/15 text-blue-400', icon: <Loader2 className="w-3 h-3 animate-spin" /> },
      paused: { color: 'bg-yellow-500/15 text-yellow-400', icon: <Clock className="w-3 h-3" /> },
      pending: { color: 'bg-gray-500/15 text-gray-400', icon: <Clock className="w-3 h-3" /> },
    };
    const config = configs[status] || configs.pending;
    return (
      <span className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs ${config.color}`}>
        {config.icon}
        {status}
      </span>
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <Loader2 className="w-6 h-6 animate-spin text-brand-400" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-red-400 text-sm">
        Error: {error}
        <button onClick={fetchTasks} className="ml-4 text-brand-400 hover:underline">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 min-w-[200px] relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search tasks..."
            className="w-full bg-onsite48-surface-light rounded-lg pl-9 pr-4 py-2 text-sm border border-onsite48-border focus:border-brand-500 outline-none transition-colors"
          />
        </div>
        
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-onsite48-surface-light rounded-lg px-3 py-2 text-sm border border-onsite48-border focus:border-brand-500 outline-none"
        >
          <option value="all">All Status</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
          <option value="paused">Paused</option>
        </select>
        
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as 'created_at' | 'duration' | 'cost')}
          className="bg-onsite48-surface-light rounded-lg px-3 py-2 text-sm border border-onsite48-border focus:border-brand-500 outline-none"
        >
          <option value="created_at">Created</option>
          <option value="duration">Duration</option>
          <option value="cost">Cost</option>
        </select>
        
        <button
          onClick={() => setSortOrder(order => order === 'asc' ? 'desc' : 'asc')}
          className="p-2 rounded-lg bg-onsite48-surface-light border border-onsite48-border hover:bg-onsite48-surface-hover transition-colors"
        >
          {sortOrder === 'asc' ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
        
        <button
          onClick={fetchTasks}
          className="px-3 py-2 rounded-lg bg-onsite48-surface-light border border-onsite48-border hover:bg-onsite48-surface-hover transition-colors text-sm"
        >
          Refresh
        </button>
      </div>

      {/* Task List */}
      {sortedTasks.length === 0 ? (
        <div className="text-center py-8 text-text-muted">
          No tasks found
        </div>
      ) : (
        <div className="space-y-2">
          {sortedTasks.map((task) => (
            <div
              key={task.thread_id}
              className={`bg-onsite48-surface rounded-xl border transition-all ${
                selectedTaskId === task.thread_id
                  ? 'border-brand-500/50 bg-brand-500/5'
                  : 'border-onsite48-border hover:border-onsite48-surface-hover'
              }`}
            >
              <div className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setExpandedTask(expandedTask === task.thread_id ? null : task.thread_id)}
                        className="p-1 rounded hover:bg-onsite48-surface-hover transition-colors"
                      >
                        {expandedTask === task.thread_id ? (
                          <ChevronDown className="w-4 h-4 text-text-muted" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-text-muted" />
                        )}
                      </button>
                      <p className="font-medium text-text-primary truncate">
                        {task.task}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-3 mt-1.5 text-xs text-text-muted">
                      {getStatusBadge(task.status)}
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatDate(task.created_at)}
                      </span>
                      {task.duration_seconds && (
                        <span>⏱ {formatDuration(task.duration_seconds)}</span>
                      )}
                      <span className="flex items-center gap-1">
                        <DollarSign className="w-3 h-3" />
                        ${task.total_cost_usd.toFixed(6)}
                      </span>
                      <span>{task.events_count} events</span>
                    </div>
                  </div>
                  
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => onSelectTask(task.thread_id)}
                      className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                      title="View details"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => onReplayTask(task.thread_id)}
                      className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                      title="Replay task"
                    >
                      <Play className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => window.open(`/api/history/tasks/${task.thread_id}/export?format=markdown`, '_blank')}
                      className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                      title="Export"
                    >
                      <Download className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Expanded Details */}
                {expandedTask === task.thread_id && (
                  <div className="mt-3 pt-3 border-t border-onsite48-border">
                    {task.summary && (
                      <div className="mb-2">
                        <span className="text-xs text-text-muted">Summary:</span>
                        <p className="text-sm text-text-secondary mt-1">{task.summary}</p>
                      </div>
                    )}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                      <div>
                        <span className="text-text-muted">Thread ID:</span>
                        <span className="ml-1 text-text-secondary font-mono">{task.thread_id.slice(0, 12)}...</span>
                      </div>
                      <div>
                        <span className="text-text-muted">Events:</span>
                        <span className="ml-1 text-text-secondary">{task.events_count}</span>
                      </div>
                      <div>
                        <span className="text-text-muted">Completed:</span>
                        <span className="ml-1 text-text-secondary">
                          {task.completed_at ? formatDate(task.completed_at) : 'N/A'}
                        </span>
                      </div>
                      <div>
                        <span className="text-text-muted">Duration:</span>
                        <span className="ml-1 text-text-secondary">{formatDuration(task.duration_seconds)}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}