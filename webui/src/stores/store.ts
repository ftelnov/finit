import { create } from "zustand";
import type {
  Task,
  Agent,
  AgUiEvent,
  TimelineEntry,
  Workspace,
  TaskStatus,
} from "../types";
import * as api from "../api/client";
import { createTransport, type AgUiTransport } from "../api/agui";

// ── Derived state helpers ──

function deriveWorkspaces(tasks: Task[]): Workspace[] {
  const groups = new Map<string, string[]>();
  const defaultWs = "default";

  for (const task of tasks) {
    const key = task.project_id ?? defaultWs;
    const list = groups.get(key) ?? [];
    list.push(task.id);
    groups.set(key, list);
  }

  return Array.from(groups.entries()).map(([id, taskIds]) => ({
    id,
    name: id === defaultWs ? "Default Workspace" : id,
    taskIds,
  }));
}

// ── Store types ──

interface TaskEvents {
  timeline: TimelineEntry[];
  streamingText: Map<string, string>; // message_id -> accumulated text
  awaitingInput: {
    inputType: string;
    spec?: Record<string, unknown>;
    question?: string;
    options?: string[];
  } | null;
  finished: boolean;
  error: string | null;
}

interface AppState {
  // Data
  tasks: Task[];
  agents: Agent[];
  workspaces: Workspace[];
  selectedTaskId: string | null;
  selectedWorkspaceId: string | null;
  taskEvents: Map<string, TaskEvents>;

  // Connection
  transport: AgUiTransport | null;
  connected: boolean;
  backendHealthy: boolean;

  // UI
  sidebarCollapsed: boolean;
  createDialogOpen: boolean;

  // Actions
  fetchTasks: () => Promise<void>;
  fetchAgents: () => Promise<void>;
  checkHealth: () => Promise<void>;
  createTask: (input: string, projectId?: string) => Promise<Task>;
  cancelTask: (id: string) => Promise<void>;
  submitInput: (taskId: string, action: "approve" | "reject" | "respond", data?: unknown) => Promise<void>;
  selectTask: (taskId: string | null) => void;
  selectWorkspace: (workspaceId: string | null) => void;
  toggleSidebar: () => void;
  setCreateDialogOpen: (open: boolean) => void;

  // SSE
  connectToTask: (taskId: string) => void;
  disconnectFromTask: () => void;
}

