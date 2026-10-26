"use client";

import { Check } from "lucide-react";
import Link from "next/link";

import type { Stage } from "@/lib/journey";
import { cn } from "@/lib/utils";

/** Numbered dot for a journey stage: check when done, filled when it is the current stage. */
export function StageDot({ index, status, current }: { index: number; status: Stage["status"]; current: boolean }) {
  return (
    <span
      className={cn(
        "grid size-5 shrink-0 place-items-center rounded-full border text-[10px] font-semibold tabular-nums",
        status === "done" && "border-success bg-success text-white",
        status !== "done" && current && "border-primary bg-primary text-primary-foreground",
        status === "active" && !current && "border-primary text-primary",
        status === "todo" && !current && "text-muted-foreground",
      )}
      aria-hidden
    >
      {status === "done" ? <Check className="size-3" strokeWidth={3} /> : index + 1}
    </span>
  );
}

/** Horizontal stepper (stacks vertically on small screens). */
export function JourneySteps({ stages, current }: { stages: Stage[]; current: number }) {
  return (
    <ol className="grid gap-2 sm:grid-cols-5" aria-label="Your journey">
      {stages.map((s, i) => (
        <li key={s.key}>
          <Link
            href={s.href}
            aria-current={i === current ? "step" : undefined}
            className={cn(
              "flex h-full items-start gap-2 rounded-md border p-2.5 transition-colors hover:bg-muted",
              i === current && "border-primary/50 bg-primary-soft hover:bg-primary-soft",
            )}
          >
            <StageDot index={i} status={s.status} current={i === current} />
            <span className="min-w-0">
              <span className="block text-sm font-medium leading-5">{s.label}</span>
              <span className="block text-xs text-muted-foreground">
                {s.status === "done" ? "Done · " : i === current ? "You are here · " : ""}
                {s.detail}
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}
