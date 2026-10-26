"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Bookmark, BookmarkCheck, Briefcase, MapPin, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { QualityFlags, ScoreRing, SkillChip } from "@/components/domain";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, Input, Label, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingCards, PageHeader } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { useJobs } from "@/lib/queries";
import { formatSalary, relativeDays, titleCase } from "@/lib/utils";
import type { Job } from "@/types/api";

type Filters = {
  q: string;
  arrangement: string;
  seniority: string;
  location: string;
  min_score: string;
  max_years: string;
  saved: boolean;
  include_stale: boolean;
  sort: string;
};
const INITIAL: Filters = { q: "", arrangement: "", seniority: "", location: "", min_score: "", max_years: "", saved: false, include_stale: true, sort: "match" };

export default function JobsPage() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Filters>(INITIAL);
  const [filters, setFilters] = useState<Filters>(INITIAL);
  const [page, setPage] = useState(1);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "error" | "warning"; text: string } | null>(null);
  const jobs = useJobs({ ...filters, page, page_size: 10 });

  async function refresh() {
    setRefreshing(true);
    setNotice(null);
    try {
      const r = await api<{ providers_used: string[]; provider_errors: { provider: string }[]; fetched: number; after_dedupe: number; demo_data: boolean }>("/jobs/refresh", { method: "POST" });
      setNotice({
        tone: r.provider_errors.length ? "warning" : "success",
        text: `Fetched ${r.fetched} listings from ${r.providers_used.join(", ") || "no providers"}; ${r.after_dedupe} after removing duplicates.${r.provider_errors.length ? ` Failed: ${r.provider_errors.map((e) => e.provider).join(", ")}.` : ""}`,
      });
      await qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
    } finally {
      setRefreshing(false);
    }
  }

  async function toggleSave(job: Job) {
    try {
      await api(`/jobs/${job.id}/save`, { method: job.is_saved ? "DELETE" : "POST" });
      await qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (err) {
      setNotice({ tone: "error", text: errorMessage(err) });
    }
  }

  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const data = jobs.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <PageHeader
        title="Job discovery"
        description="Ranked by fit with your evidence-backed skills, experience and preferences. Listings show their source and when they were last checked."
        actions={<Button variant="outline" onClick={refresh} loading={refreshing}><RefreshCw /> Refresh listings</Button>}
      />
      {notice && <Alert tone={notice.tone} className="mb-4">{notice.text}</Alert>}

      <Card className="mb-6">
        <CardContent className="pt-5">
          <form
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
            onSubmit={(e) => { e.preventDefault(); setPage(1); setFilters(draft); }}
          >
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="q">Search</Label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
                <Input id="q" className="pl-8" placeholder="Title, company or keyword" value={draft.q} onChange={(e) => set("q", e.target.value)} />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="arrangement">Work arrangement</Label>
              <Select id="arrangement" value={draft.arrangement} onChange={(e) => set("arrangement", e.target.value)}>
                <option value="">Any</option><option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="onsite">On-site</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="seniority">Seniority</Label>
              <Select id="seniority" value={draft.seniority} onChange={(e) => set("seniority", e.target.value)}>
                <option value="">Any</option>{["intern", "junior", "mid", "senior", "staff", "lead"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="location">Location</Label>
              <Input id="location" value={draft.location} onChange={(e) => set("location", e.target.value)} placeholder="e.g. Bengaluru" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="max_years">Max years required</Label>
              <Input id="max_years" type="number" min={0} value={draft.max_years} onChange={(e) => set("max_years", e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="min_score">Minimum match</Label>
              <Input id="min_score" type="number" min={0} max={100} value={draft.min_score} onChange={(e) => set("min_score", e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sort">Sort by</Label>
              <Select id="sort" value={draft.sort} onChange={(e) => set("sort", e.target.value)}>
                <option value="match">Best match</option><option value="recent">Most recent</option><option value="relevance">Search relevance</option>
              </Select>
            </div>
            <div className="flex flex-wrap items-center gap-4 sm:col-span-2 lg:col-span-3">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[var(--primary)]" checked={draft.saved} onChange={(e) => set("saved", e.target.checked)} /> Saved only</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[var(--primary)]" checked={draft.include_stale} onChange={(e) => set("include_stale", e.target.checked)} /> Include stale listings</label>
            </div>
            <div className="flex gap-2 lg:justify-end">
              <Button type="button" variant="ghost" onClick={() => { setDraft(INITIAL); setFilters(INITIAL); setPage(1); }}>Reset</Button>
              <Button type="submit">Apply</Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {jobs.isLoading ? <LoadingCards count={4} className="sm:grid-cols-1 lg:grid-cols-1" /> : jobs.error ? <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} /> : !data?.items.length ? (
        <EmptyState icon={Briefcase} title="No jobs match" description="Run the career analysis or refresh listings to discover jobs for your target role, or loosen the filters." />
      ) : (
        <>
          <p className="mb-3 text-sm text-muted-foreground">{data.total} jobs</p>
          <ul className="space-y-3">
            {data.items.map((job) => <JobCard key={job.id} job={job} onToggleSave={() => toggleSave(job)} />)}
          </ul>
          <nav className="mt-6 flex items-center justify-between" aria-label="Pagination">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <span className="text-sm text-muted-foreground">Page {page} of {totalPages}</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </nav>
        </>
      )}
    </>
  );
}

function JobCard({ job, onToggleSave }: { job: Job; onToggleSave: () => void }) {
  const statusBySkill = Object.fromEntries((job.match?.details ?? []).map((d) => [d.skill, d.status]));
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  return (
    <li>
      <Card className="transition-shadow hover:shadow-sm">
        <CardContent className="flex flex-col gap-4 pt-5 sm:flex-row">
          <ScoreRing value={job.match?.score} size={60} label="Match score" />
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <Link href={`/jobs/${job.id}`} className="font-semibold hover:text-primary hover:underline">{job.title}</Link>
                <p className="text-sm text-muted-foreground">{job.company}</p>
              </div>
              <Button variant="ghost" size="sm" onClick={onToggleSave} aria-pressed={job.is_saved} aria-label={job.is_saved ? `Remove ${job.title} from saved` : `Save ${job.title}`}>
                {job.is_saved ? <BookmarkCheck className="text-primary" /> : <Bookmark />} {job.is_saved ? "Saved" : "Save"}
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1"><MapPin className="size-3" aria-hidden />{job.location || "Location not stated"}</span>
              <span>{titleCase(job.work_arrangement)}</span>
              {job.seniority !== "unknown" && <span>{titleCase(job.seniority)}</span>}
              {job.min_years_experience !== null && <span>{job.min_years_experience}+ yrs</span>}
              {salary && <span>{salary}</span>}
              <span>Posted {relativeDays(job.posted_at)}</span>
              {job.application_status && <Badge variant="default">{titleCase(job.application_status)}</Badge>}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {job.required_skills.map((s) => <SkillChip key={s} skill={s} status={statusBySkill[s]} />)}
              {job.preferred_skills.slice(0, 4).map((s) => <SkillChip key={s} skill={s} status={statusBySkill[s]} required={false} />)}
            </div>
            {job.match?.notes.length ? <p className="text-xs text-muted-foreground">{job.match.notes[0]}</p> : null}
            <QualityFlags flags={job.quality_flags} hide={["no_application_url", "unverifiable"]} />
          </div>
        </CardContent>
      </Card>
    </li>
  );
}
