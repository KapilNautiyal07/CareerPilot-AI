"use client";

import { useQueryClient } from "@tanstack/react-query";
import { LogOut, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { ErrorState, LoadingCards, PageHeader } from "@/components/ui/states";
import { api, errorMessage } from "@/lib/api";
import { keys, useMe, useMeta, useProfile } from "@/lib/queries";
import { titleCase } from "@/lib/utils";
import type { Profile } from "@/types/api";

export default function SettingsPage() {
  const profile = useProfile();
  if (profile.isLoading) return <LoadingCards count={3} className="sm:grid-cols-1 lg:grid-cols-1" />;
  if (profile.error || !profile.data) return <ErrorState error={profile.error} onRetry={() => profile.refetch()} />;
  return (
    <>
      <PageHeader title="Profile & settings" />
      <div className="grid gap-6 lg:grid-cols-2">
        <ProfileForm profile={profile.data} />
        <PreferencesForm profile={profile.data} />
        <AccountCard />
        <IntegrationsCard />
      </div>
    </>
  );
}

function useSaver() {
  const qc = useQueryClient();
  const [state, setState] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(fn: () => Promise<unknown>) {
    setBusy(true);
    setState(null);
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: keys.profile });
      setState({ tone: "success", text: "Saved. Re-run the analysis to apply changes to jobs and the roadmap." });
    } catch (err) {
      setState({ tone: "error", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }
  return { state, busy, save };
}

function ProfileForm({ profile }: { profile: Profile }) {
  const meta = useMeta();
  const { state, busy, save } = useSaver();
  const [v, setV] = useState({
    headline: profile.headline, target_role: profile.target_role, target_seniority: profile.target_seniority,
    years_experience: profile.years_experience?.toString() ?? "", location: profile.location, summary: profile.summary,
  });
  return (
    <Card>
      <CardHeader><CardTitle>Candidate profile</CardTitle><CardDescription>Your confirmed values take precedence over what was extracted.</CardDescription></CardHeader>
      <CardContent>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void save(() => api("/profile", { method: "PUT", body: { ...v, years_experience: v.years_experience === "" ? null : Number(v.years_experience) } })); }}>
          <Field label="Target role" htmlFor="s-role">
            <Input id="s-role" list="s-roles" value={v.target_role} onChange={(e) => setV({ ...v, target_role: e.target.value })} required minLength={2} />
            <datalist id="s-roles">{meta.data?.roles.map((r) => <option key={r.key} value={r.title} />)}</datalist>
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Target seniority" htmlFor="s-sen">
              <Select id="s-sen" value={v.target_seniority} onChange={(e) => setV({ ...v, target_seniority: e.target.value })}>
                {["unknown", "intern", "junior", "mid", "senior", "staff", "lead"].map((s) => <option key={s} value={s}>{s === "unknown" ? "Not sure" : titleCase(s)}</option>)}
              </Select>
            </Field>
            <Field label="Years of experience" htmlFor="s-years"><Input id="s-years" type="number" min={0} max={60} step="0.5" value={v.years_experience} onChange={(e) => setV({ ...v, years_experience: e.target.value })} /></Field>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Headline" htmlFor="s-head"><Input id="s-head" value={v.headline} onChange={(e) => setV({ ...v, headline: e.target.value })} /></Field>
            <Field label="Location" htmlFor="s-loc"><Input id="s-loc" value={v.location} onChange={(e) => setV({ ...v, location: e.target.value })} /></Field>
          </div>
          <Field label="Summary" htmlFor="s-sum"><Textarea id="s-sum" rows={3} value={v.summary} onChange={(e) => setV({ ...v, summary: e.target.value })} /></Field>
          {state && <Alert tone={state.tone}>{state.text}</Alert>}
          <Button type="submit" loading={busy}>Save profile</Button>
        </form>
      </CardContent>
    </Card>
  );
}

