// frontend/src/components/StateInspector.tsx

"use client";

import { useState, useEffect } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, Loader2, Circle, Edit, X } from 'lucide-react';

interface Step {
  step_id?: string | number;
  description: string;
  tool_name?: string | null;
  tool_args?: Record<string, any>;
}

interface StepResult {
  step: string;
  tool?: string | null;
  output: string;
  step_index: number;
}

interface StateInspectorProps {
  threadId: string;
  onModifyStep?: (threadId: string, stepIndex: number, newStep: Step) => Promise<void>;
}

export function StateInspector({ threadId, onModifyStep }: StateInspectorProps) {
  const [state, setState] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [editingStep, setEditingStep] = useState<number | null>(null);
  const [editData, setEditData] = useState<Partial<Step>>({});

  useEffect(() => {
    fetchState();
    const interval = setInterval(fetchState, 5000);
    return () => clearInterval(interval);
  }, [threadId]);

  const fetchState = async () => {
    try {
      const response = await fetch(`/api/tasks/${threadId}/state`);
      if (!response.ok) throw new Error('Failed to fetch state');
      const data = await response.json();
      setState(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch state');
    } finally {
      setLoading(false);
    }
  };

  const handleEditStep = (stepIndex: number) => {
    const step = state?.plan?.[stepIndex];
    if (step) {
      setEditingStep(stepIndex);
      setEditData(step);
    }
  };

  const handleSaveEdit = async () => {
    if (editingStep === null || !onModifyStep) return;
    try {
      await onModifyStep(threadId, editingStep, editData as Step);
      setEditingStep(null);
      setEditData({});
      await fetchState();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to modify step');
    }
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
        Error loading state: {error}
      </div>
    );
  }

  if (!state) {
    return (
      <div className="p-4 text-text-muted text-sm">
        No state available for this task.
      </div>
    );
  }

  const plan = state.plan || [];
  const results = state.results || [];
  const currentStep = state.current_step || 0;

  return (
    <div className="space-y-4">
      {/* Task Info */}
      <div className="bg-onsite48-surface-light rounded-lg p-4">
        <h3 className="text-sm font-semibold text-text-secondary mb-2">Task</h3>
        <p className="text-text-primary">{state.task || 'Untitled'}</p>
        <div className="flex items-center gap-4 mt-2 text-xs text-text-muted">
          <span>Status: {state.status || 'pending'}</span>
          <span>Steps: {plan.length}</span>
          <span>Completed: {results.length}</span>
          {state.paused_at && (
            <span>Paused at: {new Date(state.paused_at).toLocaleString()}</span>
          )}
        </div>
      </div>

      {/* Plan Steps */}
      <div>
        <h3 className="text-sm font-semibold text-text-secondary mb-2">Plan</h3>
        <div className="space-y-2">
          {plan.map((step: Step, index: number) => {
            const result = results.find((r: StepResult) => r.step_index === index);
            const isCompleted = !!result;
            const isCurrent = index === currentStep && !isCompleted;
            const isExpanded = expandedStep === index;

            return (
              <div
                key={index}
                className={`rounded-lg border ${
                  isCompleted ? 'border-green-500/20 bg-green-500/5' :
                  isCurrent ? 'border-brand-500/30 bg-brand-500/5' :
                  'border-onsite48-border'
                } transition-colors`}
              >
                <div
                  className="flex items-center gap-2 p-3 cursor-pointer hover:bg-onsite48-surface-hover"
                  onClick={() => setExpandedStep(isExpanded ? null : index)}
                >
                  {isCompleted ? (
                    <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
                  ) : isCurrent ? (
                    <Loader2 className="w-4 h-4 animate-spin text-brand-400 shrink-0" />
                  ) : (
                    <Circle className="w-4 h-4 text-text-muted shrink-0" />
                  )}
                  <span className={`text-sm flex-1 ${isCompleted ? 'text-text-primary' : isCurrent ? 'text-brand-300' : 'text-text-secondary'}`}>
                    {step.description}
                  </span>
                  {step.tool_name && (
                    <span className="text-xs bg-onsite48-surface-hover px-2 py-0.5 rounded text-text-muted">
                      {step.tool_name}
                    </span>
                  )}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleEditStep(index);
                    }}
                    className="p-1 rounded hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                  >
                    <Edit className="w-3 h-3" />
                  </button>
                  {isExpanded ? (
                    <ChevronDown className="w-4 h-4 text-text-muted" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-text-muted" />
                  )}
                </div>

                {isExpanded && (
                  <div className="p-3 border-t border-onsite48-border">
                    {step.tool_args && (
                      <div className="text-xs">
                        <span className="text-text-muted">Arguments:</span>
                        <pre className="mt-1 bg-onsite48-surface-hover p-2 rounded overflow-auto">
                          {JSON.stringify(step.tool_args, null, 2)}
                        </pre>
                      </div>
                    )}
                    {result && (
                      <div className="mt-2">
                        <span className="text-xs text-text-muted">Output:</span>
                        <pre className="mt-1 text-xs bg-onsite48-surface-hover p-2 rounded overflow-auto max-h-40">
                          {result.output}
                        </pre>
                      </div>
                    )}
                  </div>
                )}

                {/* Edit Modal */}
                {editingStep === index && (
                  <div className="p-4 border-t border-onsite48-border bg-onsite48-surface-light">
                    <h4 className="text-sm font-medium text-text-primary mb-2">Edit Step</h4>
                    <div className="space-y-2">
                      <input
                        type="text"
                        value={editData.description || ''}
                        onChange={(e) => setEditData({ ...editData, description: e.target.value })}
                        placeholder="Step description"
                        className="w-full bg-onsite48-surface rounded-lg px-3 py-2 text-sm border border-onsite48-border focus:border-brand-500 outline-none"
                      />
                      <input
                        type="text"
                        value={editData.tool_name || ''}
                        onChange={(e) => setEditData({ ...editData, tool_name: e.target.value || null })}
                        placeholder="Tool name (optional)"
                        className="w-full bg-onsite48-surface rounded-lg px-3 py-2 text-sm border border-onsite48-border focus:border-brand-500 outline-none"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={handleSaveEdit}
                          className="px-3 py-1 bg-brand-600 rounded-lg text-sm hover:bg-brand-700 transition-colors"
                        >
                          Save
                        </button>
                        <button
                          onClick={() => {
                            setEditingStep(null);
                            setEditData({});
                          }}
                          className="px-3 py-1 border border-onsite48-border rounded-lg text-sm hover:bg-onsite48-surface-hover transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Cost Metrics */}
      {state.cost_metrics && (
        <div className="bg-onsite48-surface-light rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text-secondary mb-2">Cost Metrics</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
            <div>
              <span className="text-text-muted">Total:</span>
              <span className="ml-2 text-text-primary">${(state.cost_metrics.total_cost_usd || 0).toFixed(6)}</span>
            </div>
            <div>
              <span className="text-text-muted">Tool Calls:</span>
              <span className="ml-2 text-text-primary">{state.cost_metrics.tool_calls || 0}</span>
            </div>
            <div>
              <span className="text-text-muted">Kimi:</span>
              <span className="ml-2 text-text-primary">
                {(state.cost_metrics.kimi_input_tokens || 0) + (state.cost_metrics.kimi_output_tokens || 0)}
              </span>
            </div>
            <div>
              <span className="text-text-muted">DeepSeek:</span>
              <span className="ml-2 text-text-primary">
                {(state.cost_metrics.deepseek_input_tokens || 0) + (state.cost_metrics.deepseek_output_tokens || 0)}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}