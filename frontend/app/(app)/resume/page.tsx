"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Check, FileText, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { useState } from "react";

import { GapBadge } from "@/components/domain";
import { ResumeUploader } from "@/components/resume-uploader";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Textarea } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingCards, PageHeader } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { keys, useProfile, useResume, useResumes } from "@/lib/queries";
import { formatDate, formatDateTime, titleCase } from "@/lib/utils";
import type { Experience, Profile, Skill, Tailoring } from "@/types/api";

const LEVEL_ORDER: Skill["evidence_level"][] = ["assessed", "demonstrated", "user_asserted", "listed", "inferred"];
const LEVEL_TEXT: Record<Skill["evidence_level"], string> = {
  assessed: "Assessed in CareerPilot",
  demonstrated: "Demonstrated in work or projects",
  user_asserted: "Added by you",
  listed: "Listed only (no described usage)",
  inferred: "Inferred from related skills",
};

export default function ResumePage() {
  const profile = useProfile();
  const resumes = useResumes();
  const active = useResume(profile.data?.active_resume_id);
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await Promise.all([keys.profile, keys.resumes, ["resume"]].map((k) => qc.invalidateQueries({ queryKey: k })));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const analysis = active.data?.analysis;
  return (
    <>
      <PageHeader title="Resume analysis" description="Review what the Resume Analysis Agent extracted. Everything links back to text in your resume, and you can correct it." />
      {error && <Alert tone="error" className="mb-4">{error}</Alert>}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-1">
          <ResumeUploader />
          <Card>
            <CardHeader><CardTitle>Your resumes</CardTitle></CardHeader>
            <CardContent>
              {resumes.isLoading ? <LoadingCards count={1} className="sm:grid-cols-1 lg:grid-cols-1" /> : resumes.data?.length ? (
                <ul className="space-y-2">
                  {resumes.data.map((r) => (
                    <li key={r.id} className="flex items-center gap-2 rounded-md border p-2 text-sm">
                      <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">{r.original_filename}</p>
                        <p className="text-xs text-muted-foreground">{formatDateTime(r.uploaded_at)} · {r.status}</p>
                      </div>
                      {r.is_active && <Badge>Active</Badge>}
                      <Button variant="ghost" size="icon" aria-label={`Delete ${r.original_filename}`} onClick={() => confirm("Delete this resume and its file?") && act(() => api(`/resumes/${r.id}`, { method: "DELETE" }))}>
                        <Trash2 />
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : <p className="text-sm text-muted-foreground">No resumes uploaded yet.</p>}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6 lg:col-span-2">
          {profile.isLoading || active.isLoading ? <LoadingCards count={2} className="sm:grid-cols-1 lg:grid-cols-1" /> : profile.error ? <ErrorState error={profile.error} /> : !analysis ? (
            <EmptyState icon={FileText} title="No analyzed resume yet" description="Upload a resume to see extracted skills, experience and suggested improvements." />
          ) : (
            <>
              <Card>
                <CardHeader>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <CardTitle>{analysis.candidate_name ?? "Extracted profile"}</CardTitle>
                      <CardDescription>
                        {analysis.total_years_experience !== null ? `${analysis.total_years_experience} years of dated experience · ` : ""}
                        Extraction: {analysis.extraction_method === "llm_grounded" ? "LLM, verified against your resume" : "deterministic"}
                      </CardDescription>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => act(() => api(`/resumes/${active.data!.id}/reviewed`, { method: "POST" }))}>
                      <Check /> Mark as reviewed
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {analysis.summary && <p className="text-sm">{analysis.summary}</p>}
                  {analysis.security_flags.length > 0 && (
                    <Alert tone="warning" title="Instruction-like text was found in your resume">
                      These passages were ignored during analysis: {analysis.security_flags.map((f) => titleCase(f.code)).join(", ")}.
                    </Alert>
                  )}
                  {analysis.warnings.map((w) => <Alert key={w} tone="info">{w}</Alert>)}
                </CardContent>
              </Card>

              <SkillsCard profile={profile.data!} onAct={act} />
              <ExperienceCard experiences={profile.data!.experiences} onAct={act} />

              <div className="grid gap-6 md:grid-cols-2">
                <Card>
                  <CardHeader><CardTitle>Missing information</CardTitle><CardDescription>Gaps in the resume itself.</CardDescription></CardHeader>
                  <CardContent>
                    {analysis.missing_information.length ? (
                      <ul className="list-inside list-disc space-y-1 text-sm">{analysis.missing_information.map((m) => <li key={m}>{m}</li>)}</ul>
                    ) : <p className="text-sm text-muted-foreground">Nothing obvious is missing.</p>}
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader><CardTitle>Unclear claims</CardTitle><CardDescription>Statements that need evidence.</CardDescription></CardHeader>
                  <CardContent>
                    {analysis.unclear_claims.length ? (
                      <ul className="space-y-2 text-sm">{analysis.unclear_claims.map((c) => <li key={c.claim}><span className="font-medium">{c.claim}</span><span className="block text-xs text-muted-foreground">{c.reason}</span></li>)}</ul>
                    ) : <p className="text-sm text-muted-foreground">No unsupported claims found.</p>}
                  </CardContent>
                </Card>
              </div>

              <EducationCard profile={profile.data!} />
              <CompareCard resumeId={active.data!.id} />
            </>
          )}
        </div>
      </div>
    </>
  );
}

function SkillsCard({ profile, onAct }: { profile: Profile; onAct: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [skill, setSkill] = useState("");
  const [evidence, setEvidence] = useState("");
  const groups = LEVEL_ORDER.map((level) => ({ level, items: profile.skills.filter((s) => s.evidence_level === level) })).filter((g) => g.items.length);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Skills ({profile.skills.length})</CardTitle>
        <CardDescription>Grouped by strength of evidence. Listed or inferred skills count only partially toward job matches.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {groups.map((g) => (
          <section key={g.level} aria-label={LEVEL_TEXT[g.level]}>
            <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{LEVEL_TEXT[g.level]}</h4>
            <ul className="space-y-2">
              {g.items.map((s) => (
                <li key={s.id} className="rounded-md border p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{s.skill}</span>
                    {s.years !== null && <Badge variant="muted">{s.years} yrs</Badge>}
                    {s.user_confirmed && <Badge variant="success">Confirmed</Badge>}
                    <span className="ml-auto flex gap-1">
                      {!s.user_confirmed && (
                        <Button size="sm" variant="ghost" onClick={() => onAct(() => api(`/profile/skills/${s.id}`, { method: "PATCH", body: { user_confirmed: true } }))}>Confirm</Button>
                      )}
                      <Button size="sm" variant="ghost" aria-label={`Remove ${s.skill}`} onClick={() => onAct(() => api(`/profile/skills/${s.id}`, { method: "DELETE" }))}>
                        <Trash2 />
                      </Button>
                    </span>
                  </div>
                  {s.evidence.length > 0 && (
                    <blockquote className="mt-1.5 border-l-2 pl-2 text-xs text-muted-foreground">&ldquo;{s.evidence[0]}&rdquo;</blockquote>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
        <form
          className="flex flex-col gap-2 border-t pt-4 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            void onAct(() => api("/profile/skills", { body: { skill, evidence } })).then(() => { setSkill(""); setEvidence(""); });
          }}
        >
          <Input aria-label="Skill name" placeholder="Add a missing skill (e.g. Kubernetes)" value={skill} onChange={(e) => setSkill(e.target.value)} required className="sm:w-56" />
          <Input aria-label="Evidence" placeholder="Where did you use it? (optional)" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
          <Button type="submit" variant="outline"><Plus /> Add</Button>
        </form>
      </CardContent>
    </Card>
  );
}

function ExperienceCard({ experiences, onAct }: { experiences: Experience[]; onAct: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState({ title: "", company: "" });
  return (
    <Card>
      <CardHeader><CardTitle>Work experience</CardTitle><CardDescription>Fix any title or company the parser got wrong.</CardDescription></CardHeader>
      <CardContent>
        {experiences.length ? (
          <ul className="space-y-4">
            {experiences.map((e) => (
              <li key={e.id} className="space-y-1.5">
                {editing === e.id ? (
                  <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(ev) => { ev.preventDefault(); void onAct(() => api(`/profile/experiences/${e.id}`, { method: "PATCH", body: draft })).then(() => setEditing(null)); }}>
                    <Input aria-label="Title" value={draft.title} onChange={(ev) => setDraft({ ...draft, title: ev.target.value })} />
                    <Input aria-label="Company" value={draft.company} onChange={(ev) => setDraft({ ...draft, company: ev.target.value })} />
                    <Button type="submit" size="sm">Save</Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
                  </form>
                ) : (
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium">{e.title || "Untitled role"}</span>
                    <span className="text-sm text-muted-foreground">{e.company || "Unknown company"}</span>
                    <span className="text-xs text-muted-foreground">{formatDate(e.start_date, { day: undefined })} – {e.is_current ? "Present" : formatDate(e.end_date, { day: undefined })}</span>
                    <Button size="sm" variant="link" className="h-auto px-0" onClick={() => { setEditing(e.id); setDraft({ title: e.title, company: e.company }); }}>Edit</Button>
                  </div>
                )}
                <ul className="list-inside list-disc text-sm text-muted-foreground">{e.highlights.map((h) => <li key={h}>{h}</li>)}</ul>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted-foreground">No dated roles were detected. Use a format like &ldquo;Title | Company | Jan 2021 – Present&rdquo;.</p>}
      </CardContent>
    </Card>
  );
}

function EducationCard({ profile }: { profile: Profile }) {
  if (!profile.education.length && !profile.certifications.length && !profile.projects.length) return null;
  return (
    <Card>
      <CardHeader><CardTitle>Projects, education & certifications</CardTitle></CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-3">
        <div><h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Projects</h4>
          <ul className="space-y-2 text-sm">{profile.projects.map((p) => <li key={p.id}><span className="font-medium">{p.name}</span><span className="block text-xs text-muted-foreground">{p.technologies.join(", ")}</span></li>)}</ul></div>
        <div><h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Education</h4>
          <ul className="space-y-2 text-sm">{profile.education.map((e) => <li key={e.raw}>{e.degree || e.raw}<span className="block text-xs text-muted-foreground">{e.institution} {e.graduation_year ?? ""}</span></li>)}</ul></div>
        <div><h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Certifications</h4>
          <ul className="space-y-2 text-sm">{profile.certifications.map((c) => <li key={c.name}>{c.name}<span className="block text-xs text-muted-foreground">{c.issuer} {c.year ?? ""}</span></li>)}</ul></div>
      </CardContent>
    </Card>
  );
}

function CompareCard({ resumeId }: { resumeId: number }) {
  const [jd, setJd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ skill_score: number; tailoring: Tailoring; details: { skill: string; status: "matched" | "partial" | "missing"; level: string; explanation: string }[] } | null>(null);
  async function compare(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api(`/resumes/${resumeId}/compare`, { body: { job_description: jd } }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Compare with a job description</CardTitle>
        <CardDescription>Paste any job posting to see matched and missing requirements and honest tailoring suggestions.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={compare} className="space-y-2">
          <Textarea aria-label="Job description" rows={6} value={jd} onChange={(e) => setJd(e.target.value)} placeholder="Requirements:&#10;- 3+ years of Python&#10;- PostgreSQL…" required minLength={30} />
          <Button type="submit" loading={busy}>Compare</Button>
        </form>
        {error && <Alert tone="error">{error}</Alert>}
        {result && (
          <div className="space-y-4">
            <p className="text-sm">Skill match: <span className="font-semibold">{Math.round(result.skill_score)}/100</span></p>
            <div className="flex flex-wrap gap-2">
              {result.details.map((d) => <span key={d.skill} className="inline-flex items-center gap-1.5 text-sm" title={d.explanation}>{d.skill} <GapBadge status={d.status} /></span>)}
            </div>
            <ul className="space-y-2">
              {result.tailoring.suggestions.map((s, i) => (
                <li key={i} className="rounded-md border p-2.5 text-sm">
                  <p>{s.suggestion}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{s.rationale}</p>
                </li>
              ))}
            </ul>
            <Alert tone="info"><ShieldAlert className="mr-1 inline size-3.5" />{result.tailoring.disclaimer}</Alert>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
