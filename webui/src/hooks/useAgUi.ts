import { useEffect } from "react";
import { useStore } from "../stores/store";

/**
 * Hook to manage AG-UI SSE connection lifecycle.
 * Connects when a task is selected, disconnects on unmount or task change.
 * Properly handles React StrictMode's double-invoke of effects.
 */
export function useAgUiConnection(taskId: string | null) {
  const connectToTask = useStore((s) => s.connectToTask);
  const disconnectFromTask = useStore((s) => s.disconnectFromTask);

  useEffect(() => {
    if (taskId) {
      connectToTask(taskId);
    }
    return () => {
      disconnectFromTask();
    };
  }, [taskId, connectToTask, disconnectFromTask]);
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
