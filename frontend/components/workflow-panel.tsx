"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowRight, CheckCircle2, Circle, Loader2, PauseCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Progress, Select } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { keys, useInvalidateCareerData, useWorkflow } from "@/lib/queries";
import { cn } from "@/lib/utils";
import type { Workflow, WorkflowStep } from "@/types/api";

/** Plain-language phases; each groups one or more graph nodes. */
interface Phase {
  label: string;
  doing: string;
  nodes: string[];
}

const CAREER_PHASES: Phase[] = [
  { label: "Read your profile", doing: "Checking your profile, study hours and deadline…", nodes: ["validate_request", "load_profile"] },
  { label: "Understand your resume", doing: "Pulling skills and experience out of your resume…", nodes: ["resume_analysis"] },
  { label: "Confirm your target role", doing: "Working out which role you are preparing for…", nodes: ["determine_target"] },
  { label: "Search job listings", doing: "Collecting job listings and learning resources for your role…", nodes: ["job_research", "resource_retrieval"] },
  { label: "Find your skill gaps", doing: "Comparing what jobs ask for with what you already have…", nodes: ["skill_gap"] },
  { label: "Build your weekly plan", doing: "Fitting the most important skills into your weekly hours…", nodes: ["roadmap", "revise"] },
  { label: "Double-check the plan", doing: "Making sure the plan fits your hours and deadline…", nodes: ["evaluate"] },
  { label: "Save results", doing: "Saving your skill gaps and roadmap…", nodes: ["persist"] },
];

const REPLAN_PHASES: Phase[] = [
  { label: "Load your progress", doing: "Reading completed tasks and assessment scores…", nodes: ["load_progress"] },
  { label: "Re-check skill gaps", doing: "Updating which skills still need work…", nodes: ["reevaluate_gaps"] },
  { label: "Update your plan", doing: "Moving unfinished work into the remaining weeks…", nodes: ["update_roadmap", "revise_progress"] },
  { label: "Double-check the plan", doing: "Making sure the plan still fits your hours and deadline…", nodes: ["evaluate_progress"] },
  { label: "Save the new plan", doing: "Saving the updated roadmap…", nodes: ["persist_progress"] },
];

const NODE_LABELS: Record<string, string> = {
  validate_request: "Validate request",
  load_profile: "Load candidate profile",
  resume_analysis: "Resume Analysis Agent",
  determine_target: "Determine target role & constraints",
  job_research: "Job Research Agent",
  resource_retrieval: "Career resource retrieval",
  skill_gap: "Skill Gap Analysis Agent",
  roadmap: "Learning Roadmap Agent",
  evaluate: "Career Evaluation Agent",
  revise: "Bounded revision",
  persist: "Persist results",
  load_progress: "Load progress & assessments",
  reevaluate_gaps: "Re-evaluate skill gaps",
  update_roadmap: "Update roadmap",
  evaluate_progress: "Career Evaluation Agent",
  revise_progress: "Bounded revision",
  persist_progress: "Persist updated plan",
};

type PhaseStatus = "pending" | WorkflowStep["status"];

/** Status of a phase from the latest step of each of its nodes (revision loops re-run nodes). */
function phaseStatus(phase: Phase, wf: Workflow): PhaseStatus {
  const latest = phase.nodes
    .map((n) => [...wf.steps].reverse().find((s) => s.node === n)?.status)
    .filter((s): s is WorkflowStep["status"] => !!s);
  if (latest.includes("failed")) return "failed";
  if (latest.includes("awaiting_input")) return "awaiting_input";
  if (latest.includes("running")) return "running";
  if (latest.length === 0) return wf.status === "succeeded" || wf.status === "partial" ? "succeeded" : "pending";
  return latest.includes("partial") ? "partial" : "succeeded";
}

function StepIcon({ status }: { status: PhaseStatus }) {
  if (status === "running") return <Loader2 className="size-4 animate-spin text-primary" aria-label="In progress" />;
  if (status === "succeeded") return <CheckCircle2 className="size-4 text-success" aria-label="Done" />;
  if (status === "partial") return <AlertCircle className="size-4 text-warning" aria-label="Done with warnings" />;
  if (status === "failed") return <AlertCircle className="size-4 text-danger" aria-label="Failed" />;
  if (status === "awaiting_input") return <PauseCircle className="size-4 text-warning" aria-label="Waiting for you" />;
  return <Circle className="size-4 text-muted-foreground/50" aria-label="Not started" />;
}

