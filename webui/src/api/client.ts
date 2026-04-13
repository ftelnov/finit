import type { Task, Agent } from "../types";

/**
 * Base URL for the orchestrator API.
 * In dev mode, Vite proxy handles forwarding to localhost:8080.
 * In production (Docker), nginx serves static files and proxies /api.
 * For Tauri, this can be overridden to point at the orchestrator directly.
 */
const BASE_URL = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const message = body?.error?.message ?? res.statusText;
    throw new ApiError(res.status, message);
  }

  return res.json();
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ── Tasks ──

export async function createTask(input: string, projectId?: string): Promise<Task> {
  return request<Task>("/api/tasks", {
    method: "POST",
    body: JSON.stringify({ input, project_id: projectId ?? null }),
  });
}

export async function listTasks(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<Task[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return request<Task[]>(`/api/tasks${query ? `?${query}` : ""}`);
}

export async function getTask(id: string): Promise<Task> {
  return request<Task>(`/api/tasks/${id}`);
}

export async function cancelTask(id: string): Promise<Task> {
  return request<Task>(`/api/tasks/${id}`, { method: "DELETE" });
}

export async function submitTaskInput(
  id: string,
  action: "approve" | "reject" | "respond",
  data?: unknown,
): Promise<Task> {
  return request<Task>(`/api/tasks/${id}/input`, {
    method: "POST",
    body: JSON.stringify({ action, data }),
  });
}

// ── Agents ──

export async function listAgents(): Promise<Agent[]> {
  return request<Agent[]>("/api/agents");
}

export async function getAgent(id: string): Promise<Agent> {
  return request<Agent>(`/api/agents/${id}`);
}

// ── Health ──

export async function checkHealth(): Promise<{ status: string; database: string }> {
  return request("/health");
}
