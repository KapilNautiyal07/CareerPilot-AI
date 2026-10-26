"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  Application,
  Dashboard,
  GapsResponse,
  InterviewSession,
  Job,
  JobDetail,
  Meta,
  Page,
  Profile,
  Resume,
  Roadmap,
  Task,
  User,
  Workflow,
} from "@/types/api";

export const keys = {
  me: ["me"] as const,
  meta: ["meta"] as const,
  profile: ["profile"] as const,
  dashboard: ["dashboard"] as const,
  resumes: ["resumes"] as const,
  resume: (id: number) => ["resume", id] as const,
  jobs: (params: Record<string, unknown>) => ["jobs", params] as const,
  job: (id: number) => ["job", id] as const,
  gaps: ["gaps"] as const,
  roadmap: ["roadmap"] as const,
  today: ["roadmap", "today"] as const,
  interviews: ["interviews"] as const,
  interview: (id: number) => ["interview", id] as const,
  applications: ["applications"] as const,
  workflows: ["workflows"] as const,
  workflow: (id: string) => ["workflow", id] as const,
};

const TERMINAL = new Set(["succeeded", "partial", "failed", "awaiting_input"]);

export const useMe = () => useQuery({ queryKey: keys.me, queryFn: () => api<User>("/auth/me"), retry: false });
export const useMeta = () =>
  useQuery({ queryKey: keys.meta, queryFn: () => api<Meta>("/meta"), staleTime: 5 * 60_000 });
export const useProfile = (enabled = true) =>
  useQuery({ queryKey: keys.profile, queryFn: () => api<Profile>("/profile"), enabled });
export const useDashboard = (enabled = true) =>
  useQuery({ queryKey: keys.dashboard, queryFn: () => api<Dashboard>("/dashboard"), enabled });
export const useResumes = () => useQuery({ queryKey: keys.resumes, queryFn: () => api<Resume[]>("/resumes") });
export const useResume = (id: number | null | undefined) =>
  useQuery({ queryKey: keys.resume(id ?? 0), queryFn: () => api<Resume>(`/resumes/${id}`), enabled: !!id });
export const useJobs = (params: Record<string, string | number | boolean | string[] | undefined>) =>
  useQuery({
    queryKey: keys.jobs(params),
    queryFn: () => api<Page<Job>>("/jobs", { query: params }),
    placeholderData: (prev) => prev,
  });
export const useJob = (id: number) => useQuery({ queryKey: keys.job(id), queryFn: () => api<JobDetail>(`/jobs/${id}`) });
export const useGaps = () => useQuery({ queryKey: keys.gaps, queryFn: () => api<GapsResponse>("/gaps") });
export const useRoadmap = () => useQuery({ queryKey: keys.roadmap, queryFn: () => api<Roadmap | null>("/roadmap") });
export const useTodayTasks = (enabled = true) =>
  useQuery({ queryKey: keys.today, queryFn: () => api<Task[]>("/roadmap/today"), enabled });
export const useInterviews = () =>
  useQuery({ queryKey: keys.interviews, queryFn: () => api<InterviewSession[]>("/interviews") });
export const useInterview = (id: number) =>
  useQuery({ queryKey: keys.interview(id), queryFn: () => api<InterviewSession>(`/interviews/${id}`) });
export const useApplications = () =>
  useQuery({ queryKey: keys.applications, queryFn: () => api<Application[]>("/applications") });
export const useWorkflows = () =>
  useQuery({ queryKey: keys.workflows, queryFn: () => api<Workflow[]>("/workflows", { query: { limit: 5 } }) });

/** Polls an execution every 1.5s until it reaches a terminal or awaiting-input state. */
export const useWorkflow = (id: string | null | undefined) =>
  useQuery({
    queryKey: keys.workflow(id ?? ""),
    queryFn: () => api<Workflow>(`/workflows/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && TERMINAL.has(q.state.data.status) ? false : 1500),
  });

/** Invalidate everything derived from analysis results (after a workflow or task change). */
export function useInvalidateCareerData() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      [keys.dashboard, keys.gaps, keys.roadmap, keys.profile, ["jobs"], keys.workflows, keys.applications].map((k) =>
        qc.invalidateQueries({ queryKey: k }),
      ),
    );
}

export function useTaskUpdate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; status?: string; self_rating?: number; notes?: string }) =>
      api<Task>(`/roadmap/tasks/${id}`, { method: "PATCH", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.roadmap });
      qc.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export function useStartAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { target_role?: string; reanalyze?: boolean; deadline?: string; weekly_hours?: number } = {}) =>
      api<{ execution_id: string }>("/workflows/career-analysis", { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.workflows }),
  });
}
