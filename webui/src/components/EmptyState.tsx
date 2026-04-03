import { Cpu } from "lucide-react";
import { useStore } from "../stores/store";

export function EmptyState() {
  const setCreateDialogOpen = useStore((s) => s.setCreateDialogOpen);

  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center max-w-md">
        <div className="w-16 h-16 rounded-2xl bg-surface-2 border border-surface-4 flex items-center justify-center mx-auto mb-4">
          <Cpu className="w-8 h-8 text-zinc-600" />
        </div>
        <h2 className="text-lg font-medium text-zinc-300 mb-2">
          No task selected
        </h2>
        <p className="text-sm text-zinc-500 mb-6">
          Select a task from the sidebar or create a new one to see the agent
          workflow and live progress.
        </p>
        <button
          onClick={() => setCreateDialogOpen(true)}
          className="btn-primary"
        >
          Create New Task
        </button>
      </div>
    </div>
  );
}
