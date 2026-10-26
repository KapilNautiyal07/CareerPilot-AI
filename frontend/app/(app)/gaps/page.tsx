"use client";

import { ChevronDown, Compass } from "lucide-react";
import { useState } from "react";

import { GapHistoryChart } from "@/components/charts";
import { GapBadge } from "@/components/domain";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Progress } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingCards, PageHeader } from "@/components/ui/states";
import { useGaps } from "@/lib/queries";
import { cn, formatDateTime, pct, titleCase } from "@/lib/utils";
import type { GapStatus, SkillGap } from "@/types/api";

const TABS: { key: GapStatus | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "missing", label: "Missing" },
  { key: "partial", label: "Partial" },
  { key: "matched", label: "Matched" },
];

export default function GapsPage() {
  const gaps = useGaps();
  const [tab, setTab] = useState<GapStatus | "all">("all");

  if (gaps.isLoading) return <LoadingCards count={3} />;
  if (gaps.error) return <ErrorState error={gaps.error} onRetry={() => gaps.refetch()} />;
  const g = gaps.data!;
  if (!g.items.length)
    return (
      <>
        <PageHeader title="Skill gaps" />
        <EmptyState icon={Compass} title="No skill gap analysis yet" description="Run the career analysis from the dashboard to compare your skills with real job requirements." />
      </>
    );
  const items = tab === "all" ? g.items : g.items.filter((i) => i.status === tab);

  return (
    <>
      <PageHeader
        title="Skill gaps"
        description={`For ${g.target_role}. Updated ${formatDateTime(g.computed_at)}. Priority combines importance, gap severity, deadline urgency and effort; every component is shown.`}
      />
      <div className="mb-6 grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Summary</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            {(["matched", "partial", "missing"] as const).map((s) => (
              <div key={s} className="flex items-center justify-between"><GapBadge status={s} /><span className="tabular-nums">{g.counts[s]}</span></div>
            ))}
            <p className="text-xs text-muted-foreground">Partial means evidence is weak (only listed, inferred, transferable or a low assessment score), not absent.</p>
          </CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Improvement over time</CardTitle><CardDescription>Each analysis or interview-driven update creates a snapshot.</CardDescription></CardHeader>
          <CardContent><GapHistoryChart data={g.history} height={180} /></CardContent>
        </Card>
      </div>

      <div role="tablist" aria-label="Filter by status" className="mb-4 inline-flex rounded-md border bg-card p-1">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)} className={cn("rounded px-3 py-1.5 text-sm", tab === t.key ? "bg-primary-soft font-medium text-accent-foreground" : "text-muted-foreground hover:text-foreground")}>
            {t.label}
          </button>
        ))}
      </div>
      <ul className="space-y-3">{items.map((item) => <GapRow key={item.id} gap={item} />)}</ul>
    </>
  );
}

function GapRow({ gap }: { gap: SkillGap }) {
  const [open, setOpen] = useState(false);
  const c = gap.priority_components;
  return (
    <li>
      <Card>
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full flex-col gap-3 p-4 text-left sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{gap.skill}</span>
              <GapBadge status={gap.status} />
              <Badge variant={gap.requirement_level === "required" ? "default" : "outline"}>{titleCase(gap.requirement_level)}</Badge>
              <span className="text-xs text-muted-foreground">in {pct(gap.demand_share)} of target demand</span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{gap.recommendation}</p>
          </div>
          <div className="flex items-center gap-4 sm:w-56">
            <div className="flex-1 space-y-1">
              <div className="flex justify-between text-xs"><span className="text-muted-foreground">Priority</span><span className="tabular-nums font-medium">{Math.round(gap.priority_score)}</span></div>
              <Progress value={gap.priority_score} tone={gap.priority_score >= 50 ? "danger" : gap.priority_score >= 25 ? "warning" : "success"} label={`${gap.skill} priority`} />
            </div>
            <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden />
          </div>
        </button>
        {open && (
          <CardContent className="grid gap-4 border-t pt-4 md:grid-cols-2">
            <div>
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Evidence</h4>
              {gap.evidence.length ? <ul className="space-y-1.5 text-sm">{gap.evidence.map((e) => <li key={e} className="border-l-2 pl-2 text-muted-foreground">{e}</li>)}</ul> : <p className="text-sm text-muted-foreground">No evidence found.</p>}
              {gap.transferable_from.length > 0 && <p className="mt-2 text-sm">Transferable from: <span className="font-medium">{gap.transferable_from.join(", ")}</span></p>}
            </div>
            <div>
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Priority components</h4>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Importance</dt><dd className="tabular-nums">{c.importance}</dd>
                <dt className="text-muted-foreground">Severity (1 − evidence)</dt><dd className="tabular-nums">{c.severity}</dd>
                <dt className="text-muted-foreground">Deadline urgency</dt><dd className="tabular-nums">{c.urgency}</dd>
                <dt className="text-muted-foreground">Effort adjustment</dt><dd className="tabular-nums">{c.effort_penalty}</dd>
                <dt className="text-muted-foreground">Evidence strength</dt><dd className="tabular-nums">{gap.evidence_strength}</dd>
                <dt className="text-muted-foreground">Est. hours to close</dt><dd className="tabular-nums">{gap.estimated_hours}</dd>
              </dl>
              <p className="mt-2 font-mono text-xs text-muted-foreground">{c.formula}</p>
            </div>
          </CardContent>
        )}
      </Card>
    </li>
  );
}