export function WorkflowPanel({ executionId, onFinished }: { executionId: string; onFinished?: () => void }) {
  const { data: wf, error } = useWorkflow(executionId);
  const invalidate = useInvalidateCareerData();
  const qc = useQueryClient();
  const notified = useRef(false);
  const [role, setRole] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [inputError, setInputError] = useState<string | null>(null);

  useEffect(() => {
    if (!wf || notified.current) return;
    if (["succeeded", "partial", "failed"].includes(wf.status)) {
      notified.current = true;
      invalidate();
      onFinished?.();
    }
  }, [wf, invalidate, onFinished]);

  if (error) return <Alert tone="error" title="Lost track of the workflow">{errorMessage(error)}</Alert>;
  if (!wf) return null;

  const isCareer = wf.kind === "career_analysis";
  const phases = isCareer ? CAREER_PHASES : REPLAN_PHASES;
  const statuses = phases.map((p) => phaseStatus(p, wf));
  const running = wf.status === "queued" || wf.status === "running";
  const finishedOk = wf.status === "succeeded" || wf.status === "partial";
  const doneCount = statuses.filter((s) => s === "succeeded" || s === "partial").length;
  const percent = finishedOk ? 100 : Math.round((100 * doneCount) / phases.length);
  const activeIdx = statuses.findIndex((s) => s === "running" || s === "awaiting_input" || s === "failed");
  const nowIdx = activeIdx !== -1 ? activeIdx : Math.min(doneCount, phases.length - 1);
  const title = isCareer ? "Career analysis" : "Roadmap update";

  async function submitRole(e: React.FormEvent) {
    e.preventDefault();
    setInputError(null);
    setSubmitting(true);
    try {
      await api(`/workflows/${executionId}/input`, { body: { target_role: role } });
      notified.current = false;
      await qc.invalidateQueries({ queryKey: keys.workflow(executionId) });
    } catch (err) {
      setInputError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  const headline = running
    ? wf.status === "queued"
      ? "Starting…"
      : phases[nowIdx].doing
    : wf.status === "awaiting_input"
      ? "Waiting for your answer below"
      : wf.status === "failed"
        ? `Stopped at: ${phases[nowIdx].label.toLowerCase()}`
        : wf.status === "partial"
          ? "Finished, with a few things to review"
          : "All done";

  return (
    <Card aria-live="polite" className={cn(finishedOk && "border-success/40", wf.status === "failed" && "border-danger/40")}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {running && <Loader2 className="size-4 animate-spin text-primary" aria-hidden />}
          {finishedOk && <CheckCircle2 className="size-4 text-success" aria-hidden />}
          {wf.status === "failed" && <AlertCircle className="size-4 text-danger" aria-hidden />}
          {title}
        </CardTitle>
        <CardDescription className="text-sm text-foreground">{headline}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Progress value={percent} tone={finishedOk ? "success" : undefined} label={`${title} progress`} />
          <p className="text-xs text-muted-foreground">
            {finishedOk ? `${phases.length} of ${phases.length} steps` : `Step ${nowIdx + 1} of ${phases.length}`}
            {running && " · you can leave this page; it keeps running and resumes automatically"}
          </p>
        </div>

        <ol className="grid gap-1.5 sm:grid-cols-2">
          {phases.map((p, i) => (
            <li key={p.label} className={cn("flex items-center gap-2 text-sm", statuses[i] === "pending" && "text-muted-foreground")}>
              <StepIcon status={statuses[i]} />
              <span className={cn(statuses[i] === "running" && "font-medium")}>{p.label}</span>
            </li>
          ))}
        </ol>

        {wf.status === "awaiting_input" && wf.pending_question && (
          <form onSubmit={submitRole} className="flex flex-col gap-2 rounded-md border bg-muted/50 p-3 sm:flex-row sm:items-end">
            <div className="flex flex-1 flex-col gap-1.5">
              <Label htmlFor="wf-role">{wf.pending_question.question}</Label>
              <Select id="wf-role" value={role} onChange={(e) => setRole(e.target.value)} required>
                <option value="" disabled>
                  Choose a role…
                </option>
                {wf.pending_question.options.map((o) => (
                  <option key={o}>{o}</option>
                ))}
              </Select>
            </div>
            <span className="text-center text-xs text-muted-foreground sm:pb-2">or</span>
            <Input aria-label="Other role" placeholder="Type another role" onChange={(e) => setRole(e.target.value)} className="sm:w-48" />
            <Button type="submit" loading={submitting} disabled={role.trim().length < 2}>
              Continue
            </Button>
          </form>
        )}
        {inputError && <Alert tone="error">{inputError}</Alert>}

        {finishedOk && (
          <div className="space-y-3 rounded-md border bg-muted/40 p-3">
            {wf.result?.explanation && <p className="text-sm">{wf.result.explanation}</p>}
            <div className="flex flex-wrap gap-2">
              {isCareer && (
                <Button asChild size="sm" variant="outline"><Link href="/gaps">See your skill gaps <ArrowRight /></Link></Button>
              )}
              <Button asChild size="sm"><Link href="/roadmap">Open your roadmap <ArrowRight /></Link></Button>
            </div>
          </div>
        )}
        {wf.result?.warnings && wf.result.warnings.length > 0 && !running && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Good to know</p>
            <ul className="list-inside list-disc text-xs text-muted-foreground">
              {wf.result.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}
        {wf.status === "partial" && wf.result?.issues?.some((i) => i.severity === "error") && (
          <Alert tone="warning" title="Some checks did not pass">
            {wf.result.issues.filter((i) => i.severity === "error").map((i) => i.message).join(" ")}
          </Alert>
        )}
        {wf.status === "failed" && (
          <Alert tone="error" title="Something went wrong">
            {wf.error?.message} You can fix the issue and run it again.
          </Alert>
        )}

        {wf.steps.length > 0 && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none hover:text-foreground">Technical details</summary>
            <ol className="mt-2 space-y-1">
              {wf.steps.map((s, i) => (
                <li key={`${s.node}-${i}`} className="flex items-center gap-2">
                  <StepIcon status={s.status} />
                  <span>{NODE_LABELS[s.node] ?? s.node}</span>
                  {s.duration_ms !== undefined && <span>{(s.duration_ms / 1000).toFixed(1)}s</span>}
                  {s.detail && <span className="truncate">· {s.detail}</span>}
                </li>
              ))}
            </ol>
          </details>
        )}
      </CardContent>
    </Card>
  );
}
