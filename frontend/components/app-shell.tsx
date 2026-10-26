"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  Briefcase,
  ClipboardList,
  Compass,
  FileText,
  GraduationCap,
  LogOut,
  Map,
  Menu,
  MessagesSquare,
  Settings,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { DemoBanner } from "@/components/demo-banner";
import { StageDot } from "@/components/journey";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useJourney } from "@/lib/journey";
import { useMe } from "@/lib/queries";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/resume", label: "Resume", icon: FileText, stage: "resume" },
  { href: "/gaps", label: "Skill gaps", icon: Compass, stage: "gaps" },
  { href: "/roadmap", label: "Roadmap", icon: Map, stage: "roadmap" },
  { href: "/interviews", label: "Interviews", icon: MessagesSquare, stage: "interviews" },
  { href: "/applications", label: "Applications", icon: ClipboardList, stage: "applications" },
  { href: "/jobs", label: "Jobs", icon: Briefcase },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const journey = useJourney(!!me.data?.onboarding_completed);

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    } else if (me.data && !me.data.onboarding_completed) {
      router.replace("/onboarding");
    }
  }, [me.error, me.data, router, pathname]);

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    qc.clear();
    router.replace("/login");
  }

  if (!me.data || !me.data.onboarding_completed) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-6" aria-busy="true">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40" />
        {me.error && !(me.error instanceof ApiError && me.error.status === 401) && (
          <p className="text-sm text-danger">Cannot reach the API. Is the backend running?</p>
        )}
      </div>
    );
  }

  const link = ({ href, label, icon: Icon, stage }: (typeof NAV)[number]) => {
    const active = pathname === href || pathname.startsWith(`${href}/`);
    const i = journey.stages.findIndex((s) => s.key === stage);
    return (
      <Link
        key={href}
        href={href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          active && "bg-primary-soft text-accent-foreground hover:bg-primary-soft",
        )}
      >
        {i !== -1 ? <StageDot index={i} status={journey.stages[i].status} current={i === journey.current} /> : <Icon className="size-4" aria-hidden />}
        <span className="flex-1">{label}</span>
        {i !== -1 && i === journey.current && <span className="text-[10px] font-semibold uppercase tracking-wide text-primary">Next</span>}
      </Link>
    );
  };
  const steps = NAV.filter((n) => n.stage);
  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5" onClick={() => setOpen(false)}>
      {link(NAV[0])}
      <p className="mt-4 mb-1 flex items-center justify-between px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Your journey
        {journey.ready && <span className="normal-case tracking-normal tabular-nums">{journey.stages.filter((s) => s.status === "done").length}/{journey.stages.length} done</span>}
      </p>
      {steps.map(link)}
      <p className="mt-4 mb-1 px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">More</p>
      {NAV.filter((n) => !n.stage && n.href !== "/dashboard").map(link)}
    </nav>
  );

  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-card focus:p-2">
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r bg-card px-3 py-4 lg:flex">
        <Brand />
        <div className="mt-6 flex-1">{nav}</div>
        <UserBox email={me.data.email} name={me.data.full_name} onLogout={logout} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center justify-between border-b bg-card/95 px-4 py-3 backdrop-blur lg:hidden">
          <Brand />
          <Button variant="ghost" size="icon" onClick={() => setOpen((v) => !v)} aria-label="Toggle navigation" aria-expanded={open}>
            {open ? <X /> : <Menu />}
          </Button>
        </header>
        {open && (
          <div className="border-b bg-card px-3 py-3 lg:hidden">
            {nav}
            <div className="mt-3 border-t pt-3">
              <UserBox email={me.data.email} name={me.data.full_name} onLogout={logout} />
            </div>
          </div>
        )}
        <DemoBanner />
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <Link href="/dashboard" className="flex items-center gap-2 px-2 font-semibold">
      <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
        <GraduationCap className="size-4" aria-hidden />
      </span>
      CareerPilot AI
    </Link>
  );
}

function UserBox({ email, name, onLogout }: { email: string; name: string; onLogout: () => void }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md px-2 py-1">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{name || "Your account"}</p>
        <p className="truncate text-xs text-muted-foreground">{email}</p>
      </div>
      <Button variant="ghost" size="icon" onClick={onLogout} aria-label="Log out">
        <LogOut />
      </Button>
    </div>
  );
}
