import type { AgUiEvent, AgUiEventType } from "../types";

/**
 * AG-UI SSE transport layer.
 *
 * Abstracted so we can swap the underlying mechanism:
 * - Browser: native EventSource
 * - Tauri: could use tauri-plugin-http or invoke commands
 *
 * The transport emits parsed AgUiEvent objects via a callback.
 */

export interface AgUiTransport {
  connect(taskId: string): void;
  disconnect(): void;
  readonly connected: boolean;
}

export type AgUiEventHandler = (event: AgUiEvent, seq: number) => void;
export type AgUiErrorHandler = (error: Event | string) => void;

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

const AG_UI_EVENT_TYPES: AgUiEventType[] = [
  "RUN_STARTED",
  "STEP_STARTED",
  "STEP_FINISHED",
  "RUN_AWAITING_INPUT",
  "TEXT_MESSAGE_START",
  "TEXT_MESSAGE_CONTENT",
  "TEXT_MESSAGE_END",
  "STATE_SNAPSHOT",
  "STATE_DELTA",
  "RUN_FINISHED",
  "RUN_ERROR",
];

/**
 * Browser-native SSE transport using EventSource.
 */
export class BrowserAgUiTransport implements AgUiTransport {
  private eventSource: EventSource | null = null;
  private lastEventId: string | null = null;

  constructor(
    private onEvent: AgUiEventHandler,
    private onError?: AgUiErrorHandler,
  ) {}

  get connected(): boolean {
    return this.eventSource?.readyState === EventSource.OPEN;
  }

  connect(taskId: string): void {
    this.disconnect();

    const url = `${BASE_URL}/ag-ui/tasks/${taskId}/events`;
    this.eventSource = new EventSource(url);

    // Listen for each event type
    for (const eventType of AG_UI_EVENT_TYPES) {
      this.eventSource.addEventListener(eventType, (e: MessageEvent) => {
        try {
          const parsed: AgUiEvent = JSON.parse(e.data);
          const seq = e.lastEventId ? parseInt(e.lastEventId, 10) : 0;
          this.lastEventId = e.lastEventId;
          this.onEvent(parsed, seq);
        } catch (err) {
          console.error("Failed to parse AG-UI event:", err, e.data);
        }
      });
    }

    this.eventSource.onerror = (e) => {
      this.onError?.(e);
    };
  }

  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  getLastEventId(): string | null {
    return this.lastEventId;
  }
}

/**
 * Tauri transport stub - to be implemented when wrapping in Tauri.
 * Would use tauri's HTTP client or invoke commands to establish SSE.
 */
export class TauriAgUiTransport implements AgUiTransport {
  private _connected = false;

  constructor(
    private onEvent: AgUiEventHandler,
    private onError?: AgUiErrorHandler,
  ) {}

  get connected(): boolean {
    return this._connected;
  }

  connect(_taskId: string): void {
    // TODO: implement using @tauri-apps/api/http or invoke
    console.warn("TauriAgUiTransport not yet implemented, falling back to browser SSE");
    const browser = new BrowserAgUiTransport(this.onEvent, this.onError);
    browser.connect(_taskId);
    this._connected = true;
  }

  disconnect(): void {
    this._connected = false;
  }
}

/**
 * Factory: create the appropriate transport based on environment.
 */
export function createTransport(
  onEvent: AgUiEventHandler,
  onError?: AgUiErrorHandler,
): AgUiTransport {
  // Detect Tauri environment
  if (typeof window !== "undefined" && "__TAURI__" in window) {
    return new TauriAgUiTransport(onEvent, onError);
  }
  return new BrowserAgUiTransport(onEvent, onError);
}
