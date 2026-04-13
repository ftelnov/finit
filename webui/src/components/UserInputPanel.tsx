import { useState } from "react";
import { useStore } from "../stores/store";
import { MessageSquare, Send } from "lucide-react";

interface UserInputPanelProps {
  taskId: string;
  question?: string;
  options?: string[];
}

export function UserInputPanel({ taskId, question, options }: UserInputPanelProps) {
  const [response, setResponse] = useState("");
  const [loading, setLoading] = useState(false);
  const submitInput = useStore((s) => s.submitInput);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!response.trim()) return;

    setLoading(true);
    try {
      await submitInput(taskId, "respond", { text: response.trim() });
      setResponse("");
    } finally {
      setLoading(false);
    }
  };

  const handleOption = async (option: string) => {
    setLoading(true);
    try {
      await submitInput(taskId, "respond", { text: option });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card overflow-hidden">
      <div className="p-4 bg-status-awaiting/5 border-b border-status-awaiting/20">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-status-awaiting" />
          <h3 className="text-sm font-medium text-zinc-200">
            Input Required
          </h3>
        </div>
      </div>

      <div className="p-4">
        {question && (
          <p className="text-sm text-zinc-300 mb-4 leading-relaxed">
            {question}
          </p>
        )}

        {/* Quick options */}
        {options && options.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-4">
            {options.map((opt) => (
              <button
                key={opt}
                onClick={() => handleOption(opt)}
                disabled={loading}
                className="btn-secondary text-xs"
              >
                {opt}
              </button>
            ))}
          </div>
        )}

        {/* Free-form input */}
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            value={response}
            onChange={(e) => setResponse(e.target.value)}
            placeholder="Type your response..."
            className="input-field flex-1"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={!response.trim() || loading}
            className="btn-primary flex items-center gap-1.5"
          >
            <Send className="w-3.5 h-3.5" />
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
