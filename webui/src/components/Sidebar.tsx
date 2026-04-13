import { useStore } from "../stores/store";
import { clsx } from "clsx";
import { Plus, FolderOpen, Circle } from "lucide-react";
import { StatusBadge } from "./StatusBadge";
import { formatDistanceToNow } from "date-fns";

export function Sidebar() {
  const tasks = useStore((s) => s.tasks);
  const workspaces = useStore((s) => s.workspaces);
  const selectedTaskId = useStore((s) => s.selectedTaskId);
  const selectedWorkspaceId = useStore((s) => s.selectedWorkspaceId);
  const selectTask = useStore((s) => s.selectTask);
  const selectWorkspace = useStore((s) => s.selectWorkspace);
  const setCreateDialogOpen = useStore((s) => s.setCreateDialogOpen);
  const agents = useStore((s) => s.agents);

  const filteredTasks = selectedWorkspaceId
    ? tasks.filter(
        (t) =>
          (t.project_id ?? "default") === selectedWorkspaceId,
      )
    : tasks;

  return (
    <div className="h-full flex flex-col">
      {/* New task button */}
      <div className="p-3">
        <button
          onClick={() => setCreateDialogOpen(true)}
          className="btn-primary w-full flex items-center justify-center gap-2"
        >
          <Plus className="w-4 h-4" />
          New Task
        </button>
      </div>

      {/* Workspaces */}
      <div className="px-3 pb-2">
        <div className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-1">
          Workspaces
        </div>
        <div className="space-y-0.5">
          <button
            onClick={() => selectWorkspace(null)}
            className={clsx(
              "w-full text-left px-2 py-1.5 rounded-md text-sm flex items-center gap-2 transition-colors",
              !selectedWorkspaceId
                ? "bg-surface-3 text-zinc-100"
                : "text-zinc-400 hover:text-zinc-200 hover:bg-surface-2",
            )}
          >
            <FolderOpen className="w-3.5 h-3.5" />
            All Tasks
            <span className="ml-auto text-xs text-zinc-600">{tasks.length}</span>
          </button>
          {workspaces.map((ws) => (
            <button
              key={ws.id}
              onClick={() => selectWorkspace(ws.id)}
              className={clsx(
                "w-full text-left px-2 py-1.5 rounded-md text-sm flex items-center gap-2 transition-colors",
                selectedWorkspaceId === ws.id
                  ? "bg-surface-3 text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-surface-2",
              )}
            >
              <FolderOpen className="w-3.5 h-3.5" />
              <span className="truncate">{ws.name}</span>
              <span className="ml-auto text-xs text-zinc-600">
                {ws.taskIds.length}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Divider */}
      <div className="border-t border-surface-4 mx-3" />

      {/* Task list */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <div className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-1">
          Tasks
        </div>
        <div className="space-y-1">
          {filteredTasks.length === 0 && (
            <div className="text-xs text-zinc-600 py-4 text-center">
              No tasks yet
            </div>
          )}
          {filteredTasks.map((task) => (
            <button
              key={task.id}
              onClick={() => selectTask(task.id)}
              className={clsx(
                "w-full text-left px-2.5 py-2 rounded-lg transition-colors group",
                selectedTaskId === task.id
                  ? "bg-accent/10 border border-accent/30"
                  : "hover:bg-surface-2 border border-transparent",
              )}
            >
              <div className="flex items-start gap-2">
                <StatusBadge status={task.status} className="mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-zinc-200 truncate">
                    {task.input.length > 60
                      ? task.input.slice(0, 60) + "..."
                      : task.input}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-xs text-zinc-600">
                      {formatDistanceToNow(new Date(task.created_at), {
                        addSuffix: true,
                      })}
                    </span>
                    {task.iteration > 0 && (
                      <span className="text-xs text-zinc-600">
                        iter {task.iteration}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Agents status */}
      <div className="border-t border-surface-4 p-3">
        <div className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">
          Agents
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {agents.length === 0 && (
            <div className="col-span-2 text-xs text-zinc-600 text-center py-1">
              No agents registered
            </div>
          )}
          {agents.map((agent) => (
            <div
              key={agent.id}
              className="flex items-center gap-1.5 px-2 py-1 rounded bg-surface-2 text-xs"
            >
              <Circle
                className={clsx(
                  "w-2 h-2 fill-current",
                  agent.status === "healthy"
                    ? "text-green-500"
                    : "text-red-500",
                )}
              />
              <span className="text-zinc-400 truncate">{agent.name}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
