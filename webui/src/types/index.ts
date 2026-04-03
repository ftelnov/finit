// ── Task types matching orchestrator API ──

export type TaskStatus =
  | "created"
  | "running"
  | "awaiting_input"
  | "completed"
  | "failed"
  | "escalated"
  | "cancelled";

export interface Task {
  id: string;
  project_id: string | null;
  input: string;
  status: TaskStatus;
  workspace_id: string | null;
  iteration: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

// ── Agent types ──

export interface Agent {
  id: string;
  name: string;
  url: string;
  status: "healthy" | "unhealthy";
  agent_card: Record<string, unknown>;
  last_health_check: string | null;
  registered_at: string | null;
}

// ── AG-UI event types matching agui.rs ──

export type AgUiEventType =
  | "RUN_STARTED"
  | "STEP_STARTED"
  | "STEP_FINISHED"
  | "RUN_AWAITING_INPUT"
  | "TEXT_MESSAGE_START"
  | "TEXT_MESSAGE_CONTENT"
  | "TEXT_MESSAGE_END"
  | "STATE_SNAPSHOT"
  | "STATE_DELTA"
  | "RUN_FINISHED"
  | "RUN_ERROR";

export interface RunStartedEvent {
  type: "RUN_STARTED";
  task_id: string;
  timestamp: string;
}

export interface StepStartedEvent {
  type: "STEP_STARTED";
  step: string;
  agent_id: string;
}

export interface StepFinishedEvent {
  type: "STEP_FINISHED";
  step: string;
  status: string;
  duration_ms: number;
}

export interface RunAwaitingInputEvent {
  type: "RUN_AWAITING_INPUT";
  input_type: string;
  spec?: Record<string, unknown>;
  question?: string;
  options?: string[];
}

export interface TextMessageStartEvent {
  type: "TEXT_MESSAGE_START";
  message_id: string;
  role: string;
}

export interface TextMessageContentEvent {
  type: "TEXT_MESSAGE_CONTENT";
  message_id: string;
  delta: string;
}

export interface TextMessageEndEvent {
  type: "TEXT_MESSAGE_END";
  message_id: string;
}

export interface StateSnapshotEvent {
  type: "STATE_SNAPSHOT";
  data: Record<string, unknown>;
}

export interface StateDeltaEvent {
  type: "STATE_DELTA";
  path: string;
  value: unknown;
}

export interface RunFinishedEvent {
  type: "RUN_FINISHED";
  result: string;
  artifacts?: Array<Record<string, unknown>>;
}

export interface RunErrorEvent {
  type: "RUN_ERROR";
  error: string;
  context?: string;
}

export type AgUiEvent =
  | RunStartedEvent
  | StepStartedEvent
  | StepFinishedEvent
  | RunAwaitingInputEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | StateSnapshotEvent
  | StateDeltaEvent
  | RunFinishedEvent
  | RunErrorEvent;

// ── Timeline representation for UI ──

export type AgentName = "planner" | "bootstrapper" | "worker" | "reviewer" | "orchestrator";

export interface TimelineEntry {
  id: string;
  seq: number;
  event: AgUiEvent;
  timestamp: Date;
}

// ── Workspace / project grouping ──

export interface Workspace {
  id: string;
  name: string;
  taskIds: string[];
}
