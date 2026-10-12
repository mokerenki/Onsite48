"use client";

import { useEffect, useState } from 'react';
import { Loader2, ChevronDown, ChevronUp, DollarSign, Clock } from 'lucide-react';

interface CostMetrics {
  total_cost_usd: number;
  kimi_input_tokens: number;
  kimi_output_tokens: number;
  deepseek_input_tokens: number;
  deepseek_output_tokens: number;
  tool_calls: number;
}

interface Thread {
  thread_id: string;
  task: string;
  status: string;
  final_answer?: string;
  timestamp: string;
  cost_metrics?: CostMetrics;
}

interface HistoryResponse {
  threads: Thread[];
}

export default function TaskHistory() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/tasks/history?limit=50')
      .then(r => r.json())
      .then((data: HistoryResponse) => {
        setThreads(data.threads || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'bg-green-900 text-green-300';
      case 'failed':
        return 'bg-red-900 text-red-300';
      default:
        return 'bg-blue-900 text-blue-300';
    }
  };

  if (loading) return <div className="p-8 text-gray-400">Loading task history...</div>;

  return (
    <div className="max-w-4xl mx-auto p-8 space-y-4">
      <h1 className="text-3xl font-bold text-white">Task History</h1>

      {threads.length === 0 && (
        <div className="text-gray-400">No tasks found.</div>
      )}

      <div className="space-y-3">
        {threads.map((t) => (
          <div key={t.thread_id} className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
            <div className="p-5 flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <p className="text-white font-medium truncate">{t.task}</p>
                <div className="flex flex-wrap items-center gap-3 mt-2 text-sm text-gray-400">
                  <span className={`px-2 py-0.5 rounded text-xs ${getStatusColor(t.status)}`}>
                    {t.status}
                  </span>
                  {t.cost_metrics && (
                    <span className="flex items-center gap-1">
                      <DollarSign className="w-3 h-3" />
                      ${t.cost_metrics.total_cost_usd.toFixed(6)}
                    </span>
                  )}
                  {t.timestamp && (
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {new Date(t.timestamp).toLocaleString()}
                    </span>
                  )}
                </div>
              </div>
              <button
                onClick={() => setExpandedId(expandedId === t.thread_id ? null : t.thread_id)}
                className="text-gray-400 hover:text-white p-1"
              >
                {expandedId === t.thread_id ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
              </button>
            </div>

            {expandedId === t.thread_id && (
              <div className="px-5 pb-5 border-t border-gray-700 pt-4">
                {t.final_answer && (
                  <div className="mb-4">
                    <h4 className="text-sm font-semibold text-gray-400 mb-1">Final Answer</h4>
                    <p className="text-gray-200 whitespace-pre-wrap text-sm bg-gray-900 p-3 rounded-lg">{t.final_answer}</p>
                  </div>
                )}
                {t.cost_metrics && (
                  <div>
                    <h4 className="text-sm font-semibold text-gray-400 mb-2">Cost Metrics</h4>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs text-gray-300">
                      <div className="bg-gray-900 p-2 rounded">Kimi In: {t.cost_metrics.kimi_input_tokens}</div>
                      <div className="bg-gray-900 p-2 rounded">Kimi Out: {t.cost_metrics.kimi_output_tokens}</div>
                      <div className="bg-gray-900 p-2 rounded">DeepSeek In: {t.cost_metrics.deepseek_input_tokens}</div>
                      <div className="bg-gray-900 p-2 rounded">DeepSeek Out: {t.cost_metrics.deepseek_output_tokens}</div>
                      <div className="bg-gray-900 p-2 rounded">Tool Calls: {t.cost_metrics.tool_calls}</div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}