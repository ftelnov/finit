import { useState } from "react";
import { X } from "lucide-react";
import { useStore } from "../stores/store";

interface CreateTaskDialogProps {
  onClose: () => void;
}

export function CreateTaskDialog({ onClose }: CreateTaskDialogProps) {
  const [input, setInput] = useState("");
  const [projectId, setProjectId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const createTask = useStore((s) => s.createTask);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;

    setLoading(true);
    setError(null);
    try {
      await createTask(input.trim(), projectId.trim() || undefined);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Dialog */}
      <div className="relative bg-surface-2 border border-surface-4 rounded-xl shadow-2xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between p-4 border-b border-surface-4">
          <h2 className="text-base font-medium text-zinc-200">
            Create New Task
          </h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-surface-3 rounded-md transition-colors"
          >
            <X className="w-4 h-4 text-zinc-400" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm text-zinc-400 mb-1.5">
              Task Description
            </label>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Describe what you want the agents to build..."
              className="input-field min-h-[120px] resize-y"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm text-zinc-400 mb-1.5">
              Workspace / Project ID
              <span className="text-zinc-600 ml-1">(optional)</span>
            </label>
            <input
              type="text"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              placeholder="e.g. my-project"
              className="input-field"
            />
          </div>

          {error && (
            <div className="text-sm text-red-400 bg-red-500/10 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary">
              Cancel
            </button>
            <button
              type="submit"
              disabled={!input.trim() || loading}
              className="btn-primary"
            >
              {loading ? "Creating..." : "Create Task"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
