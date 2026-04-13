import { usePolling } from "./hooks/useAgUi";
import { useStore } from "./stores/store";
import { Layout } from "./components/Layout";
import { Sidebar } from "./components/Sidebar";
import { TaskDetail } from "./components/TaskDetail";
import { EmptyState } from "./components/EmptyState";
import { CreateTaskDialog } from "./components/CreateTaskDialog";

export default function App() {
  usePolling(4000);

  const selectedTaskId = useStore((s) => s.selectedTaskId);
  const createDialogOpen = useStore((s) => s.createDialogOpen);
  const setCreateDialogOpen = useStore((s) => s.setCreateDialogOpen);

  return (
    <Layout sidebar={<Sidebar />}>
      {selectedTaskId ? <TaskDetail taskId={selectedTaskId} /> : <EmptyState />}
      {createDialogOpen && (
        <CreateTaskDialog onClose={() => setCreateDialogOpen(false)} />
      )}
    </Layout>
  );
}
