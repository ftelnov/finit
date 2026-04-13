import { clsx } from "clsx";
import {
  Play,
  FastForward,
  CheckCircle2,
  AlertCircle,
  MessageSquare,
  Eye,
  Pause,
  Database,
  Zap,
} from "lucide-react";
import type { TimelineEntry, AgUiEvent } from "../types";
import { useEffect, useRef } from "react";

interface AgentTimelineProps {
  entries: TimelineEntry[];
  streamingText: Map<string, string>;
}

const AGENT_COLORS: Record<string, string> = {
  planner: "border-agent-planner bg-agent-planner/10 text-agent-planner",
  bootstrapper: "border-agent-bootstrapper bg-agent-bootstrapper/10 text-agent-bootstrapper",
  worker: "border-agent-worker bg-agent-worker/10 text-agent-worker",
  reviewer: "border-agent-reviewer bg-agent-reviewer/10 text-agent-reviewer",
  orchestrator: "border-agent-orchestrator bg-agent-orchestrator/10 text-agent-orchestrator",
};

function getEventIcon(event: AgUiEvent) {
  switch (event.type) {
    case "RUN_STARTED":
      return <Play className="w-3 h-3" />;
    case "STEP_STARTED":
      return <FastForward className="w-3 h-3" />;
    case "STEP_FINISHED":
      return <CheckCircle2 className="w-3 h-3" />;
    case "RUN_AWAITING_INPUT":
      return <Pause className="w-3 h-3" />;
    case "TEXT_MESSAGE_START":
    case "TEXT_MESSAGE_CONTENT":
    case "TEXT_MESSAGE_END":
      return <MessageSquare className="w-3 h-3" />;
    case "STATE_SNAPSHOT":
    case "STATE_DELTA":
      return <Database className="w-3 h-3" />;
    case "RUN_FINISHED":
      return <Zap className="w-3 h-3" />;
    case "RUN_ERROR":
      return <AlertCircle className="w-3 h-3" />;
    default:
      return <Eye className="w-3 h-3" />;
  }
}

function getEventColor(event: AgUiEvent): string {
  switch (event.type) {
    case "RUN_STARTED":
      return "text-blue-400";
    case "STEP_STARTED":
      return "text-accent";
    case "STEP_FINISHED": {
      const e = event as { status: string };
      return e.status === "completed" ? "text-green-400" : "text-yellow-400";
    }
    case "RUN_AWAITING_INPUT":
      return "text-status-awaiting";
    case "RUN_FINISHED":
      return "text-green-400";
    case "RUN_ERROR":
      return "text-red-400";
    default:
      return "text-zinc-500";
  }
}

const AGENT_LABELS: Record<string, string> = {
  planner: "Planner",
  bootstrapper: "Bootstrapper",
  worker: "Worker",
  reviewer: "Reviewer",
};

function getEventSummary(event: AgUiEvent): string {
  switch (event.type) {
    case "RUN_STARTED":
      return "Run started";
    case "STEP_STARTED": {
      const e = event as { step: string; agent_id: string };
      const name = AGENT_LABELS[e.step] ?? e.step;
      return `${name} started`;
    }
    case "STEP_FINISHED": {
      const e = event as { step: string; status: string; duration_ms: number };
      const name = AGENT_LABELS[e.step] ?? e.step;
      if (e.duration_ms > 0) {
        const dur =
          e.duration_ms < 1000
            ? `${e.duration_ms}ms`
            : `${(e.duration_ms / 1000).toFixed(1)}s`;
        return `${name} ${e.status} (${dur})`;
      }
      return `${name} ${e.status}`;
    }
    case "RUN_AWAITING_INPUT": {
      const e = event as { input_type: string; question?: string };
      if (e.input_type === "spec_approval") return "Awaiting spec approval";
      return e.question ?? "Awaiting user input";
    }
    case "TEXT_MESSAGE_START":
      return "Streaming message...";
    case "TEXT_MESSAGE_CONTENT":
      return ""; // Don't show individual deltas
    case "TEXT_MESSAGE_END":
      return "Message complete";
    case "STATE_SNAPSHOT":
      return "State snapshot";
    case "STATE_DELTA": {
      const e = event as { path: string };
      return `State update: ${e.path}`;
    }
    case "RUN_FINISHED":
      return "Run completed";
    case "RUN_ERROR": {
      const e = event as { error: string };
      return e.error;
    }
    default:
      return (event as { type: string }).type;
  }
}

function shouldShowEntry(event: AgUiEvent): boolean {
  // Skip TEXT_MESSAGE_CONTENT entries (shown in streaming text instead)
  return event.type !== "TEXT_MESSAGE_CONTENT";
}

export function AgentTimeline({ entries, streamingText }: AgentTimelineProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries.length]);

  const visibleEntries = entries.filter((e) => shouldShowEntry(e.event));

  // Show streaming text
  const streamingEntries = Array.from(streamingText.entries()).filter(
    ([, text]) => text.length > 0,
  );

  return (
    <div className="p-3">
      {visibleEntries.length === 0 && streamingEntries.length === 0 && (
        <div className="text-xs text-zinc-600 text-center py-8">
          Waiting for events...
        </div>
      )}

      <div className="relative">
        {/* Vertical line */}
        {visibleEntries.length > 0 && <div className="timeline-line" />}

        <div className="space-y-2">
          {visibleEntries.map((entry) => {
            const summary = getEventSummary(entry.event);
            if (!summary) return null;

            return (
              <div
                key={entry.id}
                className="relative pl-8 flex items-start gap-2"
              >
                {/* Dot */}
                <div
                  className={clsx(
                    "absolute left-2.5 top-1 w-3 h-3 rounded-full border-2 border-surface-1",
                    getEventColor(entry.event).replace("text-", "bg-"),
                  )}
                />

                {/* Content */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className={clsx("shrink-0", getEventColor(entry.event))}>
                      {getEventIcon(entry.event)}
                    </span>
                    <span className="text-xs text-zinc-300 truncate">
                      {summary}
                    </span>
                  </div>
                  <div className="text-[10px] text-zinc-600 mt-0.5">
                    {entry.timestamp.toLocaleTimeString()}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Streaming text messages */}
      {streamingEntries.length > 0 && (
        <div className="mt-3 space-y-2">
          {streamingEntries.map(([msgId, text]) => (
            <div key={msgId} className="card p-2.5">
              <div className="text-[10px] text-zinc-600 mb-1 font-mono">
                {msgId}
              </div>
              <div className="text-xs text-zinc-300 whitespace-pre-wrap font-mono leading-relaxed">
                {text}
                <span className="inline-block w-1.5 h-3 bg-accent animate-pulse ml-0.5" />
              </div>
            </div>
          ))}
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
