"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, GraduationCap } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { ResumeUploader } from "@/components/resume-uploader";
import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { api, ApiError, errorMessage } from "@/lib/api";
import { keys, useMe, useMeta, useStartAnalysis } from "@/lib/queries";
import { cn } from "@/lib/utils";

const tomorrow = () => new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
/** Default deadline: twelve weeks from now, deadline day inclusive (computed outside render). */
const defaultDeadline = () => new Date(Date.now() + 83 * 86_400_000).toISOString().slice(0, 10);

const schema = z.object({
  target_role: z.string().trim().min(2, "Choose or type a target role").max(120),
  target_seniority: z.enum(["unknown", "intern", "junior", "mid", "senior", "staff", "lead"]),
  years_experience: z.union([z.literal(""), z.coerce.number().min(0).max(60)]).optional(),
  location: z.string().max(120).optional(),
  headline: z.string().max(200).optional(),
  preferred_locations: z.string().max(500).optional(),
  work_arrangements: z.array(z.enum(["remote", "hybrid", "onsite"])),
  salary_min: z.union([z.literal(""), z.coerce.number().min(0)]).optional(),
  salary_currency: z.string().length(3, "Use a 3-letter code"),
  weekly_study_hours: z.coerce.number().min(0.5, "At least 0.5 hours").max(80, "At most 80 hours"),
  study_days_per_week: z.coerce.number().int().min(1).max(7),
  target_deadline: z.string().refine((v) => !!v && v >= tomorrow(), "Pick a future date"),
});
type Values = z.input<typeof schema>;

const STEPS = [
  { title: "Target role", fields: ["target_role", "target_seniority", "years_experience", "location", "headline"] },
  { title: "Job preferences", fields: ["preferred_locations", "work_arrangements", "salary_min", "salary_currency"] },
  { title: "Study plan", fields: ["weekly_study_hours", "study_days_per_week", "target_deadline"] },
  { title: "Resume", fields: [] },
] as const;

