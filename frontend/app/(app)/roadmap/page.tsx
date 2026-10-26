"use client";

import { CheckCircle2, ChevronDown, Gauge, Map, RefreshCw, XCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { TaskChecklist } from "@/components/task-checklist";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, Progress } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingCards, PageHeader } from "@/components/ui/states";
import { WorkflowPanel } from "@/components/workflow-panel";
import { api, errorMessage } from "@/lib/api";
import { useProfile, useRoadmap, useStartAnalysis } from "@/lib/queries";
import { cn, formatDate, formatDateTime, titleCase } from "@/lib/utils";
import type { Roadmap } from "@/types/api";

const PACES = [
  { key: "fast", label: "Fast track", weeks: 6, hours: 15, body: "Intensive: more hours, sooner deadline" },
  { key: "steady", label: "Steady", weeks: 12, hours: 8, body: "Balanced alongside a full-time job" },
  { key: "relaxed", label: "Relaxed", weeks: 20, hours: 5, body: "Lighter weekly load, longer runway" },
] as const;

/** Deadline for an N-week plan starting today; the deadline day itself is a study day. */
const deadlineFor = (weeks: number) => new Date(Date.now() + (weeks * 7 - 1) * 86_400_000).toISOString().slice(0, 10);

function PacePicker({ currentWeeks, currentHours, onStarted }: { currentWeeks: number; currentHours: number; onStarted: (id: string) => void }) {
  const profile = useProfile();
  const start = useStartAnalysis();
  const [weeks, setWeeks] = useState(String(currentWeeks));
  const [hours, setHours] = useState(String(currentHours));
  const [error, setError] = useState<string | null>(null);
  const w = Number(weeks);
  const h = Number(hours);
  const valid = Number.isInteger(w) && w >= 1 && w <= 52 && h >= 0.5 && h <= 80;
  const selected = PACES.find((p) => p.weeks === w && p.hours === h)?.key;

  async function apply() {
    setError(null);
    const deadline = deadlineFor(w);
    try {
      // Save as preferences so later analyses and replans keep this pace.
      const prefs = profile.data?.preferences;
      if (prefs) await api("/profile/preferences", { method: "PUT", body: { ...prefs, weekly_study_hours: h, target_deadline: deadline } });
      const r = await start.mutateAsync({ deadline, weekly_hours: h });
      onStarted(r.execution_id);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>Learning pace</CardTitle>
        <CardDescription>Choose how fast you want to learn. The roadmap is rebuilt to fit this many weeks and hours per week.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="Pace presets">
          {PACES.map((p) => (
            <button
              key={p.key}
              type="button"
              role="radio"
              aria-checked={selected === p.key}
              onClick={() => { setWeeks(String(p.weeks)); setHours(String(p.hours)); }}
              className={cn("rounded-lg border p-3 text-left transition-colors hover:bg-muted", selected === p.key && "border-primary bg-primary-soft")}
            >
              <p className="text-sm font-semibold">{p.label}</p>
              <p className="text-xs tabular-nums text-muted-foreground">{p.weeks} weeks · {p.hours} h/week</p>
              <p className="mt-1 text-xs text-muted-foreground">{p.body}</p>
            </button>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Weeks" htmlFor="pace-weeks" error={weeks && !(Number.isInteger(w) && w >= 1 && w <= 52) ? "1 to 52 weeks" : undefined}>
            <Input id="pace-weeks" type="number" min={1} max={52} step={1} value={weeks} onChange={(e) => setWeeks(e.target.value)} />
          </Field>
          <Field label="Hours / week" htmlFor="pace-hours" error={hours && !(h >= 0.5 && h <= 80) ? "0.5 to 80 hours" : undefined}>
            <Input id="pace-hours" type="number" min={0.5} max={80} step="0.5" value={hours} onChange={(e) => setHours(e.target.value)} />
          </Field>
          <div className="flex flex-col justify-end gap-1.5">
            <p className="text-xs text-muted-foreground">{valid ? `${Math.round(w * h)} h total · ends ${formatDate(deadlineFor(w))}` : " "}</p>
            <Button onClick={apply} disabled={!valid || profile.isLoading} loading={start.isPending}>Rebuild roadmap</Button>
          </div>
        </div>
        {error && <Alert tone="error">{error}</Alert>}
      </CardContent>
    </Card>
  );
}

export default function RoadmapPage() {
  const roadmap = useRoadmap();
  const [execution, setExecution] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPace, setShowPace] = useState(false);
  const [openWeeks, setOpenWeeks] = useState<Set<number>>(new Set());

  async function replan() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ execution_id: string }>("/roadmap/replan", { method: "POST" });
      setExecution(r.execution_id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (roadmap.isLoading) return <LoadingCards count={3} />;
  if (roadmap.error) return <ErrorState error={roadmap.error} onRetry={() => roadmap.refetch()} />;
  const rm = roadmap.data;
  if (!rm)
    return (
      <>
        <PageHeader title="Learning roadmap" />
        <EmptyState icon={Map} title="No roadmap yet" description="Run the career analysis to generate a plan from your skill gaps." action={<Button asChild><Link href="/dashboard">Go to dashboard</Link></Button>} />
      </>
    );

  const today = new Date().toISOString().slice(0, 10);
  const currentWeek = rm.weeks.find((w) => w.start <= today && today <= w.end)?.index ?? rm.weeks[0]?.index;
  const isOpen = (i: number) => openWeeks.has(i) || (openWeeks.size === 0 && i === currentWeek);
  const toggle = (i: number) =>
    setOpenWeeks((prev) => {
      const next = new Set(prev.size === 0 && currentWeek ? [currentWeek] : prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  const ev = rm.summary.evaluation;

  return (
    <>
      <PageHeader
        title={rm.title}
        description={`${formatDate(rm.start_date)} – ${formatDate(rm.end_date)} · ${rm.weekly_hours} h/week over ${rm.study_days_per_week} days · version ${rm.version}`}
        actions={
          <>
            <Button variant="outline" onClick={() => setShowPace((s) => !s)} aria-expanded={showPace}><Gauge /> Change pace</Button>
            <Button variant="outline" onClick={replan} loading={busy}><RefreshCw /> Replan with my progress</Button>
          </>
        }
      />
      {error && <Alert tone="error" className="mb-4">{error}</Alert>}
      {showPace && (
        <PacePicker
          currentWeeks={rm.weeks.length}
          currentHours={rm.weekly_hours}
          onStarted={(id) => { setExecution(id); setShowPace(false); }}
        />
      )}
      {execution && <div className="mb-6"><WorkflowPanel executionId={execution} onFinished={() => roadmap.refetch()} /></div>}

      <AtAGlance rm={rm} today={today} currentWeek={currentWeek} onReplan={replan} replanning={busy} />

      <h2 className="mb-3 text-sm font-semibold">Week by week</h2>
      <div className="space-y-3">
        {rm.weeks.map((w) => {
          const open = isOpen(w.index);
          const pctDone = w.planned_hours ? (100 * w.completed_hours) / w.planned_hours : 0;
          const past = w.end < today;
          const finished = w.planned_hours > 0 && w.completed_hours >= w.planned_hours - 0.01;
          return (
            <Card key={w.index} className={cn(w.index === currentWeek && "border-primary/40")}>
              <button type="button" onClick={() => toggle(w.index)} aria-expanded={open} className="flex w-full items-center gap-4 p-4 text-left">
                <div className="w-20 shrink-0">
                  <p className="text-sm font-semibold">Week {w.index}</p>
                  <p className="text-xs text-muted-foreground">{formatDate(w.start, { year: undefined })}</p>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{w.milestone}</p>
                  <p className="truncate text-xs text-muted-foreground">{w.focus.join(" · ")}</p>
                </div>
                <div className="hidden w-32 sm:block"><Progress value={pctDone} tone="success" label={`Week ${w.index} progress`} /></div>
                <span className="text-xs tabular-nums text-muted-foreground">{w.completed_hours}/{w.planned_hours} h</span>
                {w.index === currentWeek ? (
                  <Badge>This week</Badge>
                ) : finished ? (
                  <Badge variant="success">Done</Badge>
                ) : past ? (
                  <Badge variant="danger">Unfinished</Badge>
                ) : null}
                <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden />
              </button>
              {open && <CardContent className="border-t pt-2"><TaskChecklist tasks={w.tasks} showResources /></CardContent>}
            </Card>
          );
        })}
      </div>

      <details className="group mt-8">
        <summary className="flex cursor-pointer select-none items-center gap-2 text-sm font-semibold">
          <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden />
          How this plan was built
          <span className="font-normal text-muted-foreground">· skills covered, assumptions, checks and history</span>
        </summary>
      <Card className="mt-4">
        <CardContent className="space-y-2 pt-5 text-sm">
          <p><span className="font-medium">Covers:</span> {rm.summary.covered_skills.join(", ") || "—"}</p>
          {rm.summary.uncovered_gaps.length > 0 && <p><span className="font-medium">Not covered (not enough time):</span> <span className="text-muted-foreground">{rm.summary.uncovered_gaps.join(", ")}</span></p>}
          <ul className="list-inside list-disc text-xs text-muted-foreground">{rm.summary.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </CardContent>
      </Card>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        {ev && (
          <Card>
            <CardHeader><CardTitle>Validation by the Career Evaluation Agent</CardTitle><CardDescription>{ev.explanation}</CardDescription></CardHeader>
            <CardContent className="space-y-2">
              {ev.checks.map((c) => (
                <p key={c.name} className="flex items-center gap-2 text-sm">
                  {c.passed ? <CheckCircle2 className="size-4 text-success" aria-label="Passed" /> : <XCircle className="size-4 text-danger" aria-label="Failed" />}
                  {titleCase(c.name)} <span className="text-xs text-muted-foreground">({c.detail})</span>
                </p>
              ))}
              {ev.issues.filter((i) => i.severity === "warning").map((i) => <p key={i.message} className="text-xs text-muted-foreground">⚠ {i.message}</p>)}
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader><CardTitle>Revision history</CardTitle></CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {[...rm.revision_log].reverse().map((r) => (
                <li key={`${r.version}-${r.at}`}>
                  <span className="font-medium">v{r.version}</span> · {titleCase(String(r.reason))} <span className="text-xs text-muted-foreground">{formatDateTime(r.at)}</span>
                  {Array.isArray(r.remediation) && r.remediation.length > 0 && <span className="block text-xs text-muted-foreground">Added practice for: {(r.remediation as string[]).join(", ")}</span>}
                  {Array.isArray(r.mastered) && r.mastered.length > 0 && <span className="block text-xs text-muted-foreground">Dropped (mastered): {(r.mastered as string[]).join(", ")}</span>}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
      </details>
    </>
  );
}

/** Plain-language summary: where you are, whether you are on track, and what to do now. */
function AtAGlance({ rm, today, currentWeek, onReplan, replanning }: { rm: Roadmap; today: string; currentWeek?: number; onReplan: () => void; replanning: boolean }) {
  const DAY = 86_400_000;
  const t = (s: string) => new Date(`${s}T00:00:00`).getTime();
  const totalDays = Math.max(1, (t(rm.end_date) - t(rm.start_date)) / DAY + 1);
  const elapsed = Math.min(totalDays, Math.max(0, (t(today) - t(rm.start_date)) / DAY));
  const daysLeft = Math.max(0, Math.round((t(rm.end_date) - t(today)) / DAY) + 1);
  const expected = (100 * elapsed) / totalDays;
  const diff = rm.progress.percent - expected;
  const overdue = rm.progress.overdue_tasks;
  const status =
    rm.progress.percent >= 100
      ? { tone: "success" as const, label: "Plan complete", text: "You finished every task. Run the analysis again to plan your next step." }
      : overdue > 0 || diff < -10
        ? { tone: "danger" as const, label: "Behind schedule", text: overdue ? `${overdue} task${overdue === 1 ? " is" : "s are"} overdue. Replan to spread the remaining work over the weeks you have left.` : "You are behind where you'd expect to be by now. Try to catch up this week, or replan." }
        : diff > 10
          ? { tone: "success" as const, label: "Ahead of schedule", text: "Nice, you're ahead of plan. Keep going or start a mock interview." }
          : { tone: "primary" as const, label: "On track", text: "You're where you should be. Finish this week's tasks to stay on track." };
  const week = rm.weeks.find((w) => w.index === currentWeek);
  const nextTask = week?.tasks.find((x) => x.status === "pending") ?? rm.weeks.flatMap((w) => w.tasks).find((x) => x.status === "pending");
  const dot = { success: "bg-success", danger: "bg-danger", primary: "bg-primary" }[status.tone];

  return (
    <Card className="mb-6">
      <CardContent className="grid gap-5 pt-5 md:grid-cols-3">
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Where you are</p>
          <p className="text-2xl font-semibold">Week {currentWeek ?? 1} <span className="text-base font-normal text-muted-foreground">of {rm.weeks.length}</span></p>
          <Progress value={rm.progress.percent} label="Roadmap progress" />
          <p className="text-xs text-muted-foreground">{Math.round(rm.progress.percent)}% done · {rm.progress.completed_hours}/{rm.progress.total_hours} h · {daysLeft} day{daysLeft === 1 ? "" : "s"} left</p>
        </div>
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">How it&apos;s going</p>
          <p className="flex items-center gap-2 font-semibold"><span className={cn("size-2.5 rounded-full", dot)} aria-hidden />{status.label}</p>
          <p className="text-sm text-muted-foreground">{status.text}</p>
          {overdue > 0 && <Button size="sm" variant="outline" onClick={onReplan} loading={replanning}><RefreshCw /> Replan now</Button>}
        </div>
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Do next</p>
          {nextTask ? (
            <>
              <p className="font-semibold">{nextTask.title}</p>
              <p className="text-sm text-muted-foreground">{nextTask.estimated_hours} h{nextTask.skill ? ` · ${nextTask.skill}` : ""} · planned for {formatDate(nextTask.scheduled_date, { year: undefined })}</p>
              {week && <p className="text-xs text-muted-foreground">This week: {week.completed_hours}/{week.planned_hours} h done</p>}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No pending tasks.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