function PreferencesForm({ profile }: { profile: Profile }) {
  const p = profile.preferences!;
  const { state, busy, save } = useSaver();
  const [v, setV] = useState({
    weekly_study_hours: p.weekly_study_hours.toString(), study_days_per_week: p.study_days_per_week.toString(),
    target_deadline: p.target_deadline ?? "", preferred_locations: p.preferred_locations.join(", "),
    work_arrangements: p.work_arrangements, salary_min: p.salary_min?.toString() ?? "", salary_currency: p.salary_currency,
  });
  const toggle = (a: string) =>
    setV((s) => ({ ...s, work_arrangements: s.work_arrangements.includes(a) ? s.work_arrangements.filter((x) => x !== a) : [...s.work_arrangements, a] }));
  return (
    <Card>
      <CardHeader><CardTitle>Preferences & study plan</CardTitle><CardDescription>Used for job ranking and roadmap capacity.</CardDescription></CardHeader>
      <CardContent>
        <form className="space-y-3" onSubmit={(e) => {
          e.preventDefault();
          void save(() => api("/profile/preferences", { method: "PUT", body: {
            weekly_study_hours: Number(v.weekly_study_hours), study_days_per_week: Number(v.study_days_per_week),
            target_deadline: v.target_deadline || null,
            preferred_locations: v.preferred_locations.split(",").map((s) => s.trim()).filter(Boolean),
            work_arrangements: v.work_arrangements, salary_min: v.salary_min === "" ? null : Number(v.salary_min),
            salary_currency: v.salary_currency.toUpperCase(), interview_categories: p.interview_categories,
          } }));
        }}>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Hours / week" htmlFor="p-hours"><Input id="p-hours" type="number" min={0.5} max={80} step="0.5" value={v.weekly_study_hours} onChange={(e) => setV({ ...v, weekly_study_hours: e.target.value })} /></Field>
            <Field label="Days / week" htmlFor="p-days"><Input id="p-days" type="number" min={1} max={7} value={v.study_days_per_week} onChange={(e) => setV({ ...v, study_days_per_week: e.target.value })} /></Field>
            <Field label="Deadline" htmlFor="p-deadline"><Input id="p-deadline" type="date" value={v.target_deadline} onChange={(e) => setV({ ...v, target_deadline: e.target.value })} /></Field>
          </div>
          <Field label="Preferred locations" htmlFor="p-loc" hint="Comma separated"><Input id="p-loc" value={v.preferred_locations} onChange={(e) => setV({ ...v, preferred_locations: e.target.value })} /></Field>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Work arrangement</legend>
            <div className="flex flex-wrap gap-3">
              {["remote", "hybrid", "onsite"].map((a) => (
                <label key={a} className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[var(--primary)]" checked={v.work_arrangements.includes(a)} onChange={() => toggle(a)} /> {titleCase(a)}</label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-[1fr_7rem]">
            <Field label="Minimum salary" htmlFor="p-sal"><Input id="p-sal" type="number" min={0} value={v.salary_min} onChange={(e) => setV({ ...v, salary_min: e.target.value })} /></Field>
            <Field label="Currency" htmlFor="p-cur"><Input id="p-cur" maxLength={3} value={v.salary_currency} onChange={(e) => setV({ ...v, salary_currency: e.target.value })} /></Field>
          </div>
          {state && <Alert tone={state.tone}>{state.text}</Alert>}
          <Button type="submit" loading={busy}>Save preferences</Button>
        </form>
      </CardContent>
    </Card>
  );
}

function AccountCard() {
  const me = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const close = () => {
    setOpen(false);
    setPassword("");
    setError(null);
  };

  async function logoutAll() {
    await api("/auth/logout-all", { method: "POST" }).catch(() => undefined);
    qc.clear();
    router.replace("/login");
  }
  async function deleteAccount(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/me", { method: "DELETE", body: { password } });
      qc.clear();
      router.replace("/");
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }
  return (
    <Card>
      <CardHeader><CardTitle>Account & data</CardTitle><CardDescription>Signed in as {me.data?.email}.</CardDescription></CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">Sign out on every device (invalidates all sessions).</p>
          <Button variant="outline" onClick={logoutAll}><LogOut /> Log out everywhere</Button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-4">
          <p className="max-w-sm text-sm text-muted-foreground">Permanently delete your account, profile, analyses, interview history and uploaded resume files.</p>
          <Button variant="destructive" onClick={() => setOpen(true)}><Trash2 /> Delete account</Button>
        </div>
        <p className="text-xs text-muted-foreground">Uploaded resumes are also deleted automatically after the retention period configured by the operator.</p>
      </CardContent>
      <Dialog open={open} onClose={close} title="Delete your account?" description="This cannot be undone. All of your data and files are removed.">
        <form method="post" onSubmit={deleteAccount} className="space-y-3">
          <Field label="Confirm with your password" htmlFor="del-pass"><Input id="del-pass" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
          {error && <Alert tone="error">{error}</Alert>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={close}>Cancel</Button><Button type="submit" variant="destructive" loading={busy}>Delete permanently</Button></div>
        </form>
      </Dialog>
    </Card>
  );
}

function IntegrationsCard() {
  const meta = useMeta();
  const m = meta.data;
  return (
    <Card>
      <CardHeader><CardTitle>Integrations</CardTitle><CardDescription>Configured by the server operator through environment variables.</CardDescription></CardHeader>
      <CardContent className="space-y-2 text-sm">
        {m ? (
          <>
            <p><span className="font-medium">AI model:</span> {m.llm.provider === "mock" ? "Deterministic mode (no LLM configured)" : `${m.llm.provider} · ${m.llm.model}`}</p>
            <p><span className="font-medium">Job sources:</span> {m.job_providers.map((p) => (p === "mock" ? "Demo data" : titleCase(p))).join(", ")}</p>
            <p><span className="font-medium">Code execution:</span> {m.code_sandbox === "docker" ? "Isolated Docker sandbox" : "Disabled (static checks only)"}</p>
          </>
        ) : <p className="text-muted-foreground">Loading…</p>}
      </CardContent>
    </Card>
  );
}
