import { clsx } from "clsx";
import type { TaskStatus } from "../types";

const STATUS_CONFIG: Record<
  TaskStatus,
  { color: string; label: string; animate?: boolean }
> = {
  created: { color: "bg-status-created", label: "Created" },
  running: { color: "bg-status-running", label: "Running", animate: true },
  awaiting_input: {
    color: "bg-status-awaiting",
    label: "Awaiting Input",
    animate: true,
  },
  completed: { color: "bg-status-completed", label: "Completed" },
  failed: { color: "bg-status-failed", label: "Failed" },
  escalated: { color: "bg-status-escalated", label: "Escalated" },
  cancelled: { color: "bg-status-cancelled", label: "Cancelled" },
};

interface StatusBadgeProps {
  status: TaskStatus;
  className?: string;
  showLabel?: boolean;
}

export function StatusBadge({
  status,
  className,
  showLabel = false,
}: StatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.created;

  return (
    <div className={clsx("flex items-center gap-1.5", className)}>
      <div
        className={clsx(
          "status-dot",
          config.color,
          config.animate && "animate-pulse-dot",
        )}
      />
      {showLabel && (
        <span className="text-xs text-zinc-400">{config.label}</span>
      )}
    </div>
  );
}
