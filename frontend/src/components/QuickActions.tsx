// frontend/src/components/QuickActions.tsx

"use client";

import { 
  FileText, 
  Globe, 
  Code, 
  TrendingUp,
  Search,
  FileSpreadsheet,
  Presentation,
  Mail,
  Users,
  Building2,
  BarChart3,
  Shield,
  Sparkles,
  Video,
  MessageSquare,
  LineChart,
  Send
} from 'lucide-react';

interface QuickAction {
  icon: React.ReactNode;
  label: string;
  action: string;
}

interface QuickActionsProps {
  onAction: (action: string) => void;
  onMore?: () => void; // NEW: Optional callback for "More" button
}

export default function QuickActions({ onAction, onMore }: QuickActionsProps) {
  const actions: QuickAction[] = [
    { icon: <TrendingUp className="w-4 h-4" />, label: 'Find TikTok hook', action: 'find_tiktok_hook' },
    { icon: <Sparkles className="w-4 h-4" />, label: 'Generate viral angle', action: 'generate_viral_angle' },
    { icon: <Video className="w-4 h-4" />, label: 'Draft video script', action: 'draft_video_script' },
    { icon: <Search className="w-4 h-4" />, label: 'Analyze competitor ads', action: 'analyze_competitor_ads' },
    { icon: <MessageSquare className="w-4 h-4" />, label: 'Craft high-intent DM', action: 'craft_outreach_dm' },
    { icon: <LineChart className="w-4 h-4" />, label: 'Audit social engagement', action: 'audit_social_engagement' },
    { icon: <FileSpreadsheet className="w-4 h-4" />, label: 'Build campaign brief', action: 'build_campaign_brief' },
    { icon: <Send className="w-4 h-4" />, label: 'Schedule post batch', action: 'schedule_posts' },
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((action, index) => (
        <button
          key={index}
          onClick={() => onAction(action.action)}
          className="flex items-center gap-2 px-4 py-2 bg-onsite48-surface hover:bg-onsite48-surface-hover rounded-lg text-sm text-text-secondary hover:text-text-primary transition-colors border border-onsite48-border hover:border-brand-500/30"
        >
          <span className="text-brand-400">{action.icon}</span>
          <span>{action.label}</span>
        </button>
      ))}
      <button 
        onClick={onMore || (() => {})} // Safe fallback if no handler
        className="px-4 py-2 text-text-muted hover:text-text-secondary rounded-lg text-sm transition-colors hover:bg-onsite48-surface-hover"
      >
        + More
      </button>
    </div>
  );
}