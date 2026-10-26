"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Lightbulb, Send, Square } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";

import { CompetencyBars } from "@/components/charts";
import { ScoreRing } from "@/components/domain";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Label, Progress, Textarea } from "@/components/ui/primitives";
import { ErrorState, LoadingCards } from "@/components/ui/states";
import { WorkflowPanel } from "@/components/workflow-panel";
import { api, errorMessage } from "@/lib/api";
import { keys, useInterview } from "@/lib/queries";
import { scoreTone, titleCase } from "@/lib/utils";
import type { AnswerOutResponse, Question, QuestionResponse } from "./types";

export default function InterviewWorkspace({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const sessionId = Number(id);
  const session = useInterview(sessionId);
  const qc = useQueryClient();
  const [answer, setAnswer] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"answer" | "hint" | "end" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastFeedback, setLastFeedback] = useState<{ question: Question; response: QuestionResponse } | null>(null);
  const [replanId, setReplanId] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = session.data?.current_question;
    if (!q) return;
    setBusy("answer");
    setError(null);
    try {
      const r = await api<AnswerOutResponse>(`/interviews/${sessionId}/answer`, { body: { answer, code: code || null } });
      qc.setQueryData(keys.interview(sessionId), r.session);
      setLastFeedback({ question: q, response: r.evaluation });
      if (r.replan_execution_id) setReplanId(r.replan_execution_id);
      setAnswer("");
      setCode("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function hint() {
    setBusy("hint");
    setError(null);
    try {
      await api(`/interviews/${sessionId}/hint`, { method: "POST" });
      await session.refetch();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function end() {
    if (!confirm("End this interview now? Unanswered questions will not be scored.")) return;
    setBusy("end");
    try {
      const r = await api<AnswerOutResponse>(`/interviews/${sessionId}/end`, { method: "POST" });
      qc.setQueryData(keys.interview(sessionId), r.session);
      if (r.replan_execution_id) setReplanId(r.replan_execution_id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (session.isLoading) return <LoadingCards count={2} className="sm:grid-cols-1 lg:grid-cols-1" />;
  if (session.error || !session.data) return <ErrorState error={session.error} onRetry={() => session.refetch()} />;
  const s = session.data;
  const q = s.current_question;
  const answered = s.questions.filter((x) => x.response).length;

  return (
    <div className="space-y-6">
      <Link href="/interviews" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Interviews</Link>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{titleCase(s.category)} interview</h1>
          <p className="text-sm text-muted-foreground">{s.target_role} · {s.difficulty} · {answered} answered</p>
        </div>
        {s.status === "in_progress" && <Button variant="outline" onClick={end} loading={busy === "end"}><Square /> End interview</Button>}
      </div>
      {error && <Alert tone="error">{error}</Alert>}

      {lastFeedback && <FeedbackCard question={lastFeedback.question} response={lastFeedback.response} />}

      {s.status === "in_progress" && q && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <Badge>Question {q.order_index + 1}</Badge>
              {q.is_follow_up && <Badge variant="warning">Follow-up</Badge>}
              <Badge variant="muted">{titleCase(q.kind)}</Badge>
              {q.skills.map((sk) => <Badge key={sk} variant="outline">{sk}</Badge>)}
            </div>
            <CardTitle className="mt-2 text-lg leading-snug">{q.prompt}</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-4">
              {q.hints_revealed.length > 0 && (
                <Alert tone="info" title="Hints (each hint lowers this question's score by 5 points)">
                  <ul className="list-inside list-disc">{q.hints_revealed.map((h) => <li key={h}>{h}</li>)}</ul>
                </Alert>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="answer">{q.kind === "code" ? "Explain your approach and complexity" : "Your answer"}</Label>
                <Textarea id="answer" rows={q.kind === "code" ? 4 : 8} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={q.kind === "behavioral" ? "Situation, Task, Action, Result…" : "Think out loud: explain your reasoning, trade-offs and edge cases."} />
              </div>
              {q.kind === "code" && (
                <div className="space-y-1.5">
                  <Label htmlFor="code">Code (Python) — define <code className="font-mono">{q.code_function}</code></Label>
                  <Textarea id="code" rows={10} spellCheck={false} className="font-mono text-xs" value={code} onChange={(e) => setCode(e.target.value)} placeholder={`def ${q.code_function}(...):\n    ...`} />
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" loading={busy === "answer"} disabled={!answer.trim() && !code.trim()}><Send /> Submit answer</Button>
                <Button type="button" variant="ghost" onClick={hint} loading={busy === "hint"} disabled={q.hints_revealed.length >= q.hints_available}>
                  <Lightbulb /> Hint ({q.hints_available - q.hints_revealed.length} left)
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {s.status !== "in_progress" && <Results session={s} />}
      {replanId && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">Your results are being used to re-evaluate skill gaps and update your roadmap:</p>
          <WorkflowPanel executionId={replanId} />
        </div>
      )}

      {answered > 0 && (
        <Card>
          <CardHeader><CardTitle>Answers so far</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {s.questions.filter((x) => x.response).map((x) => (
              <details key={x.id} className="rounded-md border p-3">
                <summary className="flex cursor-pointer items-center justify-between gap-2 text-sm font-medium">
                  <span className="truncate">{x.is_follow_up ? "↳ " : ""}{x.prompt}</span>
                  <Badge variant={scoreToneBadge(x.response!.score)}>{Math.round(x.response!.score)}</Badge>
                </summary>
                <div className="mt-3"><FeedbackBody response={x.response!} /></div>
              </details>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function scoreToneBadge(v: number) {
  const t = scoreTone(v);
  return t === "muted" ? "muted" : t;
}

function FeedbackCard({ question, response }: { question: Question; response: QuestionResponse }) {
  return (
    <Card className="border-primary/30" aria-live="polite">
      <CardHeader>
        <CardTitle>Feedback on your last answer</CardTitle>
        <CardDescription className="line-clamp-2">{question.prompt}</CardDescription>
      </CardHeader>
      <CardContent><FeedbackBody response={response} /></CardContent>
    </Card>
  );
}

function FeedbackBody({ response }: { response: QuestionResponse }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <ScoreRing value={response.score} size={56} label="Answer score" />
        <p className="text-sm">{response.feedback}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {response.criteria.map((c) => (
          <div key={c.id} className="space-y-1">
            <div className="flex justify-between gap-2 text-xs"><span>{c.description}</span><span className="tabular-nums text-muted-foreground">{Math.round(c.met * 100)}%</span></div>
            <Progress value={c.met * 100} tone={c.met >= 0.7 ? "success" : c.met >= 0.4 ? "warning" : "danger"} label={c.description} />
            {c.evidence && <p className="truncate text-xs text-muted-foreground" title={c.evidence}>&ldquo;{c.evidence}&rdquo;</p>}
          </div>
        ))}
      </div>
      {response.code_execution && (
        <Alert tone={response.code_execution.status === "passed" ? "success" : response.code_execution.status === "not_executed" ? "info" : "warning"} title={`Code: ${titleCase(response.code_execution.status)}${response.code_execution.total ? ` (${response.code_execution.passed}/${response.code_execution.total} tests)` : ""}`}>
          {response.code_execution.detail}
          {response.code_execution.static_issues.length > 0 && <ul className="mt-1 list-inside list-disc">{response.code_execution.static_issues.map((i) => <li key={i}>{i}</li>)}</ul>}
        </Alert>
      )}
      {response.mistakes.length > 0 && <ul className="list-inside list-disc text-sm text-muted-foreground">{response.mistakes.map((m) => <li key={m}>{m}</li>)}</ul>}
      <p className="text-xs text-muted-foreground">Graded by {response.evaluator}{response.hints_used ? ` · ${response.hints_used} hint(s) used` : ""}.</p>
    </div>
  );
}

function Results({ session }: { session: NonNullable<ReturnType<typeof useInterview>["data"]> }) {
  const sum = session.summary;
  if (session.status === "abandoned") return <Alert tone="info" title="Interview ended">No answers were submitted, so there is nothing to score.</Alert>;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Results</CardTitle>
        <CardDescription>{sum.disclaimer}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-3">
        <div className="flex flex-col items-center gap-2">
          <ScoreRing value={session.overall_score} size={96} label="Overall interview score" />
          <p className="text-sm text-muted-foreground">Overall score</p>
        </div>
        <div className="lg:col-span-2">
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Competencies</h3>
          {sum.competencies && <CompetencyBars data={sum.competencies} />}
        </div>
        <div className="lg:col-span-3 grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Recurring weaknesses</h3>
            {sum.recurring_weaknesses?.length ? <ul className="list-inside list-disc text-sm">{sum.recurring_weaknesses.map((w) => <li key={w}>{w}</li>)}</ul> : <p className="text-sm text-muted-foreground">None repeated across questions.</p>}
          </div>
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Recommended practice</h3>
            <ul className="list-inside list-disc text-sm">{sum.recommendations?.map((r) => <li key={r}>{r}</li>)}</ul>
            <Button asChild variant="outline" size="sm" className="mt-3"><Link href="/roadmap">Open updated roadmap</Link></Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
