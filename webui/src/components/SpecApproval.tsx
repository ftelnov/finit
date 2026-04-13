import { useState } from "react";
import { useStore } from "../stores/store";
import { CheckCircle2, XCircle, FileText, ListChecks, TestTube2, FolderTree, Tags } from "lucide-react";

interface SpecApprovalProps {
  taskId: string;
  spec?: Record<string, unknown>;
  options?: string[];
}

export function SpecApproval({ taskId, spec }: SpecApprovalProps) {
  const [loading, setLoading] = useState(false);
  const submitInput = useStore((s) => s.submitInput);

  const handleAction = async (action: "approve" | "reject") => {
    setLoading(true);
    try {
      await submitInput(taskId, action);
    } finally {
      setLoading(false);
    }
  };

  const title = (spec?.title as string) ?? "Untitled Specification";
  const description = (spec?.description as string) ?? "";
  const criteria = (spec?.acceptance_criteria as string[]) ?? [];
  const testPlan = spec?.test_plan as Record<string, unknown> | undefined;
  const filesAffected = (spec?.files_affected as string[]) ?? [];
  const domains = (spec?.domains as string[]) ?? [];

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="p-4 bg-status-awaiting/5 border-b border-status-awaiting/20">
        <div className="flex items-center gap-2 mb-1">
          <FileText className="w-4 h-4 text-status-awaiting" />
          <h3 className="text-sm font-medium text-zinc-200">
            Specification Review
          </h3>
        </div>
        <p className="text-xs text-zinc-500">
          The planner has generated a specification. Review and approve to
          proceed.
        </p>
      </div>

      <div className="p-4 space-y-4">
        {/* Title & Description */}
        <div>
          <h4 className="text-base font-medium text-zinc-200 mb-1">{title}</h4>
          {description && (
            <p className="text-sm text-zinc-400 leading-relaxed">
              {description}
            </p>
          )}
        </div>

        {/* Acceptance Criteria */}
        {criteria.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <ListChecks className="w-3.5 h-3.5 text-zinc-500" />
              <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                Acceptance Criteria
              </span>
            </div>
            <ul className="space-y-1">
              {criteria.map((c, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 text-sm text-zinc-300"
                >
                  <span className="text-accent mt-0.5 shrink-0">-</span>
                  {c}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Test Plan */}
        {testPlan && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <TestTube2 className="w-3.5 h-3.5 text-zinc-500" />
              <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                Test Plan
              </span>
            </div>
            <pre className="text-xs text-zinc-400 bg-surface-1 rounded-lg p-3 overflow-x-auto font-mono">
              {JSON.stringify(testPlan, null, 2)}
            </pre>
          </div>
        )}

        {/* Files & Domains */}
        <div className="flex gap-4">
          {filesAffected.length > 0 && (
            <div className="flex-1">
              <div className="flex items-center gap-1.5 mb-1.5">
                <FolderTree className="w-3.5 h-3.5 text-zinc-500" />
                <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                  Files
                </span>
              </div>
              <div className="space-y-0.5">
                {filesAffected.map((f, i) => (
                  <div
                    key={i}
                    className="text-xs text-zinc-500 font-mono truncate"
                  >
                    {f}
                  </div>
                ))}
              </div>
            </div>
          )}
          {domains.length > 0 && (
            <div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <Tags className="w-3.5 h-3.5 text-zinc-500" />
                <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                  Domains
                </span>
              </div>
              <div className="flex flex-wrap gap-1">
                {domains.map((d, i) => (
                  <span
                    key={i}
                    className="text-xs px-2 py-0.5 rounded-full bg-surface-3 text-zinc-400"
                  >
                    {d}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="p-4 border-t border-surface-4 flex items-center gap-3">
        <button
          onClick={() => handleAction("approve")}
          disabled={loading}
          className="btn-primary flex items-center gap-2"
        >
          <CheckCircle2 className="w-4 h-4" />
          Approve Spec
        </button>
        <button
          onClick={() => handleAction("reject")}
          disabled={loading}
          className="btn-danger flex items-center gap-2"
        >
          <XCircle className="w-4 h-4" />
          Reject
        </button>
      </div>
    </div>
  );
}
