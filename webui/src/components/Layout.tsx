import type { ReactNode } from "react";
import { useStore } from "../stores/store";
import { PanelLeftClose, PanelLeft } from "lucide-react";
import { clsx } from "clsx";

interface LayoutProps {
  sidebar: ReactNode;
  children: ReactNode;
}

export function Layout({ sidebar, children }: LayoutProps) {
  const collapsed = useStore((s) => s.sidebarCollapsed);
  const toggle = useStore((s) => s.toggleSidebar);
  const healthy = useStore((s) => s.backendHealthy);

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      {/* Top bar */}
      <header className="h-12 bg-surface-1 border-b border-surface-4 flex items-center px-4 shrink-0">
        <button
          onClick={toggle}
          className="p-1.5 hover:bg-surface-3 rounded-md transition-colors mr-3"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeft className="w-4 h-4 text-zinc-400" />
          ) : (
            <PanelLeftClose className="w-4 h-4 text-zinc-400" />
          )}
        </button>

        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-accent flex items-center justify-center">
            <span className="text-white text-xs font-bold">F</span>
          </div>
          <span className="font-semibold text-sm text-zinc-200">Finit</span>
        </div>

        <div className="flex-1" />

        {/* Backend status indicator */}
        <div className="flex items-center gap-2 text-xs text-zinc-500">
          <div
            className={clsx(
              "w-2 h-2 rounded-full",
              healthy ? "bg-green-500" : "bg-red-500 animate-pulse-dot",
            )}
          />
          <span>{healthy ? "Connected" : "Disconnected"}</span>
        </div>
      </header>

      {/* Main content area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <aside
          className={clsx(
            "bg-surface-1 border-r border-surface-4 transition-all duration-200 overflow-hidden shrink-0",
            collapsed ? "w-0" : "w-72",
          )}
        >
          {!collapsed && sidebar}
        </aside>

        {/* Content */}
        <main className="flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
