// frontend/src/components/StatsCards.tsx

"use client";

import { FolderOpen, ClipboardList, History, Wrench, AlertCircle } from 'lucide-react';
import { useEffect, useState } from 'react';

export default function StatsCards() {
  const [taskCount, setTaskCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await fetch('/api/tasks/history?limit=100');
        
        // CRITICAL FIX: Check response.ok
        if (!response.ok) {
          throw new Error(`API returned ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // CRITICAL FIX: Check if threads property exists
        if (data && data.threads && Array.isArray(data.threads)) {
          setTaskCount(data.threads.length);
        } else {
          setTaskCount(0);
          setError('No tasks found');
        }
      } catch (error) {
        console.error('Failed to fetch task count:', error);
        setError(error instanceof Error ? error.message : 'Failed to load stats');
        setTaskCount(0);
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, []);

  const stats = [
    { icon: <FolderOpen className="w-5 h-5" />, label: 'Projects', value: '3 active', color: 'text-blue-400' },
    { 
      icon: <ClipboardList className="w-5 h-5" />, 
      label: 'Tasks', 
      value: loading ? '...' : error ? '⚠️' : `${taskCount || 0} total`, 
      color: error ? 'text-red-400' : 'text-purple-400',
    },
    { 
      icon: <History className="w-5 h-5" />, 
      label: 'History', 
      value: loading ? '...' : error ? '⚠️' : `${Math.min(taskCount || 0, 10)} recent`, 
      color: error ? 'text-red-400' : 'text-green-400',
    },
    { icon: <Wrench className="w-5 h-5" />, label: 'Tools', value: '9 available', color: 'text-orange-400' },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {stats.map((stat, index) => (
        <div 
          key={index} 
          className={`bg-onsite48-surface/50 rounded-xl p-4 border card-hover ${
            error && (stat.label === 'Tasks' || stat.label === 'History') 
              ? 'border-red-500/30' 
              : 'border-onsite48-border'
          }`}
        >
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg bg-onsite48-surface-hover ${stat.color}`}>
              {stat.icon}
            </div>
            <div>
              <p className="text-sm text-text-secondary">{stat.label}</p>
              <p className={`text-lg font-semibold ${stat.color}`}>
                {stat.value}
              </p>
              {error && (stat.label === 'Tasks' || stat.label === 'History') && (
                <p className="text-xs text-red-400 mt-0.5">API error</p>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}