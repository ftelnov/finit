import type { Task } from "../types";
import { StatusBadge } from "./StatusBadge";
import { useStore } from "../stores/store";
import { X, Square, Clock } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

interface TaskHeaderProps {
  task: Task;
}

export function TaskHeader({ task }: TaskHeaderProps) {
  const cancelTask = useStore((s) => s.cancelTask);
  const selectTask = useStore((s) => s.selectTask);

  const isActive =
    task.status === "running" || task.status === "awaiting_input";
  const isTerminal =
    task.status === "completed" ||
    task.status === "failed" ||
    task.status === "cancelled";

  return (
    <div className="border-b border-surface-4 bg-surface-1 px-4 py-3 shrink-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <StatusBadge status={task.status} showLabel />
            <span className="text-xs text-zinc-600 font-mono">
              {task.id}
            </span>
          </div>
          <h2 className="text-sm font-medium text-zinc-200 leading-snug">
            {task.input}
          </h2>
          <div className="flex items-center gap-3 mt-1.5 text-xs text-zinc-500">
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {formatDistanceToNow(new Date(task.created_at), {
                addSuffix: true,
              })}
            </span>
            {task.iteration > 0 && (
              <span>Iteration {task.iteration}</span>
            )}
            {task.workspace_id && (
              <span className="font-mono text-zinc-600">
                {task.workspace_id}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {isActive && (
            <button
              onClick={() => cancelTask(task.id)}
              className="btn-danger flex items-center gap-1.5 text-xs py-1.5 px-3"
            >
              <Square className="w-3 h-3" />
              Cancel
            </button>
          )}
          <button
            onClick={() => selectTask(null)}
            className="p-1.5 hover:bg-surface-3 rounded-md transition-colors"
          >
            <X className="w-4 h-4 text-zinc-400" />
          </button>
        </div>
      </div>
    </div>
  );
}
