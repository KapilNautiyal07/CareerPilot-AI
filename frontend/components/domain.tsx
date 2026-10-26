"use client";

import { CheckCircle2, CircleDashed, CircleDot, ExternalLink, ShieldAlert, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/primitives";
import { cn, scoreTone } from "@/lib/utils";
import type { GapStatus, ResourceRef } from "@/types/api";

export const GAP_LABEL: Record<GapStatus, string> = { matched: "Matched", partial: "Partial", missing: "Missing" };
const GAP_VARIANT: Record<GapStatus, "success" | "warning" | "danger"> = {
  matched: "success",
  partial: "warning",
  missing: "danger",
};
const GAP_ICON = { matched: CheckCircle2, partial: CircleDot, missing: CircleDashed };

export function GapBadge({ status }: { status: GapStatus }) {
  const Icon = GAP_ICON[status];
  return (
    <Badge variant={GAP_VARIANT[status]}>
      <Icon className="size-3" aria-hidden />
      {GAP_LABEL[status]}
    </Badge>
  );
}

export function SkillChip({ skill, status, required }: { skill: string; status?: GapStatus; required?: boolean }) {
  const tone = status ? GAP_VARIANT[status] : "muted";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs",
        tone === "success" && "border-success/30 bg-success-soft",
        tone === "warning" && "border-warning/30 bg-warning-soft",
        tone === "danger" && "border-danger/30 bg-danger-soft",
        tone === "muted" && "bg-muted",
      )}
      title={status ? `${GAP_LABEL[status]}${required === false ? " · preferred" : ""}` : undefined}
    >
      {status && <span className="sr-only">{GAP_LABEL[status]}:</span>}
      {skill}
      {required === false && <span className="text-muted-foreground">(pref)</span>}
    </span>
  );
}

export function ScoreRing({ value, size = 72, label }: { value: number | null | undefined; size?: number; label?: string }) {
  const v = value ?? 0;
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const tone = scoreTone(value);
  const color = {
    success: "var(--success)",
    warning: "var(--warning)",
    danger: "var(--danger)",
    muted: "var(--muted-foreground)",
  }[tone];
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={`${label ?? "Score"}: ${value === null || value === undefined ? "not available" : Math.round(v)} out of 100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--muted)" strokeWidth={6} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - Math.max(0, Math.min(100, v)) / 100)}
        />
      </svg>
      <span className="absolute inset-0 grid place-items-center text-lg font-semibold tabular-nums">
        {value === null || value === undefined ? "—" : Math.round(v)}
      </span>
    </div>
  );
}

export function ResourceLink({ r }: { r: ResourceRef }) {
  const verified = r.verification_status === "reachable";
  return (
    <a
      href={r.url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-xs hover:bg-muted"
    >
      {verified ? (
        <ShieldCheck className="size-3 shrink-0 text-success" aria-label="Link checked and reachable" />
      ) : (
        <ShieldAlert className="size-3 shrink-0 text-muted-foreground" aria-label="Link not yet checked" />
      )}
      <span className="truncate">{r.title}</span>
      <span className="shrink-0 text-muted-foreground">· {r.provider}</span>
      <ExternalLink className="size-3 shrink-0 text-muted-foreground" aria-hidden />
    </a>
  );
}

const FLAG_TEXT: Record<string, string> = {
  demo_data: "Demo data",
  stale: "Possibly closed",
  long_open: "Open 2+ months",
  no_posting_date: "No posting date",
  missing_location: "No location",
  no_requirements_extracted: "No requirements found",
  short_description: "Short description",
  no_application_url: "No application link",
  unverifiable: "Unverified",
  suspicious_content: "Suspicious content",
};

export function QualityFlags({ flags, hide = [] }: { flags: string[]; hide?: string[] }) {
  const shown = flags.filter((f) => !hide.includes(f));
  if (!shown.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((f) => (
        <Badge
          key={f}
          variant={f === "demo_data" ? "demo" : f === "suspicious_content" || f === "stale" ? "danger" : "muted"}
        >
          {FLAG_TEXT[f] ?? f}
        </Badge>
      ))}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
