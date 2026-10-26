"use client";

import { useDashboard, useProfile, useTodayTasks } from "@/lib/queries";

export type StageStatus = "done" | "active" | "todo";

export interface Stage {
  key: "resume" | "gaps" | "roadmap" | "interviews" | "applications";
  label: string;
  href: string;
  status: StageStatus;
  /** One short line on where the user stands in this stage. */
  detail: string;
}

export interface NextStep {
  title: string;
  why: string;
  /** Either a link or the special "run-analysis" action handled by the dashboard. */
  href: string | "run-analysis";
  cta: string;
}

/**
 * The user's journey (resume -> skill gaps -> roadmap -> interviews -> applications) and the single
 * most useful next action, derived from data the app already loads. `ready` is false until both
 * profile and dashboard have loaded.
 */
export function useJourney(enabled = true) {
  const profile = useProfile(enabled);
  const dash = useDashboard(enabled);
  const today = useTodayTasks(enabled);
  const p = profile.data;
  const d = dash.data;
  if (!p || !d) return { ready: false as const, stages: [] as Stage[], next: null, current: 0 };

  const hasResume = !!p.active_resume_id || p.skills.length > 0;
  const analyzed = d.skill_gaps.total > 0;
  const rm = d.roadmap;
  const applications = Object.values(d.applications).reduce((a, b) => a + b, 0);
  const pendingToday = (today.data ?? []).filter((t) => t.status === "pending").length;

  const stages: Stage[] = [
    {
      key: "resume",
      label: "Resume",
      href: "/resume",
      status: hasResume ? "done" : "todo",
      detail: hasResume ? `${p.skills.length} skills found` : "Upload your resume",
    },
    {
      key: "gaps",
      label: "Skill gaps",
      href: "/gaps",
      status: analyzed ? "done" : "todo",
      detail: analyzed ? `${d.skill_gaps.missing + d.skill_gaps.partial} skills to work on` : "Run the career analysis",
    },
    {
      key: "roadmap",
      label: "Roadmap",
      href: "/roadmap",
      status: !rm ? "todo" : rm.percent >= 100 ? "done" : "active",
      detail: rm ? `${Math.round(rm.percent)}% complete` : "Created by the analysis",
    },
    {
      key: "interviews",
      label: "Mock interviews",
      href: "/interviews",
      status: d.interviews.completed > 0 ? "done" : "todo",
      detail: d.interviews.completed ? `${d.interviews.completed} completed` : "Practice when ready",
    },
    {
      key: "applications",
      label: "Applications",
      href: "/applications",
      status: applications > 0 ? "done" : "todo",
      detail: applications ? `${applications} tracked` : "Track jobs you apply to",
    },
  ];

  let next: NextStep;
  if (!hasResume) {
    next = { title: "Add your resume", why: "Everything else (skill gaps, roadmap, interviews) is built from your skills.", href: "/resume", cta: "Add resume" };
  } else if (!analyzed) {
    next = { title: "Run your career analysis", why: "It finds matching jobs, compares them with your skills and builds a weekly plan. Takes a few seconds.", href: "run-analysis", cta: "Run analysis" };
  } else if (d.overdue_tasks > 0) {
    next = { title: `Catch up: ${d.overdue_tasks} task${d.overdue_tasks === 1 ? " is" : "s are"} overdue`, why: "Replan to spread unfinished work over the remaining weeks without going over your weekly hours.", href: "/roadmap", cta: "Open roadmap" };
  } else if (pendingToday > 0) {
    next = { title: pendingToday === 1 ? "Do today's task" : `Do today's ${pendingToday} tasks`, why: "Tick them off below as you finish; your progress and readiness update automatically.", href: "#today", cta: "Show today's tasks" };
  } else if (d.interviews.completed === 0) {
    next = { title: "Try a mock interview", why: "Your answers are scored, and weak areas are added back into your roadmap.", href: "/interviews", cta: "Start practicing" };
  } else if (applications === 0) {
    next = { title: "Start tracking applications", why: "Keep every application and its next step in one place.", href: "/applications", cta: "Open tracker" };
  } else {
    next = { title: "You're on track", why: "Nothing urgent today. Look ahead in your roadmap or do another mock interview.", href: "/roadmap", cta: "View roadmap" };
  }

  const current = stages.findIndex((s) => s.status !== "done");
  return { ready: true as const, stages, next, current: current === -1 ? stages.length : current };
}
