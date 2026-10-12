import { useState, useEffect, useCallback } from 'react';
import { useForm, Controller } from 'react-hook-form';
import { yupResolver } from '@hookform/resolvers/yup';
import * as yup from 'yup';
import YAML from 'js-yaml';
import CodeMirror, { basicSetup } from '@uiw/react-codemirror';
import { yaml } from '@codemirror/lang-yaml';

// Define a basic schema for validation (can be more detailed)
const heartbeatConfigSchema = yup.object().shape({
  scheduler: yup.object().shape({
    interval_seconds: yup.number().integer().min(10).required(),
    active_hours: yup.object().shape({
      start: yup.string().matches(/^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$/).required(),
      end: yup.string().matches(/^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$/).required(),
    }).required(),
  }).required(),
  probes: yup.object().required(),
  policy_rules: yup.array().of(yup.object()).required(),
});

interface HeartbeatConfig {
  scheduler: { interval_seconds: number; active_hours: { start: string; end: string }; timezone?: string };
  probes: { [key: string]: any };
  policy_rules: any[];
  authorized_actions?: string[];
  _rawYaml?: string;
}

interface HeartbeatStatus {
  daemon_running: boolean;
  scheduler_running: boolean;
  probes_configured_count: number;
  last_probe_runs: { [key: string]: string };
  config_last_loaded: string;
}

export default function HeartbeatPage() {
  const [configYaml, setConfigYaml] = useState<string>('');
  const [heartbeatStatus, setHeartbeatStatus] = useState<HeartbeatStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);

  const { handleSubmit, control, reset, formState: { errors } } = useForm<HeartbeatConfig>({
    resolver: yupResolver(heartbeatConfigSchema),
  });

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/heartbeat/config');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data: HeartbeatConfig = await response.json();
      setConfigYaml(YAML.dump(data, { indent: 2 }));
      reset(data); // Reset form with fetched data
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [reset]);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/heartbeat/status');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data: HeartbeatStatus = await response.json();
      setHeartbeatStatus(data);
    } catch (e: any) {
      console.error("Failed to fetch heartbeat status:", e);
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
    fetchStatus();
    const statusInterval = setInterval(fetchStatus, 5000); // Refresh status every 5 seconds
    return () => clearInterval(statusInterval);
  }, [fetchConfig, fetchStatus]);

  const handleSave = async (data: HeartbeatConfig) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/heartbeat/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
      }
      await response.json();
      setIsEditing(false);
      fetchConfig(); // Re-fetch to ensure consistency and update UI
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onYamlChange = (value: string) => {
    setConfigYaml(value);
    try {
      const parsedConfig = YAML.load(value) as HeartbeatConfig;
      reset(parsedConfig); // Update form state from YAML editor
    } catch (e) {
      // YAML parsing error, form validation will catch schema issues
      console.error("YAML parsing error:", e);
    }
  };

  if (loading && !configYaml) return <div className="p-8 text-center">Loading Heartbeat Configuration...</div>;
  if (error && !configYaml) return <div className="p-8 text-red-500 text-center">Error: {error}</div>;

  return (
    <div className="max-w-6xl mx-auto p-8">
      <h1 className="text-3xl font-bold mb-6 text-white">Heartbeat Configuration & Status</h1>

      {heartbeatStatus && (
        <div className="bg-gray-800 rounded-xl p-6 border border-gray-700 mb-6">
          <h2 className="text-xl font-semibold mb-4 text-white">Operational Status</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 text-gray-300">
            <div><strong>Daemon Running:</strong> <span className={heartbeatStatus.daemon_running ? 'text-green-500' : 'text-red-500'}>{heartbeatStatus.daemon_running ? 'Yes' : 'No'}</span></div>
            <div><strong>Scheduler Running:</strong> <span className={heartbeatStatus.scheduler_running ? 'text-green-500' : 'text-red-500'}>{heartbeatStatus.scheduler_running ? 'Yes' : 'No'}</span></div>
            <div><strong>Probes Configured:</strong> {heartbeatStatus.probes_configured_count}</div>
            <div><strong>Config Last Loaded:</strong> {heartbeatStatus.config_last_loaded}</div>
            <div className="col-span-full mt-2">
              <strong>Last Probe Runs:</strong>
              <ul className="list-disc list-inside ml-4">
                {Object.entries(heartbeatStatus.last_probe_runs).map(([probeName, timestamp]) => (
                  <li key={probeName}>{probeName}: {new Date(timestamp).toLocaleString()}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700 mb-6">
        <h2 className="text-xl font-semibold mb-4 text-white">Configuration Editor</h2>
        {error && <div className="text-red-500 mb-4">Error: {error}</div>}
        
        <form onSubmit={handleSubmit(handleSave)}>
          <div className="mb-4">
            <label htmlFor="config-editor" className="block text-gray-300 text-sm font-bold mb-2">Edit YAML Configuration:</label>
            <Controller
              name="_rawYaml"
              control={control}
              render={({ field }) => (
                <CodeMirror
                  value={configYaml}
                  height="400px"
                  extensions={[basicSetup(), yaml()]}
                  onChange={onYamlChange}
                  theme="dark"
                />
              )}
            />
            {errors._rawYaml && <p className="text-red-500 text-xs italic mt-2">Invalid YAML or schema mismatch.</p>}
          </div>

          <div className="flex gap-4">
            <button
              type="submit"
              className="px-6 py-3 bg-green-600 hover:bg-green-700 rounded-lg font-semibold text-white"
              disabled={loading}
            >
              {loading ? 'Saving...' : 'Save Configuration'}
            </button>
            <button
              type="button"
              onClick={() => {
                setIsEditing(false);
                fetchConfig(); // Revert changes
              }}
              className="px-6 py-3 bg-gray-600 hover:bg-gray-700 rounded-lg font-semibold text-white"
              disabled={loading}
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}