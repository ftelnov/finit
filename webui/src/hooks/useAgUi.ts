import { useEffect, useRef } from "react";
import { useStore } from "../stores/store";

/**
 * Hook to manage AG-UI SSE connection lifecycle.
 * Connects when a task is selected, disconnects on unmount or task change.
 */
export function useAgUiConnection(taskId: string | null) {
  const connectToTask = useStore((s) => s.connectToTask);
  const disconnectFromTask = useStore((s) => s.disconnectFromTask);
  const prevTaskId = useRef<string | null>(null);

  useEffect(() => {
    if (taskId && taskId !== prevTaskId.current) {
      connectToTask(taskId);
      prevTaskId.current = taskId;
    }

    return () => {
      // Don't disconnect on every re-render, only on true unmount
    };
  }, [taskId, connectToTask]);

  useEffect(() => {
    return () => {
      disconnectFromTask();
    };
  }, [disconnectFromTask]);
}

/**
 * Hook for polling task list and agents on an interval.
 */
export function usePolling(intervalMs = 5000) {
  const fetchTasks = useStore((s) => s.fetchTasks);
  const fetchAgents = useStore((s) => s.fetchAgents);
  const checkHealth = useStore((s) => s.checkHealth);

  useEffect(() => {
    // Initial fetch
    fetchTasks();
    fetchAgents();
    checkHealth();

    const id = setInterval(() => {
      fetchTasks();
      fetchAgents();
      checkHealth();
    }, intervalMs);

    return () => clearInterval(id);
  }, [intervalMs, fetchTasks, fetchAgents, checkHealth]);
}
