import { clsx } from "clsx";
import {
  FileText,
  Wrench,
  Code,
  Search,
  Brain,
  ArrowRight,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  User,
} from "lucide-react";
import type { Task, AgentName } from "../types";

interface TaskEventsLike {
  timeline: Array<{ event: { type: string; step?: string; agent_id?: string; status?: string; duration_ms?: number } }>;
  awaitingInput: { inputType: string } | null;
  finished: boolean;
  error: string | null;
}

interface AgentFlowDiagramProps {
  events: TaskEventsLike | undefined;
  task: Task;
}

interface StepState {
  status: "pending" | "active" | "completed" | "failed" | "skipped";
  agent: string;
  durationMs?: number;
}

const STEPS = ["spec", "approval", "bootstrap", "work", "review"] as const;

const STEP_CONFIG: Record<
  string,
  { icon: typeof FileText; label: string; agent: string; color: string }
> = {
  spec: {
    icon: FileText,
    label: "Specification",
    agent: "planner",
    color: "text-agent-planner",
  },
  approval: {
    icon: User,
    label: "User Approval",
    agent: "user",
    color: "text-status-awaiting",
  },
  bootstrap: {
    icon: Wrench,
    label: "Environment Setup",
    agent: "bootstrapper",
    color: "text-agent-bootstrapper",
  },
  work: {
    icon: Code,
    label: "Implementation",
    agent: "worker",
    color: "text-agent-worker",
  },
  review: {
    icon: Search,
    label: "Review",
    agent: "reviewer",
    color: "text-agent-reviewer",
  },
};

function deriveStepStates(
  events: TaskEventsLike | undefined,
  task: Task,
): Map<string, StepState> {
  const states = new Map<string, StepState>();

  // Initialize all as pending
  for (const step of STEPS) {
    states.set(step, { status: "pending", agent: STEP_CONFIG[step].agent });
  }

  if (!events) return states;

  // Process timeline events to derive step states
  for (const entry of events.timeline) {
    const { event } = entry;

    if (event.type === "STEP_STARTED") {
      const e = event as { type: string; step: string; agent_id: string };
      const existing = states.get(e.step);
      if (existing) {
        states.set(e.step, { ...existing, status: "active", agent: e.agent_id });
      }
    }

    if (event.type === "STEP_FINISHED") {
      const e = event as { type: string; step: string; status: string; duration_ms: number };
      const existing = states.get(e.step);
      if (existing) {
        const status =
          e.status === "completed"
            ? "completed"
            : e.status === "failed" || e.status === "rejected"
              ? "failed"
              : "completed";
        states.set(e.step, {
          ...existing,
          status: status as StepState["status"],
          durationMs: e.duration_ms,
        });
      }
    }

    // Spec approval inference
    if (event.type === "RUN_AWAITING_INPUT") {
      states.set("approval", {
        status: "active",
        agent: "user",
      });
      const specState = states.get("spec");
      if (specState && specState.status === "active") {
        states.set("spec", { ...specState, status: "completed" });
      }
    }
  }

  // If task is awaiting input for spec_approval, mark approval as active
  if (events.awaitingInput?.inputType === "spec_approval") {
    states.set("approval", { status: "active", agent: "user" });
  }

  // If bootstrap is completed or work started, mark approval as completed
  const bootstrapState = states.get("bootstrap");
  const workState = states.get("work");
  if (
    bootstrapState &&
    (bootstrapState.status === "active" || bootstrapState.status === "completed")
  ) {
    const approvalState = states.get("approval");
    if (approvalState && approvalState.status !== "completed") {
      states.set("approval", { ...approvalState, status: "completed" });
    }
  }

  return states;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

export function AgentFlowDiagram({ events, task }: AgentFlowDiagramProps) {
  const stepStates = deriveStepStates(events, task);

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-4">
        <Brain className="w-4 h-4 text-agent-orchestrator" />
        <h3 className="text-sm font-medium text-zinc-300">Agent Workflow</h3>
        {task.iteration > 1 && (
          <span className="text-xs text-zinc-500 ml-auto">
            Iteration {task.iteration}
          </span>
        )}
      </div>

      {/* Pipeline visualization */}
      <div className="flex items-center gap-1 overflow-x-auto pb-2">
        {STEPS.map((step, idx) => {
          const state = stepStates.get(step)!;
          const config = STEP_CONFIG[step];
          const Icon = config.icon;

          return (
            <div key={step} className="flex items-center gap-1">
              {/* Step node */}
              <div
                className={clsx(
                  "flex flex-col items-center gap-1.5 px-3 py-2 rounded-lg min-w-[100px] transition-all",
                  state.status === "active" &&
                    "bg-surface-3 ring-1 ring-accent/40",
                  state.status === "completed" && "bg-surface-3/50",
                  state.status === "failed" &&
                    "bg-red-500/5 ring-1 ring-red-500/30",
                  state.status === "pending" && "opacity-40",
                )}
              >
                <div className="relative">
                  <Icon
                    className={clsx(
                      "w-5 h-5",
                      state.status === "active"
                        ? config.color
                        : state.status === "completed"
                          ? "text-green-500"
                          : state.status === "failed"
                            ? "text-red-500"
                            : "text-zinc-600",
                    )}
                  />
                  {/* Status overlay */}
                  {state.status === "active" && (
                    <Loader2 className="w-3 h-3 text-accent absolute -bottom-0.5 -right-1 animate-spin" />
                  )}
                  {state.status === "completed" && (
                    <CheckCircle2 className="w-3 h-3 text-green-500 absolute -bottom-0.5 -right-1" />
                  )}
                  {state.status === "failed" && (
                    <XCircle className="w-3 h-3 text-red-500 absolute -bottom-0.5 -right-1" />
                  )}
                </div>

                <span
                  className={clsx(
                    "text-[11px] font-medium text-center leading-tight",
                    state.status === "active"
                      ? "text-zinc-200"
                      : state.status === "completed"
                        ? "text-zinc-400"
                        : "text-zinc-600",
                  )}
                >
                  {config.label}
                </span>

                <span
                  className={clsx(
                    "text-[10px]",
                    state.status === "active"
                      ? config.color
                      : "text-zinc-600",
                  )}
                >
                  {state.agent}
                </span>

                {state.durationMs !== undefined && (
                  <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
                    <Clock className="w-2.5 h-2.5" />
                    {formatDuration(state.durationMs)}
                  </span>
                )}
              </div>

              {/* Arrow between steps */}
              {idx < STEPS.length - 1 && (
                <ArrowRight
                  className={clsx(
                    "w-4 h-4 shrink-0",
                    state.status === "completed"
                      ? "text-zinc-500"
                      : "text-zinc-700",
                  )}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
