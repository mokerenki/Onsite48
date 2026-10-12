"use client";

import { useState, useEffect } from 'react';
import { Check, X, Loader2, RefreshCw, Settings, Trash2, Database, ChevronDown } from 'lucide-react';

interface Integration {
  id: string;
  name: string;
  type: 'mcp' | 'custom';
  status: 'connected' | 'disconnected' | 'connecting' | 'error';
  last_sync?: string;
  config: Record<string, any>;
  description?: string;
}

interface Tool {
  id: string;
  integration: string;
  name: string;
  description: string;
  input_schema: Record<string, any>;
  status: string;
}

export default function IntegrationPanel() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [selectedIntegration, setSelectedIntegration] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedTool, setExpandedTool] = useState<string | null>(null);

  useEffect(() => {
    fetchIntegrations();
    fetchTools();
  }, []);

  const fetchIntegrations = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/integrations');
      if (!response.ok) throw new Error('Failed to fetch integrations');
      const data = await response.json();
      setIntegrations(data.integrations || []);
    } catch (error) {
      console.error('Failed to fetch integrations:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchTools = async () => {
    try {
      const response = await fetch('/api/integrations/tools');
      if (!response.ok) throw new Error('Failed to fetch tools');
      const data = await response.json();
      setTools(data.tools || []);
    } catch (error) {
      console.error('Failed to fetch tools:', error);
    }
  };

  const syncIntegration = async (id: string) => {
    try {
      const response = await fetch(`/api/integrations/${id}/sync`, { method: 'POST' });
      if (!response.ok) throw new Error('Sync failed');
      await fetchIntegrations();
      await fetchTools();
    } catch (error) {
      console.error('Sync failed:', error);
    }
  };

  const filteredTools = selectedIntegration 
    ? tools.filter(t => t.integration === selectedIntegration)
    : tools;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Plugins & Integrations</h1>
        <button 
          onClick={() => fetchIntegrations()}
          className="flex items-center gap-2 px-4 py-2 bg-onsite48-surface-hover rounded-lg text-sm"
        >
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      {/* Integration Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
        {integrations.map((integration) => (
          <IntegrationCard
            key={integration.id}
            integration={integration}
            onSync={() => syncIntegration(integration.id)}
            onSelect={() => setSelectedIntegration(
              selectedIntegration === integration.id ? null : integration.id
            )}
            isSelected={selectedIntegration === integration.id}
          />
        ))}
      </div>

      {/* Tools Grid */}
      {filteredTools.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-white mb-4">
            {selectedIntegration ? `Tools from ${selectedIntegration}` : 'All Tools'}
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {filteredTools.map((tool) => (
              <ToolCard
                key={tool.id}
                tool={tool}
                expanded={expandedTool === tool.id}
                onToggle={() => setExpandedTool(expandedTool === tool.id ? null : tool.id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function IntegrationCard({ integration, onSync, onSelect, isSelected }: { 
  integration: Integration; 
  onSync: () => void; 
  onSelect: () => void;
  isSelected: boolean;
}) {
  return (
    <div 
      className={`bg-onsite48-surface rounded-xl border p-4 transition-all cursor-pointer ${
        isSelected ? 'border-brand-500' : 'border-onsite48-border'
      }`}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-onsite48-surface-hover flex items-center justify-center">
            <Database className="w-5 h-5 text-text-secondary" />
          </div>
          <div>
            <h3 className="font-medium text-text-primary">{integration.name}</h3>
            <p className="text-xs text-text-muted capitalize">{integration.type}</p>
          </div>
        </div>
        <span className={`px-2 py-0.5 rounded-full text-xs ${
          integration.status === 'connected' ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'
        }`}>
          {integration.status}
        </span>
      </div>
      
      {integration.description && (
        <p className="text-sm text-text-secondary mb-3">{integration.description}</p>
      )}
      
      <div className="flex gap-2">
        <button 
          onClick={(e) => { e.stopPropagation(); onSync(); }}
          className="flex items-center gap-1 px-3 py-1.5 bg-onsite48-surface-hover rounded-lg text-xs hover:bg-onsite48-border transition-colors"
        >
          <RefreshCw className="w-3 h-3" /> Sync
        </button>
        <button 
          onClick={(e) => e.stopPropagation()}
          className="flex items-center gap-1 px-3 py-1.5 bg-onsite48-surface-hover rounded-lg text-xs hover:bg-onsite48-border transition-colors"
        >
          <Settings className="w-3 h-3" /> Configure
        </button>
      </div>
    </div>
  );
}

function ToolCard({ tool, expanded, onToggle }: { 
  tool: Tool; 
  expanded: boolean; 
  onToggle: () => void;
}) {
  return (
    <div 
      className="bg-onsite48-surface rounded-xl border border-onsite48-border overflow-hidden transition-all"
    >
      <div 
        className="p-4 cursor-pointer hover:bg-onsite48-surface-hover"
        onClick={onToggle}
      >
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <h3 className="font-medium text-text-primary truncate">{tool.name}</h3>
            <p className="text-sm text-text-secondary truncate">{tool.description || 'No description'}</p>
          </div>
          <div className="flex items-center gap-2 ml-2">
            <span className={`px-2 py-0.5 rounded-full text-xs ${
              tool.status === 'connected' ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'
            }`}>
              {tool.status}
            </span>
            <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} />
          </div>
        </div>
      </div>
      
      {expanded && (
        <div className="p-4 border-t border-onsite48-border bg-onsite48-surface-light/50">
          <div className="text-xs text-text-muted mb-2">Input Schema:</div>
          <pre className="text-xs bg-onsite48-surface-hover p-3 rounded-lg overflow-auto max-h-60">
            {JSON.stringify(tool.input_schema, null, 2)}
          </pre>
          <div className="mt-3 flex gap-2">
            <button className="text-xs text-brand-400 hover:text-brand-300">
              Test Tool
            </button>
            <button className="text-xs text-text-muted hover:text-text-secondary">
              View Docs
            </button>
          </div>
        </div>
      )}
    </div>
  );
}