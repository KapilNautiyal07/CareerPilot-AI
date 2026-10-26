"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bookmark, BookmarkCheck, ExternalLink, Info, PlusCircle } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";

import { GapBadge, QualityFlags, ScoreRing } from "@/components/domain";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Progress } from "@/components/ui/primitives";
import { ErrorState, LoadingCards } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { keys, useJob } from "@/lib/queries";
import { formatDateTime, formatSalary, titleCase } from "@/lib/utils";

export default function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const jobId = Number(id);
  const job = useJob(jobId);
  const qc = useQueryClient();
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setNotice(null);
    try {
      await fn();
      setNotice({ tone: "success", text: ok });
      await Promise.all([keys.job(jobId), ["jobs"], keys.applications].map((k) => qc.invalidateQueries({ queryKey: k })));
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
    }
  }

  if (job.isLoading) return <LoadingCards count={3} className="sm:grid-cols-1 lg:grid-cols-1" />;
  if (job.error || !job.data) return <ErrorState error={job.error} onRetry={() => job.refetch()} />;
  const j = job.data;
  const m = j.match;
  const salary = formatSalary(j.salary_min, j.salary_max, j.salary_currency);

  return (
    <div className="space-y-6">
      <Link href="/jobs" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> All jobs</Link>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{j.title}</h1>
          <p className="text-muted-foreground">{j.company} · {j.location || "Location not stated"} · {titleCase(j.work_arrangement)}{salary ? ` · ${salary}` : ""}</p>
          <div className="mt-2"><QualityFlags flags={j.quality_flags} /></div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => run(() => api(`/jobs/${j.id}/save`, { method: j.is_saved ? "DELETE" : "POST" }), j.is_saved ? "Removed from saved jobs." : "Saved and added to your tracker.")}>
            {j.is_saved ? <BookmarkCheck className="text-primary" /> : <Bookmark />} {j.is_saved ? "Saved" : "Save"}
          </Button>
          {!j.application_status && (
            <Button variant="outline" onClick={() => run(() => api("/applications", { body: { job_id: j.id, status: "planned" } }), "Added to your application tracker as planned.")}>
              <PlusCircle /> Track application
            </Button>
          )}
          {j.application_url && (
            <Button asChild>
              <a href={j.application_url} target="_blank" rel="noopener noreferrer">Open listing <ExternalLink /></a>
            </Button>
          )}
        </div>
      </div>
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
      <Alert tone={j.is_demo ? "warning" : "info"} title="Availability">
        {j.availability_note} CareerPilot never submits applications for you. Last checked {formatDateTime(j.last_checked_at)}.
      </Alert>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Match breakdown</CardTitle><CardDescription>Deterministic scoring; see the formula in the docs.</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-4"><ScoreRing value={m?.score} label="Overall match" /><p className="text-sm text-muted-foreground">Overall = 60% skills, 15% experience, 15% preferences, 10% recency.</p></div>
            {m && [["Skills", m.skill_score], ["Experience", m.experience_score], ["Preferences", m.preference_score]].map(([label, v]) => (
              <div key={label as string} className="space-y-1">
                <div className="flex justify-between text-sm"><span>{label}</span><span className="tabular-nums">{Math.round(v as number)}</span></div>
                <Progress value={v as number} label={label as string} />
              </div>
            ))}
            {m?.notes.map((n) => <p key={n} className="flex gap-1.5 text-xs text-muted-foreground"><Info className="mt-0.5 size-3 shrink-0" />{n}</p>)}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Skill comparison</CardTitle><CardDescription>Required versus preferred requirements and the evidence behind each result.</CardDescription></CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full min-w-[32rem] text-sm">
              <thead><tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground"><th className="py-2 pr-3 font-medium">Skill</th><th className="py-2 pr-3 font-medium">Requirement</th><th className="py-2 pr-3 font-medium">You</th><th className="py-2 font-medium">Why</th></tr></thead>
              <tbody>
                {(m?.details ?? []).map((d) => (
                  <tr key={d.skill} className="border-b last:border-0 align-top">
                    <td className="py-2 pr-3 font-medium">{d.skill}</td>
                    <td className="py-2 pr-3"><Badge variant={d.level === "required" ? "default" : "outline"}>{titleCase(d.level)}</Badge></td>
                    <td className="py-2 pr-3"><GapBadge status={d.status} /></td>
                    <td className="py-2 text-xs text-muted-foreground">{d.explanation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!m && <p className="text-sm text-muted-foreground">Save the job or run the analysis to compute a match.</p>}
          </CardContent>
        </Card>
      </div>

      {j.tailoring && (
        <Card>
          <CardHeader><CardTitle>Resume tailoring for this job</CardTitle><CardDescription>{j.tailoring.disclaimer}</CardDescription></CardHeader>
          <CardContent>
            <ul className="grid gap-2 md:grid-cols-2">
              {j.tailoring.suggestions.map((s, i) => (
                <li key={i} className="rounded-md border p-3 text-sm"><Badge variant="muted" className="mb-1">{titleCase(s.kind)}</Badge><p>{s.suggestion}</p><p className="mt-1 text-xs text-muted-foreground">{s.rationale}</p></li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Job description</CardTitle><CardDescription>Shown as plain text from the source{j.is_demo ? " (fictional demo listing)" : ""}.</CardDescription></CardHeader>
        <CardContent><div className="whitespace-pre-wrap text-sm leading-relaxed">{j.description}</div></CardContent>
      </Card>
    </div>
  );
}