export default function OnboardingPage() {
  const router = useRouter();
  const me = useMe();
  const meta = useMeta();
  const qc = useQueryClient();
  const start = useStartAnalysis();
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [hasResume, setHasResume] = useState(false);
  const [initialDeadline] = useState(defaultDeadline);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      target_role: "",
      target_seniority: "unknown",
      work_arrangements: [],
      salary_currency: "USD",
      weekly_study_hours: 8,
      study_days_per_week: 5,
      target_deadline: initialDeadline,
    },
  });
  const { errors, isSubmitting } = form.formState;

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace("/login?next=/onboarding");
  }, [me.error, router]);

  async function next() {
    setError(null);
    const ok = await form.trigger(STEPS[step].fields as unknown as (keyof Values)[]);
    if (!ok) return;
    if (step === 2) await save();
    else setStep((s) => s + 1);
  }

  async function save() {
    const v = schema.parse(form.getValues());
    try {
      await api("/profile/onboarding", {
        body: {
          profile: {
            target_role: v.target_role,
            target_seniority: v.target_seniority,
            years_experience: v.years_experience === "" || v.years_experience === undefined ? null : v.years_experience,
            location: v.location ?? "",
            headline: v.headline ?? "",
          },
          preferences: {
            weekly_study_hours: v.weekly_study_hours,
            study_days_per_week: v.study_days_per_week,
            target_deadline: v.target_deadline,
            preferred_locations: (v.preferred_locations ?? "").split(",").map((s) => s.trim()).filter(Boolean),
            work_arrangements: v.work_arrangements,
            salary_min: v.salary_min === "" || v.salary_min === undefined ? null : v.salary_min,
            salary_currency: v.salary_currency.toUpperCase(),
          },
        },
      });
      await qc.invalidateQueries({ queryKey: keys.me });
      setStep(3);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function finish(run: boolean) {
    if (!run) return router.replace("/dashboard");
    try {
      const { execution_id } = await start.mutateAsync({});
      router.replace(`/dashboard?run=${execution_id}`);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const arrangements = useWatch({ control: form.control, name: "work_arrangements" }) ?? [];

  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col px-4 py-8">
      <div className="mb-6 flex items-center gap-2 font-semibold">
        <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
          <GraduationCap className="size-4" aria-hidden />
        </span>
        CareerPilot AI
      </div>
      <ol className="mb-6 grid grid-cols-4 gap-2" aria-label="Onboarding progress">
        {STEPS.map((s, i) => (
          <li key={s.title} className="flex flex-col gap-1.5" aria-current={i === step ? "step" : undefined}>
            <div className={cn("h-1.5 rounded-full bg-muted", i <= step && "bg-primary")} />
            <span className={cn("text-xs text-muted-foreground", i === step && "font-medium text-foreground")}>
              {s.title}
            </span>
          </li>
        ))}
      </ol>

      <Card>
        <CardHeader>
          <CardTitle>{["What role are you aiming for?", "Where do you want to work?", "How much time can you study?", "Add your resume"][step]}</CardTitle>
          <CardDescription>
            {[
              "This decides which jobs, skills and interview tracks we focus on.",
              "Used to rank jobs. Leave blank if you are flexible.",
              "Your roadmap is planned to fit this budget and deadline.",
              "We extract skills with evidence from your resume. You can review and correct everything.",
            ][step]}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={(e) => { e.preventDefault(); void next(); }} className="space-y-4" noValidate>
            {step === 0 && (
              <>
                <Field label="Target role" htmlFor="target_role" error={errors.target_role?.message} hint="Pick a common role or type your own.">
                  <Input id="target_role" list="roles" placeholder="e.g. Backend Engineer" aria-invalid={!!errors.target_role} {...form.register("target_role")} />
                  <datalist id="roles">
                    {meta.data?.roles.map((r) => <option key={r.key} value={r.title} />)}
                  </datalist>
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Target seniority" htmlFor="target_seniority">
                    <Select id="target_seniority" {...form.register("target_seniority")}>
                      {["unknown", "intern", "junior", "mid", "senior", "staff", "lead"].map((s) => (
                        <option key={s} value={s}>{s === "unknown" ? "Not sure" : s[0].toUpperCase() + s.slice(1)}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Years of experience" htmlFor="years_experience" error={errors.years_experience?.message} hint="Optional; extracted from your resume otherwise.">
                    <Input id="years_experience" type="number" step="0.5" min={0} {...form.register("years_experience")} />
                  </Field>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Current location" htmlFor="location">
                    <Input id="location" placeholder="City, Country" {...form.register("location")} />
                  </Field>
                  <Field label="Current headline" htmlFor="headline">
                    <Input id="headline" placeholder="e.g. Software Engineer" {...form.register("headline")} />
                  </Field>
                </div>
              </>
            )}
            {step === 1 && (
              <>
                <Field label="Preferred locations" htmlFor="preferred_locations" hint="Comma separated, e.g. Bengaluru, Pune">
                  <Textarea id="preferred_locations" rows={2} {...form.register("preferred_locations")} />
                </Field>
                <fieldset>
                  <legend className="mb-2 text-sm font-medium">Work arrangement</legend>
                  <div className="flex flex-wrap gap-2">
                    {(["remote", "hybrid", "onsite"] as const).map((a) => (
                      <label key={a} className={cn("flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm", arrangements.includes(a) && "border-primary bg-primary-soft")}>
                        <input type="checkbox" value={a} className="accent-[var(--primary)]" {...form.register("work_arrangements")} />
                        {a[0].toUpperCase() + a.slice(1)}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
                  <Field label="Minimum salary (yearly)" htmlFor="salary_min" error={errors.salary_min?.message}>
                    <Input id="salary_min" type="number" min={0} {...form.register("salary_min")} />
                  </Field>
                  <Field label="Currency" htmlFor="salary_currency" error={errors.salary_currency?.message}>
                    <Input id="salary_currency" maxLength={3} {...form.register("salary_currency")} />
                  </Field>
                </div>
              </>
            )}
            {step === 2 && (
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Study hours per week" htmlFor="weekly_study_hours" error={errors.weekly_study_hours?.message}>
                  <Input id="weekly_study_hours" type="number" step="0.5" min={0.5} max={80} aria-invalid={!!errors.weekly_study_hours} {...form.register("weekly_study_hours")} />
                </Field>
                <Field label="Study days per week" htmlFor="study_days_per_week" error={errors.study_days_per_week?.message}>
                  <Input id="study_days_per_week" type="number" min={1} max={7} {...form.register("study_days_per_week")} />
                </Field>
                <Field label="Switch deadline" htmlFor="target_deadline" error={errors.target_deadline?.message}>
                  <Input id="target_deadline" type="date" min={tomorrow()} aria-invalid={!!errors.target_deadline} {...form.register("target_deadline")} />
                </Field>
              </div>
            )}
            {step === 3 && (
              <div className="space-y-4">
                <ResumeUploader onUploaded={() => setHasResume(true)} compact />
                {hasResume && <Alert tone="success" title="Resume analyzed">You can review the extracted profile on the Resume page.</Alert>}
              </div>
            )}

            {error && <Alert tone="error">{error}</Alert>}

            <div className="flex items-center justify-between gap-2 pt-2">
              {step > 0 && step < 3 ? (
                <Button type="button" variant="ghost" onClick={() => setStep((s) => s - 1)}>
                  <ArrowLeft /> Back
                </Button>
              ) : <span />}
              {step < 3 ? (
                <Button type="submit" loading={isSubmitting}>
                  {step === 2 ? "Save and continue" : "Next"} <ArrowRight />
                </Button>
              ) : (
                <div className="flex gap-2">
                  <Button type="button" variant="outline" onClick={() => finish(false)}>
                    Skip for now
                  </Button>
                  <Button type="button" disabled={!hasResume} loading={start.isPending} onClick={() => finish(true)}>
                    <Check /> Run career analysis
                  </Button>
                </div>
              )}
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
