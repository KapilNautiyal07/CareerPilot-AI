"use client";

import { ArrowRight, Bell, CalendarClock, Compass, Play, Sparkles, TrendingUp } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { GapHistoryChart, TrendChart } from "@/components/charts";
import { GapBadge, ScoreRing, Stat } from "@/components/domain";
import { JourneySteps } from "@/components/journey";
import { TaskChecklist } from "@/components/task-checklist";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Progress } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingCards, PageHeader } from "@/components/ui/states";
import { WorkflowPanel } from "@/components/workflow-panel";
import { errorMessage } from "@/lib/api";
import { useJourney } from "@/lib/journey";
import { useDashboard, useMe, useProfile, useStartAnalysis, useTodayTasks } from "@/lib/queries";
import { formatDate, pct, titleCase } from "@/lib/utils";

export default function DashboardPage() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  );
}

function Dashboard() {
  const params = useSearchParams();
  const router = useRouter();
  const dash = useDashboard();
  const me = useMe();
  const profile = useProfile();
  const today = useTodayTasks();
  const start = useStartAnalysis();
  const journey = useJourney();
  const [showReadiness, setShowReadiness] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const last = dash.data?.last_workflow;
  const activeRun =
    params.get("run") ?? (last && ["queued", "running", "awaiting_input"].includes(last.status) ? last.id : null);

  async function run() {
    setStartError(null);
    try {
      const { execution_id } = await start.mutateAsync({});
      router.replace(`/dashboard?run=${execution_id}`);
    } catch (err) {
      setStartError(errorMessage(err));
    }
  }

  const hasResume = !!profile.data?.active_resume_id || (profile.data?.skills.length ?? 0) > 0;
  const d = dash.data;
  const readiness = d?.readiness.latest;

  return (
    <>
      <PageHeader
        title={me.data?.full_name ? `Welcome back, ${me.data.full_name.split(" ")[0]}` : "Your progress"}
        description={profile.data?.target_role ? `Preparing for ${profile.data.target_role}.` : undefined}
        actions={
          <Button onClick={run} loading={start.isPending} disabled={!hasResume}>
            <Play /> {d?.roadmap ? "Re-run analysis" : "Run career analysis"}
          </Button>
        }
      />
      {startError && <Alert tone="error" className="mb-4">{startError}</Alert>}
      {activeRun && (
        <div className="mb-6">
          <WorkflowPanel key={activeRun} executionId={activeRun} onFinished={() => { void dash.refetch(); void today.refetch(); }} />
        </div>
      )}

      {journey.ready && journey.next && (
        <Card className="mb-6">
          <CardContent className="space-y-4 pt-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-full bg-primary-soft text-primary"><Sparkles className="size-4" aria-hidden /></span>
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Your next step</p>
                  <p className="font-semibold">{journey.next.title}</p>
                  <p className="text-sm text-muted-foreground">{journey.next.why}</p>
                </div>
              </div>
              {journey.next.href === "run-analysis" ? (
                <Button onClick={run} loading={start.isPending} className="shrink-0"><Play /> {journey.next.cta}</Button>
              ) : journey.next.href.startsWith("#") ? (
                <Button asChild className="shrink-0"><a href={journey.next.href}>{journey.next.cta} <ArrowRight /></a></Button>
              ) : (
                <Button asChild className="shrink-0"><Link href={journey.next.href}>{journey.next.cta} <ArrowRight /></Link></Button>
              )}
            </div>
            <JourneySteps stages={journey.stages} current={journey.current} />
          </CardContent>
        </Card>
      )}

      {dash.isLoading ? (
        <LoadingCards count={6} />
      ) : dash.error ? (
        <ErrorState error={dash.error} onRetry={() => dash.refetch()} />
      ) : !d?.roadmap && !d?.skill_gaps.total ? (
        <EmptyState
          icon={Compass}
          title="Your dashboard fills in after the first analysis"
          description="Readiness, matching jobs, skill coverage and today's tasks appear here once your skill gaps and roadmap exist."
        />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card className="sm:col-span-2 lg:col-span-1">
              <CardContent className="flex items-center gap-4 pt-5">
                <ScoreRing value={readiness?.score} label="Readiness" />
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Readiness</p>
                  {readiness?.delta !== null && readiness?.delta !== undefined && (
                    <p className={readiness.delta >= 0 ? "text-sm text-success" : "text-sm text-danger"}>
                      {readiness.delta >= 0 ? "+" : ""}
                      {readiness.delta.toFixed(1)} since last evaluation
                    </p>
                  )}
                  <button type="button" className="text-xs text-primary hover:underline" onClick={() => setShowReadiness((v) => !v)} aria-expanded={showReadiness}>
                    {showReadiness ? "Hide" : "How is this calculated?"}
                  </button>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-5">
                <Stat label="Relevant jobs" value={d.jobs.relevant} hint={`${d.jobs.discovered} discovered · ${d.jobs.saved} saved${d.jobs.demo ? " · demo" : ""}`} />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-5">
                <Stat label="Applications" value={d.applications.applied + d.applications.interviewing + d.applications.offered} hint={`${d.applications.interviewing} interviewing · ${d.applications.offered} offers`} />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="space-y-2 pt-5">
                <Stat label="Roadmap" value={`${Math.round(d.roadmap?.percent ?? 0)}%`} hint={`${d.roadmap?.completed_hours ?? 0} of ${d.roadmap?.total_hours ?? 0} h${d.overdue_tasks ? ` · ${d.overdue_tasks} overdue` : ""}`} />
                <Progress value={d.roadmap?.percent ?? 0} label="Roadmap progress" />
              </CardContent>
            </Card>
          </div>

          {showReadiness && readiness && (
            <Card>
              <CardHeader>
                <CardTitle>Readiness breakdown</CardTitle>
                <CardDescription>{readiness.disclaimer}</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-2">
                {readiness.components.map((c) => (
                  <div key={c.name} className="space-y-1.5">
                    <div className="flex justify-between text-sm">
                      <span className="font-medium">{titleCase(c.name)} <span className="text-muted-foreground">(weight {c.weight})</span></span>
                      <span className="tabular-nums">{Math.round(c.value)}</span>
                    </div>
                    <Progress value={c.value} label={titleCase(c.name)} />
                    <p className="text-xs text-muted-foreground">{c.explanation}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          <div className="grid gap-6 lg:grid-cols-3">
            <Card id="today" className="scroll-mt-6 lg:col-span-2">
              <CardHeader>
                <CardTitle>Today&apos;s tasks</CardTitle>
                <CardDescription>From your roadmap. Mark tasks done to track progress.</CardDescription>
              </CardHeader>
              <CardContent>
                {today.data?.length ? (
                  <TaskChecklist tasks={today.data} />
                ) : (
                  <p className="text-sm text-muted-foreground">Nothing scheduled for today. <Link className="text-primary hover:underline" href="/roadmap">View the full roadmap</Link></p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Skill coverage</CardTitle>
                <CardDescription>{d.skill_gaps.matched} of {d.skill_gaps.total} demanded skills matched ({pct(d.skill_gaps.completion)})</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex gap-2 text-xs">
                  <Badge variant="success">{d.skill_gaps.matched} matched</Badge>
                  <Badge variant="warning">{d.skill_gaps.partial} partial</Badge>
                  <Badge variant="danger">{d.skill_gaps.missing} missing</Badge>
                </div>
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Top priorities</p>
                <ul className="space-y-1.5">
                  {d.remaining_priorities.map((p) => (
                    <li key={p.skill} className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate">{p.skill}</span>
                      <GapBadge status={p.status} />
                    </li>
                  ))}
                </ul>
                <Button asChild variant="outline" size="sm" className="w-full"><Link href="/gaps">See all skill gaps</Link></Button>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><TrendingUp className="size-4 text-primary" /> Readiness & interview trend</CardTitle>
                <CardDescription>Readiness is recorded after each analysis; interview scores after each session.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {d.readiness.trend.length > 1 ? <TrendChart data={d.readiness.trend} label="Readiness" /> : <p className="text-sm text-muted-foreground">A trend appears after your next evaluation.</p>}
                {d.interviews.trend.length > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Mock interviews ({d.interviews.completed})</p>
                    <TrendChart data={d.interviews.trend.map((t) => ({ date: t.date, score: t.score }))} label="Interview score" height={160} />
                  </div>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Skill gaps over time</CardTitle>
                <CardDescription>Each bar is one analysis snapshot.</CardDescription>
              </CardHeader>
              <CardContent>
                <GapHistoryChart data={d.skill_gaps.history} />
                {d.recent_improvements.length > 0 && (
                  <div className="mt-3 space-y-1">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Recent improvements</p>
                    {d.recent_improvements.map((i) => (
                      <p key={i.skill} className="text-sm">{i.skill}: {i.from} → <span className="font-medium text-success">{i.to}</span></p>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><CalendarClock className="size-4 text-primary" /> Upcoming milestones</CardTitle></CardHeader>
              <CardContent>
                {d.upcoming_milestones.length ? (
                  <ul className="space-y-3">
                    {d.upcoming_milestones.map((m) => (
                      <li key={m.week} className="flex gap-3 text-sm">
                        <span className="w-16 shrink-0 text-xs text-muted-foreground">Week {m.week}</span>
                        <span>{m.milestone}<span className="block text-xs text-muted-foreground">by {formatDate(m.end)}</span></span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-muted-foreground">No upcoming milestones.</p>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><Bell className="size-4 text-primary" /> Application reminders</CardTitle></CardHeader>
              <CardContent>
                {d.reminders.length ? (
                  <ul className="space-y-2">
                    {d.reminders.map((r) => (
                      <li key={r.application_id} className="flex items-start justify-between gap-2 text-sm">
                        <span className="min-w-0"><span className="font-medium">{r.company}</span> · {r.title}<span className="block text-xs text-muted-foreground">{r.next_step || "Next step"}</span></span>
                        <Badge variant={r.overdue ? "danger" : "muted"}>{r.overdue ? "Overdue" : formatDate(r.due, { year: undefined })}</Badge>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-muted-foreground">No next steps due in the coming week. <Link className="text-primary hover:underline" href="/applications">Open tracker</Link></p>}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