export const useStore = create<AppState>((set, get) => ({
  // Initial state
  tasks: [],
  agents: [],
  workspaces: [],
  selectedTaskId: null,
  selectedWorkspaceId: null,
  taskEvents: new Map(),
  transport: null,
  connected: false,
  backendHealthy: false,
  sidebarCollapsed: false,
  createDialogOpen: false,

  fetchTasks: async () => {
    try {
      const tasks = await api.listTasks({ limit: 100 });
      set({
        tasks,
        workspaces: deriveWorkspaces(tasks),
      });
    } catch {
      // Silently fail, will retry
    }
  },

  fetchAgents: async () => {
    try {
      const agents = await api.listAgents();
      set({ agents });
    } catch {
      // Silently fail
    }
  },

  checkHealth: async () => {
    try {
      const health = await api.checkHealth();
      set({ backendHealthy: health.status === "healthy" });
    } catch {
      set({ backendHealthy: false });
    }
  },

  createTask: async (input: string, projectId?: string) => {
    const task = await api.createTask(input, projectId);
    const { tasks } = get();
    const updated = [task, ...tasks];
    set({
      tasks: updated,
      workspaces: deriveWorkspaces(updated),
      selectedTaskId: task.id,
    });
    // Auto-connect to the new task's SSE stream
    get().connectToTask(task.id);
    return task;
  },

  cancelTask: async (id: string) => {
    const task = await api.cancelTask(id);
    const { tasks } = get();
    const updated = tasks.map((t) => (t.id === id ? task : t));
    set({ tasks: updated, workspaces: deriveWorkspaces(updated) });
  },

  submitInput: async (taskId, action, data) => {
    const task = await api.submitTaskInput(taskId, action, data);
    const { tasks, taskEvents } = get();
    const updated = tasks.map((t) => (t.id === taskId ? task : t));

    // Clear awaiting input state
    const events = taskEvents.get(taskId);
    if (events) {
      const newEvents = new Map(taskEvents);
      newEvents.set(taskId, { ...events, awaitingInput: null });
      set({ tasks: updated, workspaces: deriveWorkspaces(updated), taskEvents: newEvents });
    } else {
      set({ tasks: updated, workspaces: deriveWorkspaces(updated) });
    }
  },

  selectTask: (taskId) => {
    set({ selectedTaskId: taskId });
    if (taskId) {
      get().connectToTask(taskId);
      // Refresh task data
      api.getTask(taskId).then((task) => {
        const { tasks } = get();
        const exists = tasks.find((t) => t.id === taskId);
        if (exists) {
          set({ tasks: tasks.map((t) => (t.id === taskId ? task : t)) });
        }
      }).catch(() => {});
    } else {
      get().disconnectFromTask();
    }
  },

  selectWorkspace: (workspaceId) => {
    set({ selectedWorkspaceId: workspaceId });
  },

  toggleSidebar: () => {
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed }));
  },

  setCreateDialogOpen: (open) => {
    set({ createDialogOpen: open });
  },

  connectToTask: (taskId: string) => {
    const { transport: existing } = get();
    existing?.disconnect();

    // Initialize events map for this task if not present
    const { taskEvents } = get();
    if (!taskEvents.has(taskId)) {
      const newEvents = new Map(taskEvents);
      newEvents.set(taskId, {
        timeline: [],
        streamingText: new Map(),
        awaitingInput: null,
        finished: false,
        error: null,
      });
      set({ taskEvents: newEvents });
    }

    const transport = createTransport(
      (event: AgUiEvent, seq: number) => {
        const { taskEvents, tasks } = get();
        const current = taskEvents.get(taskId) ?? {
          timeline: [],
          streamingText: new Map(),
          awaitingInput: null,
          finished: false,
          error: null,
        };

        // Add to timeline
        const entry: TimelineEntry = {
          id: `${taskId}-${seq}`,
          seq,
          event,
          timestamp: new Date(),
        };

        const newTimeline = [...current.timeline, entry];
        const newStreaming = new Map(current.streamingText);
        let newAwaiting = current.awaitingInput;
        let newFinished = current.finished;
        let newError = current.error;

        // Process specific event types
        switch (event.type) {
          case "TEXT_MESSAGE_START":
            newStreaming.set(event.message_id, "");
            break;
          case "TEXT_MESSAGE_CONTENT":
            newStreaming.set(
              event.message_id,
              (newStreaming.get(event.message_id) ?? "") + event.delta,
            );
            break;
          case "TEXT_MESSAGE_END":
            // Keep the accumulated text
            break;
          case "RUN_AWAITING_INPUT":
            newAwaiting = {
              inputType: event.input_type,
              spec: event.spec,
              question: event.question,
              options: event.options,
            };
            break;
          case "RUN_FINISHED":
            newFinished = true;
            newAwaiting = null;
            break;
          case "RUN_ERROR":
            newError = event.error;
            newFinished = true;
            newAwaiting = null;
            break;
        }

        // Update task status from events
        let taskStatus: TaskStatus | null = null;
        if (event.type === "RUN_STARTED") taskStatus = "running";
        if (event.type === "RUN_AWAITING_INPUT") taskStatus = "awaiting_input";
        if (event.type === "RUN_FINISHED") taskStatus = "completed";
        if (event.type === "RUN_ERROR") taskStatus = "failed";

        const updatedTasks = taskStatus
          ? tasks.map((t) => (t.id === taskId ? { ...t, status: taskStatus } : t))
          : tasks;

        const newEvents = new Map(taskEvents);
        newEvents.set(taskId, {
          timeline: newTimeline,
          streamingText: newStreaming,
          awaitingInput: newAwaiting,
          finished: newFinished,
          error: newError,
        });

        set({
          taskEvents: newEvents,
          tasks: updatedTasks,
          workspaces: deriveWorkspaces(updatedTasks),
        });
      },
      (error) => {
        console.warn("AG-UI SSE error:", error);
        set({ connected: false });
      },
    );

    transport.connect(taskId);
    set({ transport, connected: true });
  },

  disconnectFromTask: () => {
    const { transport } = get();
    transport?.disconnect();
    set({ transport: null, connected: false });
  },
}));
