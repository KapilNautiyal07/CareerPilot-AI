"use client";

import { FlaskConical } from "lucide-react";

import { useMeta } from "@/lib/queries";

/** Always tells the user which integrations are mocked, so demo data is never mistaken for real data. */
export function DemoBanner() {
  const { data } = useMeta();
  if (!data) return null;
  const parts: string[] = [];
  if (data.mock_jobs) parts.push("job listings are fictional demo data");
  if (data.llm.provider === "mock") parts.push("AI analysis runs in deterministic mode (no LLM configured)");
  if (data.code_sandbox === "disabled") parts.push("code answers are checked statically, not executed");
  if (!parts.length) return null;
  return (
    <div className="border-b border-warning/30 bg-warning-soft px-4 py-2 text-xs sm:px-6" role="note">
      <p className="mx-auto flex max-w-6xl items-start gap-2">
        <FlaskConical className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
        <span>
          <strong className="font-semibold">Demo mode:</strong> {parts.join("; ")}.
        </span>
      </p>
    </div>
  );
}
