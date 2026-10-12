// frontend/src/components/ConnectorCard.tsx

"use client";

import { useState } from 'react';
import { 
  Check, 
  ExternalLink, 
  Loader2, 
  Plug, 
  Settings, 
  Trash2,
  ChevronDown,
  ChevronRight,
  Wrench,
} from 'lucide-react';
import { Connector } from '@/data/connectors';

interface ConnectorCardProps {
  connector: Connector;
  onConnect: (connectorId: string) => Promise<void>;
  onDisconnect: (connectorId: string) => Promise<void>;
  onConfigure: (connectorId: string) => void;
  isConnected: boolean;
  isConnecting: boolean;
}

export function ConnectorCard({
  connector,
  onConnect,
  onDisconnect,
  onConfigure,
  isConnected,
  isConnecting,
}: ConnectorCardProps) {
  const [expanded, setExpanded] = useState(false);

  const getStatusBadge = () => {
    if (isConnecting) {
      return (
        <span className="flex items-center gap-1 text-xs text-blue-600 bg-blue-50 px-2 py-1 rounded-full">
          <Loader2 className="w-3 h-3 animate-spin" />
          Connecting...
        </span>
      );
    }
    if (isConnected) {
      return (
        <span className="flex items-center gap-1 text-xs text-green-600 bg-green-50 px-2 py-1 rounded-full">
          <Check className="w-3 h-3" />
          Connected
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1 text-xs text-gray-400 bg-gray-50 px-2 py-1 rounded-full">
        <Plug className="w-3 h-3" />
        Disconnected
      </span>
    );
  };

  return (
    <div 
      className={`bg-white rounded-xl border transition-all ${
        isConnected 
          ? 'border-green-200 shadow-sm' 
          : 'border-gray-200 hover:shadow-md hover:border-gray-300'
      }`}
    >
      <div className="p-4">
        <div className="flex items-start gap-4">
          {/* Connector artwork lives under /public/assets/connectors.
              Keep a letter fallback so missing artwork never breaks the card. */}
          <div
            className="w-12 h-12 rounded-xl flex items-center justify-center overflow-hidden text-white text-xl font-bold flex-shrink-0"
            style={{ backgroundColor: connector.color }}
          >
            <img
              src={`/assets/connectors/${connector.id}.svg`}
              alt=""
              className="w-8 h-8 object-contain"
              onError={(event) => {
                event.currentTarget.style.display = 'none';
                const fallback = event.currentTarget.nextElementSibling as HTMLElement | null;
                if (fallback) fallback.style.display = 'block';
              }}
            />
            <span style={{ display: 'none' }}>
              {connector.icon.charAt(0).toUpperCase()}
            </span>
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="font-semibold text-gray-900">{connector.name}</h3>
                <p className="text-sm text-gray-500 line-clamp-2">
                  {connector.description}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1">
                {getStatusBadge()}
              </div>
            </div>

            <div className="flex items-center gap-2 mt-3">
              {!isConnected ? (
                <button
                  onClick={() => onConnect(connector.id)}
                  disabled={isConnecting}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors disabled:opacity-50"
                >
                  {isConnecting ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Plug className="w-4 h-4" />
                  )}
                  Connect
                </button>
              ) : (
                <>
                  <button
                    onClick={() => onDisconnect(connector.id)}
                    className="flex items-center gap-1.5 px-4 py-1.5 bg-red-50 hover:bg-red-100 text-red-600 text-sm rounded-lg transition-colors"
                  >
                    <Trash2 className="w-4 h-4" />
                    Disconnect
                  </button>
                  <button
                    onClick={() => onConfigure(connector.id)}
                    className="flex items-center gap-1.5 px-4 py-1.5 bg-gray-50 hover:bg-gray-100 text-gray-600 text-sm rounded-lg transition-colors"
                  >
                    <Settings className="w-4 h-4" />
                    Configure
                  </button>
                </>
              )}
              
              <button
                onClick={() => setExpanded(!expanded)}
                className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 transition-colors"
              >
                {expanded ? (
                  <ChevronDown className="w-4 h-4" />
                ) : (
                  <ChevronRight className="w-4 h-4" />
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Expanded details - Shows MCP Tools */}
        {expanded && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            {isConnected && connector.tools.length > 0 && (
              <div>
                <div className="flex items-center gap-2 text-sm text-gray-600 mb-2">
                  <Wrench className="w-4 h-4" />
                  <span className="font-medium">Available Tools</span>
                  <span className="text-xs text-gray-400">
                    ({connector.tools.length})
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {connector.tools.map((tool) => (
                    <span 
                      key={tool}
                      className="text-xs bg-gray-100 text-gray-700 px-2.5 py-1 rounded-full"
                    >
                      {tool}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {!isConnected && (
              <p className="text-sm text-gray-400">
                Connect to {connector.name} to access its tools
              </p>
            )}
            <div className="mt-3 flex items-center gap-4 text-xs">
              <span className="text-gray-400">Auth: {connector.authType.toUpperCase()}</span>
              {connector.mcpServer && (
                <span className="text-gray-400">
                  MCP: {connector.mcpServer}
                </span>
              )}
              <a
                href="#"
                className="text-blue-600 hover:underline flex items-center gap-1"
              >
                <ExternalLink className="w-3 h-3" />
                Documentation
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}