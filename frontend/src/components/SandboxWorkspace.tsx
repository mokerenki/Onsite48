// frontend/src/components/SandboxWorkspace.tsx

"use client";

import { useState, useEffect, useCallback } from 'react';
import {
  FolderOpen,
  File,
  FileCode,
  FileText,
  Image,
  Upload,
  Download,
  Trash2,
  Plus,
  X,
  Terminal,
  RefreshCw,
  Loader2,
  Folder,
} from 'lucide-react';

interface FileItem {
  name: string;
  path: string;
  is_directory: boolean;
  size: number;
  permissions: string;
  modified_at: string;
}

interface SandboxWorkspaceProps {
  userId?: string;
  onFileSelect?: (path: string) => void;
  onExecute?: (command: string) => Promise<void>;
}

export function SandboxWorkspace({ userId = "default", onFileSelect, onExecute }: SandboxWorkspaceProps) {
  const [files, setFiles] = useState<FileItem[]>([]);
  const [currentPath, setCurrentPath] = useState("/workspace");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [editingContent, setEditingContent] = useState<string>("");
  const [isEditing, setIsEditing] = useState(false);
  const [showNewFile, setShowNewFile] = useState(false);
  const [newFileName, setNewFileName] = useState("");
  const [newFileContent, setNewFileContent] = useState("");
  const [showTerminal, setShowTerminal] = useState(false);
  const [terminalCommand, setTerminalCommand] = useState("");
  const [terminalOutput, setTerminalOutput] = useState<string[]>([]);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/sandbox/files/list?user_id=${userId}&path=${encodeURIComponent(currentPath)}`
      );
      if (!response.ok) throw new Error('Failed to fetch files');
      const data = await response.json();
      setFiles(data.files || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load files');
    } finally {
      setLoading(false);
    }
  }, [userId, currentPath]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  const fetchFileContent = useCallback(async (path: string) => {
    try {
      const response = await fetch(
        `/api/sandbox/files/read?user_id=${userId}&path=${encodeURIComponent(path)}`
      );
      if (!response.ok) throw new Error('Failed to read file');
      const data = await response.json();
      setFileContent(data.content || "");
      setEditingContent(data.content || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to read file');
    }
  }, [userId]);

  const handleFileClick = (file: FileItem) => {
    if (file.is_directory) {
      setCurrentPath(file.path);
      setSelectedFile(null);
    } else {
      setSelectedFile(file.path);
      fetchFileContent(file.path);
      onFileSelect?.(file.path);
    }
  };

  const handleSaveFile = async () => {
    if (!selectedFile) return;
    try {
      const formData = new FormData();
      formData.append("path", selectedFile);
      formData.append("content", editingContent);
      
      const response = await fetch(
        `/api/sandbox/files/write?user_id=${userId}`,
        { method: 'POST', body: formData }
      );
      if (!response.ok) throw new Error('Failed to save file');
      setIsEditing(false);
      setFileContent(editingContent);
      await fetchFiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save file');
    }
  };

  const handleCreateFile = async () => {
    if (!newFileName) return;
    const path = `${currentPath}/${newFileName}`;
    try {
      const formData = new FormData();
      formData.append("path", path);
      formData.append("content", newFileContent);
      
      const response = await fetch(
        `/api/sandbox/files/write?user_id=${userId}`,
        { method: 'POST', body: formData }
      );
      if (!response.ok) throw new Error('Failed to create file');
      setShowNewFile(false);
      setNewFileName("");
      setNewFileContent("");
      await fetchFiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create file');
    }
  };

  const handleDeleteFile = async (path: string) => {
    if (!confirm(`Delete ${path}?`)) return;
    try {
      const response = await fetch(
        `/api/sandbox/files/delete?user_id=${userId}&path=${encodeURIComponent(path)}`,
        { method: 'DELETE' }
      );
      if (!response.ok) throw new Error('Failed to delete file');
      await fetchFiles();
      if (selectedFile === path) {
        setSelectedFile(null);
        setFileContent("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete file');
    }
  };

  const handleDownloadFile = async (path: string) => {
    window.open(`/api/sandbox/files/download?user_id=${userId}&path=${encodeURIComponent(path)}`, '_blank');
  };

  const handleUploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    const formData = new FormData();
    formData.append("file", file);
    formData.append("user_id", userId);
    formData.append("path", currentPath);
    
    try {
      const response = await fetch('/api/sandbox/files/upload', {
        method: 'POST',
        body: formData,
      });
      if (!response.ok) throw new Error('Failed to upload file');
      await fetchFiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload file');
    }
  };

  const handleTerminalCommand = async () => {
    if (!terminalCommand) return;
    setTerminalOutput(prev => [...prev, `$ ${terminalCommand}`]);
    setTerminalCommand("");
    
    try {
      const formData = new FormData();
      formData.append("command", terminalCommand);
      
      const response = await fetch(
        `/api/sandbox/terminal?user_id=${userId}`,
        { method: 'POST', body: formData }
      );
      if (!response.ok) throw new Error('Command failed');
      const data = await response.json();
      if (data.output) {
        setTerminalOutput(prev => [...prev, data.output]);
      }
      if (data.error) {
        setTerminalOutput(prev => [...prev, `Error: ${data.error}`]);
      }
    } catch (err) {
      setTerminalOutput(prev => [...prev, `Error: ${err}`]);
    }
  };

  const getFileIcon = (file: FileItem) => {
    if (file.is_directory) return <Folder className="w-4 h-4 text-yellow-400" />;
    const ext = file.name.split('.').pop()?.toLowerCase() || '';
    switch (ext) {
      case 'py': return <FileCode className="w-4 h-4 text-blue-400" />;
      case 'js': return <FileCode className="w-4 h-4 text-yellow-400" />;
      case 'html': return <FileCode className="w-4 h-4 text-orange-400" />;
      case 'css': return <FileCode className="w-4 h-4 text-purple-400" />;
      case 'json': return <FileCode className="w-4 h-4 text-green-400" />;
      case 'md': return <FileText className="w-4 h-4 text-gray-400" />;
      case 'txt': return <FileText className="w-4 h-4 text-gray-400" />;
      case 'png':
      case 'jpg':
      case 'jpeg':
      case 'gif': return <Image className="w-4 h-4 text-pink-400" />;
      default: return <File className="w-4 h-4 text-text-muted" />;
    }
  };

  const getPathParts = () => {
    const parts = currentPath.split('/').filter(Boolean);
    const result = [{ name: '/', path: '/' }];
    let path = '';
    for (const part of parts) {
      path += '/' + part;
      result.push({ name: part, path });
    }
    return result;
  };

  return (
    <div className="flex h-full bg-onsite48-background rounded-xl border border-onsite48-border overflow-hidden">
      {/* File Explorer */}
      <div className="w-80 border-r border-onsite48-border flex flex-col">
        <div className="p-3 border-b border-onsite48-border bg-onsite48-surface-light">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-text-secondary">Files</span>
            <div className="flex items-center gap-1">
              <button
                onClick={fetchFiles}
                className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                title="Refresh"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
              <button
                onClick={() => setShowNewFile(true)}
                className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                title="New File"
              >
                <Plus className="w-4 h-4" />
              </button>
              <label className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors cursor-pointer">
                <Upload className="w-4 h-4" />
                <input
                  type="file"
                  className="hidden"
                  onChange={handleUploadFile}
                />
              </label>
              <button
                onClick={() => setShowTerminal(!showTerminal)}
                className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                title="Terminal"
              >
                <Terminal className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

        {/* Breadcrumb */}
        <div className="flex items-center gap-1 p-2 text-xs text-text-muted border-b border-onsite48-border bg-onsite48-surface-light/50">
          {getPathParts().map((part, index) => (
            <button
              key={index}
              onClick={() => setCurrentPath(part.path)}
              className={`hover:text-text-primary transition-colors ${
                index === getPathParts().length - 1 ? 'text-text-primary font-medium' : ''
              }`}
            >
              {part.name}
            </button>
          ))}
        </div>

        {/* File List */}
        <div className="flex-1 overflow-y-auto p-2">
          {loading ? (
            <div className="flex items-center justify-center p-8">
              <Loader2 className="w-6 h-6 animate-spin text-brand-400" />
            </div>
          ) : error ? (
            <div className="text-red-400 text-sm p-4">{error}</div>
          ) : files.length === 0 ? (
            <div className="text-text-muted text-sm p-4 text-center">Empty directory</div>
          ) : (
            <div className="space-y-0.5">
              {files.map((file) => (
                <div
                  key={file.path}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg cursor-pointer transition-colors ${
                    selectedFile === file.path
                      ? 'bg-brand-500/10 text-text-primary'
                      : 'hover:bg-onsite48-surface-hover text-text-secondary'
                  }`}
                  onClick={() => handleFileClick(file)}
                >
                  {getFileIcon(file)}
                  <span className="text-sm flex-1 truncate">{file.name}</span>
                  <span className="text-xs text-text-muted">
                    {file.is_directory ? '' : (file.size / 1024).toFixed(1) + 'KB'}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteFile(file.path);
                    }}
                    className="p-0.5 rounded hover:bg-onsite48-surface-hover text-text-muted hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                  {!file.is_directory && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownloadFile(file.path);
                      }}
                      className="p-0.5 rounded hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary transition-colors"
                    >
                      <Download className="w-3 h-3" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* File Editor / Content */}
      <div className="flex-1 flex flex-col">
        {selectedFile ? (
          <div className="flex-1 flex flex-col">
            <div className="p-3 border-b border-onsite48-border bg-onsite48-surface-light flex items-center justify-between">
              <span className="text-sm text-text-secondary truncate">{selectedFile}</span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setIsEditing(!isEditing)}
                  className="px-3 py-1 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white transition-colors"
                >
                  {isEditing ? 'Cancel' : 'Edit'}
                </button>
                {isEditing && (
                  <button
                    onClick={handleSaveFile}
                    className="px-3 py-1 text-xs rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition-colors"
                  >
                    Save
                  </button>
                )}
                <button
                  onClick={() => handleDeleteFile(selectedFile)}
                  className="p-1.5 rounded-lg hover:bg-onsite48-surface-hover text-text-muted hover:text-red-400 transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {isEditing ? (
                <textarea
                  value={editingContent}
                  onChange={(e) => setEditingContent(e.target.value)}
                  className="w-full h-full bg-onsite48-surface-light rounded-lg p-4 font-mono text-sm text-text-primary border border-onsite48-border focus:border-brand-500 outline-none resize-none"
                  spellCheck={false}
                />
              ) : (
                <pre className="whitespace-pre-wrap text-sm text-text-secondary font-mono">
                  {fileContent || 'Empty file'}
                </pre>
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            Select a file to view or edit
          </div>
        )}
      </div>

      {/* Terminal */}
      {showTerminal && (
        <div className="border-t border-onsite48-border bg-onsite48-surface-light">
          <div className="p-2 border-b border-onsite48-border flex items-center justify-between">
            <span className="text-xs font-medium text-text-secondary">Terminal</span>
            <button
              onClick={() => setShowTerminal(false)}
              className="p-1 rounded hover:bg-onsite48-surface-hover text-text-muted hover:text-text-primary"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
          <div className="h-48 overflow-y-auto p-3 font-mono text-xs">
            {terminalOutput.map((line, i) => (
              <div key={i} className="text-text-secondary whitespace-pre-wrap">{line}</div>
            ))}
            <div className="flex items-center gap-1 mt-1">
              <span className="text-brand-400">$</span>
              <input
                type="text"
                value={terminalCommand}
                onChange={(e) => setTerminalCommand(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleTerminalCommand()}
                className="flex-1 bg-transparent outline-none text-text-primary"
                placeholder="Enter command..."
              />
            </div>
          </div>
        </div>
      )}

      {/* New File Modal */}
      {showNewFile && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-onsite48-background rounded-xl border border-onsite48-border p-6 w-96">
            <h3 className="text-lg font-semibold text-text-primary mb-4">New File</h3>
            <input
              type="text"
              value={newFileName}
              onChange={(e) => setNewFileName(e.target.value)}
              placeholder="File name"
              className="w-full bg-onsite48-surface-light rounded-lg px-3 py-2 text-sm border border-onsite48-border focus:border-brand-500 outline-none mb-3"
            />
            <textarea
              value={newFileContent}
              onChange={(e) => setNewFileContent(e.target.value)}
              placeholder="File content..."
              className="w-full h-32 bg-onsite48-surface-light rounded-lg px-3 py-2 text-sm border border-onsite48-border focus:border-brand-500 outline-none resize-none"
            />
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setShowNewFile(false)}
                className="px-4 py-2 rounded-lg border border-onsite48-border text-text-secondary hover:bg-onsite48-surface-hover transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateFile}
                className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white transition-colors"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}