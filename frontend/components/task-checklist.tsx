"use client";

import { Clock } from "lucide-react";
import { useState } from "react";

import { ResourceLink } from "@/components/domain";
import { Badge } from "@/components/ui/primitives";
import { errorMessage } from "@/lib/api";
import { useTaskUpdate } from "@/lib/queries";
import { cn, formatDate } from "@/lib/utils";
import type { Task } from "@/types/api";

const TYPE_LABEL: Record<Task["task_type"], string> = {
  learn: "Learn",
  practice: "Practice",
  project: "Project",
  revision: "Revision",
  assessment: "Checkpoint",
  mock_interview: "Mock interview",
};

export function TaskChecklist({ tasks, showResources = false }: { tasks: Task[]; showResources?: boolean }) {
  const update = useTaskUpdate();
  const [error, setError] = useState<string | null>(null);
  // Optimistic state: the checkbox flips immediately and rolls back if the request fails.
  const [optimistic, setOptimistic] = useState<Record<number, boolean>>({});

  async function toggle(t: Task, nextDone: boolean) {
    setError(null);
    setOptimistic((o) => ({ ...o, [t.id]: nextDone }));
    try {
      await update.mutateAsync({ id: t.id, status: nextDone ? "completed" : "pending" });
    } catch (err) {
      setOptimistic((o) => ({ ...o, [t.id]: !nextDone }));
      setError(errorMessage(err));
    }
  }

  return (
    <div>
      <ul className="divide-y">
        {tasks.map((t) => {
          const done = optimistic[t.id] ?? t.status === "completed";
          return (
            <li key={t.id} className="flex gap-3 py-2.5">
              <input
                type="checkbox"
                checked={done}
                onChange={(e) => toggle(t, e.target.checked)}
                disabled={t.status === "skipped"}
                className="mt-1 size-4 shrink-0 accent-[var(--primary)]"
                aria-label={`Mark "${t.title}" as ${done ? "not done" : "done"}`}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className={cn("text-sm font-medium", done && "text-muted-foreground line-through")}>{t.title}</span>
                  <Badge variant="muted">{TYPE_LABEL[t.task_type]}</Badge>
                  {t.origin === "remediation" && <Badge variant="warning">From assessment</Badge>}
                  {t.overdue && <Badge variant="danger">Overdue</Badge>}
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">{t.description}</p>
                <p className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1"><Clock className="size-3" aria-hidden /> {t.estimated_hours} h</span>
                  <span>{formatDate(t.scheduled_date, { year: undefined })}</span>
                  {t.skill && <span>{t.skill}</span>}
                </p>
                {showResources && t.resources.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {t.resources.map((r) => <ResourceLink key={r.id} r={r} />)}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {error && <p className="mt-2 text-xs text-danger" role="alert">{error}</p>}
    </div>
  );
}
