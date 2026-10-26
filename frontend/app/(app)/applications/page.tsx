"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ClipboardList, ExternalLink, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Badge, Card, CardContent, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingCards, PageHeader } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { keys, useApplications } from "@/lib/queries";
import { formatDate, titleCase } from "@/lib/utils";
import type { Application, ApplicationStatus } from "@/types/api";

const STATUSES: ApplicationStatus[] = ["saved", "planned", "applied", "interviewing", "offered", "rejected", "withdrawn"];
const ACTIVE: ApplicationStatus[] = ["saved", "planned", "applied", "interviewing", "offered"];

const schema = z.object({
  company: z.string().trim().min(1, "Required").max(200),
  title: z.string().trim().min(1, "Required").max(200),
  job_url: z.union([z.literal(""), z.url({ protocol: /^https?$/, message: "Use an http(s) URL" })]),
  status: z.enum(STATUSES as [ApplicationStatus, ...ApplicationStatus[]]),
  next_step: z.string().max(300).optional(),
  next_step_due: z.string().optional(),
  notes: z.string().max(5000).optional(),
});
type Values = z.infer<typeof schema>;

export default function ApplicationsPage() {
  const apps = useApplications();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showClosed, setShowClosed] = useState(false);

  async function patch(a: Application, body: Partial<Application>) {
    setError(null);
    try {
      await api(`/applications/${a.id}`, { method: "PATCH", body });
      await Promise.all([qc.invalidateQueries({ queryKey: keys.applications }), qc.invalidateQueries({ queryKey: keys.dashboard })]);
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  async function remove(a: Application) {
    if (!confirm(`Remove ${a.title} at ${a.company} from the tracker?`)) return;
    try {
      await api(`/applications/${a.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: keys.applications });
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const list = (apps.data ?? []).filter((a) => showClosed || ACTIVE.includes(a.status));
  const today = new Date().toISOString().slice(0, 10);

  return (
    <>
      <PageHeader
        title="Application tracker"
        description="Keep track of where you applied and what comes next. CareerPilot never submits applications or contacts employers on your behalf."
        actions={<Button onClick={() => setAdding(true)}><Plus /> Add application</Button>}
      />
      {error && <Alert tone="error" className="mb-4">{error}</Alert>}
      {apps.data && apps.data.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STATUSES.map((s) => {
            const n = apps.data!.filter((a) => a.status === s).length;
            return <Badge key={s} variant={n ? "default" : "muted"}>{titleCase(s)} · {n}</Badge>;
          })}
          <label className="ml-auto flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[var(--primary)]" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> Show rejected & withdrawn</label>
        </div>
      )}

      {apps.isLoading ? <LoadingCards count={3} className="sm:grid-cols-1 lg:grid-cols-1" /> : apps.error ? <ErrorState error={apps.error} onRetry={() => apps.refetch()} /> : !list.length ? (
        <EmptyState icon={ClipboardList} title="No applications tracked" description="Save jobs from Job discovery or add an application you found elsewhere." action={<Button asChild variant="outline"><Link href="/jobs">Browse jobs</Link></Button>} />
      ) : (
        <ul className="space-y-3">
          {list.map((a) => {
            const overdue = !!a.next_step_due && a.next_step_due < today;
            return (
              <li key={a.id}>
                <Card>
                  <CardContent className="grid gap-4 pt-5 md:grid-cols-[1fr_auto]">
                    <div className="min-w-0 space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        {a.job_id ? <Link href={`/jobs/${a.job_id}`} className="font-semibold hover:underline">{a.title}</Link> : <span className="font-semibold">{a.title}</span>}
                        <span className="text-sm text-muted-foreground">{a.company}</span>
                        {a.job_url && <a href={a.job_url} target="_blank" rel="noopener noreferrer" className="text-muted-foreground hover:text-foreground" aria-label="Open job posting"><ExternalLink className="size-3.5" /></a>}
                      </div>
                      <InlineText label="Next step" value={a.next_step} onSave={(v) => patch(a, { next_step: v })} />
                      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <label className="flex items-center gap-1.5">Due
                          <Input type="date" className="h-7 w-36 text-xs" defaultValue={a.next_step_due ?? ""} onBlur={(e) => e.target.value !== (a.next_step_due ?? "") && patch(a, { next_step_due: e.target.value || null })} />
                        </label>
                        {overdue && <Badge variant="danger">Overdue</Badge>}
                        {a.applied_on && <span>Applied {formatDate(a.applied_on)}</span>}
                        <span>History: {a.status_history.map((h) => titleCase(h.status)).join(" → ")}</span>
                      </div>
                      <InlineText label="Notes" value={a.notes} multiline onSave={(v) => patch(a, { notes: v })} />
                    </div>
                    <div className="flex items-start gap-2">
                      <Select aria-label={`Status for ${a.title}`} value={a.status} onChange={(e) => patch(a, { status: e.target.value as ApplicationStatus })} className="w-40">
                        {STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                      </Select>
                      <Button variant="ghost" size="icon" aria-label={`Remove ${a.title}`} onClick={() => remove(a)}><Trash2 /></Button>
                    </div>
                  </CardContent>
                </Card>
              </li>
            );
          })}
        </ul>
      )}
      <AddDialog open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

function InlineText({ label, value, onSave, multiline }: { label: string; value: string; onSave: (v: string) => void; multiline?: boolean }) {
  const [draft, setDraft] = useState(value);
  const Comp = multiline ? Textarea : Input;
  return (
    <Comp
      aria-label={label}
      placeholder={`${label}…`}
      value={draft}
      rows={multiline ? 2 : undefined}
      className={multiline ? "min-h-0 text-sm" : "h-8 text-sm"}
      onChange={(e: React.ChangeEvent<HTMLInputElement & HTMLTextAreaElement>) => setDraft(e.target.value)}
      onBlur={() => draft !== value && onSave(draft)}
    />
  );
}

function AddDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { status: "planned", job_url: "" } });
  const { errors, isSubmitting } = form.formState;
  async function onSubmit(v: Values) {
    setError(null);
    try {
      await api("/applications", { body: { ...v, job_url: v.job_url || null, next_step_due: v.next_step_due || null } });
      await qc.invalidateQueries({ queryKey: keys.applications });
      form.reset();
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Add application" description="Record an application you are planning or already submitted yourself.">
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-3" noValidate>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Company" htmlFor="a-company" error={errors.company?.message}><Input id="a-company" {...form.register("company")} /></Field>
          <Field label="Role" htmlFor="a-title" error={errors.title?.message}><Input id="a-title" {...form.register("title")} /></Field>
        </div>
        <Field label="Job URL" htmlFor="a-url" error={errors.job_url?.message}><Input id="a-url" type="url" placeholder="https://" {...form.register("job_url")} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Status" htmlFor="a-status"><Select id="a-status" {...form.register("status")}>{STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}</Select></Field>
          <Field label="Next step due" htmlFor="a-due"><Input id="a-due" type="date" {...form.register("next_step_due")} /></Field>
        </div>
        <Field label="Next step" htmlFor="a-next"><Input id="a-next" placeholder="e.g. Follow up with recruiter" {...form.register("next_step")} /></Field>
        <Field label="Notes" htmlFor="a-notes"><Textarea id="a-notes" rows={3} {...form.register("notes")} /></Field>
        {error && <Alert tone="error">{error}</Alert>}
        <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" loading={isSubmitting}>Add</Button></div>
      </form>
    </Dialog>
  );
}
