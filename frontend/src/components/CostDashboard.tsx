"use client";

import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

interface CostMetrics {
  total_cost_usd: number;
  kimi_input_tokens: number;
  kimi_output_tokens: number;
  deepseek_input_tokens: number;
  deepseek_output_tokens: number;
  tool_calls: number;
}

interface TaskRecord {
  thread_id: string;
  task: string;
  status: string;
  total_cost_usd: number;
  timestamp: string;
}

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'];

export default function CostDashboard() {
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [totals, setTotals] = useState<CostMetrics | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/tasks/history?limit=50')
      .then(r => r.json())
      .then(data => {
        setTasks(data.threads || []);
        const agg = (data.threads || []).reduce((acc: any, t: any) => {
          const m = t.cost_metrics || {};
          acc.total_cost_usd += m.total_cost_usd || 0;
          acc.kimi_input_tokens += m.kimi_input_tokens || 0;
          acc.kimi_output_tokens += m.kimi_output_tokens || 0;
          acc.deepseek_input_tokens += m.deepseek_input_tokens || 0;
          acc.deepseek_output_tokens += m.deepseek_output_tokens || 0;
          acc.tool_calls += m.tool_calls || 0;
          return acc;
        }, { total_cost_usd: 0, kimi_input_tokens: 0, kimi_output_tokens: 0, deepseek_input_tokens: 0, deepseek_output_tokens: 0, tool_calls: 0 });
        setTotals(agg);
        setLoading(false);
      });
  }, []);

  const tokenData = totals ? [
    { name: 'Kimi In', value: totals.kimi_input_tokens },
    { name: 'Kimi Out', value: totals.kimi_output_tokens },
    { name: 'DeepSeek In', value: totals.deepseek_input_tokens },
    { name: 'DeepSeek Out', value: totals.deepseek_output_tokens },
  ] : [];

  if (loading) return <div className="p-8 text-gray-400">Loading cost data...</div>;

  return (
    <div className="max-w-6xl mx-auto p-8 space-y-8">
      <h1 className="text-3xl font-bold text-white">Cost Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <div className="text-sm text-gray-400">Total Cost</div>
          <div className="text-2xl font-bold text-green-400">${totals?.total_cost_usd.toFixed(4)}</div>
        </div>
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <div className="text-sm text-gray-400">Kimi Tokens</div>
          <div className="text-2xl font-bold text-blue-400">{(totals?.kimi_input_tokens || 0) + (totals?.kimi_output_tokens || 0)}</div>
        </div>
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <div className="text-sm text-gray-400">DeepSeek Tokens</div>
          <div className="text-2xl font-bold text-yellow-400">{(totals?.deepseek_input_tokens || 0) + (totals?.deepseek_output_tokens || 0)}</div>
        </div>
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <div className="text-sm text-gray-400">Tool Calls</div>
          <div className="text-2xl font-bold text-purple-400">{totals?.tool_calls}</div>
        </div>
      </div>

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700">
        <h2 className="text-xl font-semibold text-white mb-4">Token Distribution</h2>
        <ResponsiveContainer width="100%" height={250}>
          <PieChart>
            <Pie data={tokenData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80}>
              {tokenData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
            </Pie>
            <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', color: '#fff' }} />
          </PieChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700">
        <h2 className="text-xl font-semibold text-white mb-4">Recent Tasks</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-gray-300">
            <thead className="text-gray-400 border-b border-gray-700">
              <tr>
                <th className="pb-2">Task</th>
                <th className="pb-2">Status</th>
                <th className="pb-2">Cost</th>
                <th className="pb-2">Time</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map(t => (
                <tr key={t.thread_id} className="border-b border-gray-700/50">
                  <td className="py-2 max-w-xs truncate">{t.task}</td>
                  <td className="py-2">
                    <span className={`px-2 py-0.5 rounded text-xs ${t.status === 'completed' ? 'bg-green-900 text-green-300' : t.status === 'failed' ? 'bg-red-900 text-red-300' : 'bg-blue-900 text-blue-300'}`}>
                      {t.status}
                    </span>
                  </td>
                  <td className="py-2">${(t.total_cost_usd || 0).toFixed(4)}</td>
                  <td className="py-2 text-gray-500">{t.timestamp ? new Date(t.timestamp).toLocaleString() : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}