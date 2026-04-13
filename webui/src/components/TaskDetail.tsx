import { useStore } from "../stores/store";
import { useAgUiConnection } from "../hooks/useAgUi";
import { AgentTimeline } from "./AgentTimeline";
import { SpecApproval } from "./SpecApproval";
import { UserInputPanel } from "./UserInputPanel";
import { TaskHeader } from "./TaskHeader";
import { AgentFlowDiagram } from "./AgentFlowDiagram";
import { clsx } from "clsx";

interface TaskDetailProps {
  taskId: string;
}

export function TaskDetail({ taskId }: TaskDetailProps) {
  useAgUiConnection(taskId);

  const task = useStore((s) => s.tasks.find((t) => t.id === taskId));
  const events = useStore((s) => s.taskEvents.get(taskId));

  if (!task) {
    return (
      <div className="h-full flex items-center justify-center text-zinc-500">
        Task not found
      </div>
    );
  }

  const awaitingInput = events?.awaitingInput;
  const isSpecApproval = awaitingInput?.inputType === "spec_approval";
  const isQuestion = awaitingInput && !isSpecApproval;

  return (
    <div className="h-full flex flex-col">
      <TaskHeader task={task} />

      <div className="flex-1 overflow-hidden flex">
        {/* Left panel: Agent flow + interaction */}
        <div className="flex-1 overflow-y-auto">
          <div className="p-4 space-y-4">
            {/* Agent flow visualization */}
            <AgentFlowDiagram events={events} task={task} />

            {/* Spec approval panel */}
            {isSpecApproval && (
              <SpecApproval
                taskId={taskId}
                spec={awaitingInput.spec}
                options={awaitingInput.options}
              />
            )}

            {/* Generic user question panel */}
            {isQuestion && (
              <UserInputPanel
                taskId={taskId}
                question={awaitingInput.question}
                options={awaitingInput.options}
              />
            )}

            {/* Error display */}
            {(task.status === "failed" || events?.error) && (
              <div className="card p-4 border-red-500/30">
                <div className="text-sm font-medium text-red-400 mb-1">
                  Task Failed
                </div>
                <div className="text-sm text-zinc-400">
                  {events?.error ?? task.error ?? "An unknown error occurred"}
                </div>
              </div>
            )}

            {/* Completed state */}
            {task.status === "completed" && (
              <div className="card p-4 border-accent/30">
                <div className="text-sm font-medium text-accent mb-1">
                  Task Completed
                </div>
                <div className="text-sm text-zinc-400">
                  The agent workflow finished successfully.
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right panel: Timeline */}
        <div
          className={clsx(
            "w-80 border-l border-surface-4 bg-surface-1 overflow-y-auto shrink-0",
          )}
        >
          <div className="p-3 border-b border-surface-4">
            <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
              Event Timeline
            </h3>
          </div>
          <AgentTimeline
            entries={events?.timeline ?? []}
            streamingText={events?.streamingText ?? new Map()}
          />
        </div>
      </div>
    </div>
  );
}
