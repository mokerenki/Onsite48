"use client";

import { CheckCircle, Clock, AlertCircle, ChevronRight, Loader2 } from 'lucide-react';
import { useState } from 'react';

interface TaskCardProps {
  id: string;
  title: string;
  status: 'completed' | 'running' | 'failed' | 'queued' | string;
  time: string;
  cost?: number;
  onSelect?: (id: string) => void;
}

interface StatusConfig {
  icon: React.ElementType;
  color: string;
  bg: string;
  label: string;
  spin?: boolean;
}

export default function TaskCard({ id, title, status, time, cost, onSelect }: TaskCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  
  // SAFE: Fallback for invalid status
  const getStatusConfig = (status: string): StatusConfig => {
    switch (status) {
      case 'completed':
        return { icon: CheckCircle, color: 'text-green-400', bg: 'bg-green-500/10', label: 'Completed' };
      case 'running':
        return { icon: Loader2, color: 'text-blue-400', bg: 'bg-blue-500/10', label: 'Running', spin: true };
      case 'failed':
        return { icon: AlertCircle, color: 'text-red-400', bg: 'bg-red-500/10', label: 'Failed' };
      case 'queued':
        return { icon: Clock, color: 'text-yellow-400', bg: 'bg-yellow-500/10', label: 'Queued' };
      default:
        // FALLBACK: Show as 'pending' with a neutral style
        return { icon: Clock, color: 'text-gray-400', bg: 'bg-gray-500/10', label: 'Pending' };
    }
  };
  
  const config = getStatusConfig(status);
  const Icon = config.icon;

  return (
    <div 
      onClick={() => onSelect?.(id)}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      className={`flex items-center gap-4 p-4 bg-onsite48-surface/50 rounded-xl border transition-all duration-200 cursor-pointer group ${
        isHovered ? 'border-brand-500/30 bg-onsite48-surface-hover' : 'border-onsite48-border'
      }`}
    >
      <div className={`p-2 rounded-lg ${config.bg} transition-colors duration-200 ${
        isHovered ? 'scale-105' : ''
      }`}>
        <Icon className={`w-4 h-4 ${config.color} ${config.spin ? 'animate-spin' : ''}`} />
      </div>
      
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-text-primary truncate">{title}</p>
        <div className="flex items-center gap-3 mt-0.5">
          <span className={`text-xs px-2 py-0.5 rounded-full ${config.bg} ${config.color}`}>
            {config.label}
          </span>
          <span className="text-xs text-text-muted">{time}</span>
          {cost !== undefined && cost > 0 && (
            <span className="text-xs text-text-muted">${cost.toFixed(4)}</span>
          )}
        </div>
      </div>
      
      <ChevronRight className={`w-4 h-4 transition-all duration-200 ${
        isHovered ? 'text-brand-400 translate-x-0.5' : 'text-text-muted'
      }`} />
    </div>
  );
}