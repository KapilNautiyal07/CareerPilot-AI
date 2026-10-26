// Types mirroring backend/app/schemas (only fields the UI uses).

export type GapStatus = "matched" | "partial" | "missing";
export type ApplicationStatus = "saved" | "planned" | "applied" | "interviewing" | "offered" | "rejected" | "withdrawn";
export type WorkflowStatus = "queued" | "running" | "awaiting_input" | "succeeded" | "partial" | "failed";

export interface User {
  id: number;
  email: string;
  full_name: string;
  is_demo: boolean;
  created_at: string;
  onboarding_completed: boolean;
}

export interface Meta {
  app: string;
  demo_mode: boolean;
  llm: { provider: string; model: string | null };
  job_providers: string[];
  mock_jobs: boolean;
  live_job_providers: string[];
  code_sandbox: string;
  max_upload_mb: number;
  roles: { key: string; title: string }[];
}

export interface Skill {
  id: number;
  skill: string;
  category: string;
  source: "explicit" | "inferred" | "user";
  evidence_level: "assessed" | "demonstrated" | "listed" | "inferred" | "user_asserted";
  evidence: string[];
  years: number | null;
  confidence: number;
  user_confirmed: boolean;
}

export interface Experience {
  id: number;
  title: string;
  company: string;
  location: string;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  highlights: string[];
  skills: string[];
}

export interface Preferences {
  weekly_study_hours: number;
  study_days_per_week: number;
  target_deadline: string | null;
  preferred_locations: string[];
  work_arrangements: string[];
  salary_min: number | null;
  salary_currency: string;
  interview_categories: string[];
}

export interface Profile {
  id: number;
  headline: string;
  target_role: string;
  target_seniority: string;
  years_experience: number | null;
  location: string;
  summary: string;
  education: { institution: string; degree: string; field: string; graduation_year: number | null; raw: string }[];
  certifications: { name: string; issuer: string; year: number | null }[];
  achievements: string[];
  onboarding_completed: boolean;
  active_resume_id: number | null;
  skills: Skill[];
  experiences: Experience[];
  projects: { id: number; name: string; description: string; technologies: string[]; url: string | null; highlights: string[] }[];
  preferences: Preferences | null;
}

export interface ResumeAnalysis {
  candidate_name: string | null;
  summary: string;
  total_years_experience: number | null;
  missing_information: string[];
  unclear_claims: { claim: string; reason: string }[];
  security_flags: { code: string; snippet: string }[];
  extraction_method: "heuristic" | "llm_grounded";
  warnings: string[];
}

export interface Resume {
  id: number;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  status: "uploaded" | "parsed" | "analyzed" | "failed";
  uploaded_at: string;
  analyzed_at: string | null;
  security_flags: { code: string; snippet: string }[];
  error: string | null;
  is_active: boolean;
  analysis: ResumeAnalysis | null;
}

export interface SkillMatchDetail {
  skill: string;
  level: "required" | "preferred";
  status: GapStatus;
  credit: number;
  evidence_level: string | null;
  via: string | null;
  explanation: string;
}

export interface JobMatch {
  score: number;
  skill_score: number;
  experience_score: number;
  preference_score: number;
  details: SkillMatchDetail[];
  notes: string[];
  computed_at: string;
}

export interface Job {
  id: number;
  source: string;
  title: string;
  company: string;
  location: string;
  work_arrangement: string;
  employment_type: string;
  seniority: string;
  min_years_experience: number | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  application_url: string | null;
  source_url: string | null;
  posted_at: string | null;
  retrieved_at: string;
  last_checked_at: string;
  is_demo: boolean;
  quality_flags: string[];
  required_skills: string[];
  preferred_skills: string[];
  is_saved: boolean;
  application_status: ApplicationStatus | null;
  match: JobMatch | null;
}

export interface Suggestion {
  kind: string;
  section: string;
  suggestion: string;
  rationale: string;
  skill: string | null;
}

export interface Tailoring {
  job_title: string;
  matched_keywords: string[];
  missing_required: string[];
  missing_preferred: string[];
  suggestions: Suggestion[];
  disclaimer: string;
}

export interface JobDetail extends Job {
  description: string;
  requirements: { skill: string; level: string; evidence_text: string }[];
  tailoring: Tailoring | null;
  availability_note: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface SkillGap {
  id: number;
  skill: string;
  category: string;
  status: GapStatus;
  requirement_level: "required" | "preferred";
  demand_share: number;
  evidence_strength: number;
  evidence: string[];
  priority_score: number;
  priority_components: { importance: number; severity: number; urgency: number; effort_penalty: number; formula: string };
  estimated_hours: number;
  transferable_from: string[];
  recommendation: string;
}

export interface GapSnapshot {
  snapshot_id: string;
  created_at: string;
  matched: number;
  partial: number;
  missing: number;
  matched_share: number;
}

export interface GapsResponse {
  target_role: string;
  snapshot_id: string | null;
  computed_at: string | null;
  counts: Record<GapStatus, number>;
  items: SkillGap[];
  history: GapSnapshot[];
}

export interface ResourceRef {
  id: number;
  title: string;
  url: string;
  provider: string;
  resource_type: string;
  is_free: boolean;
  verification_status: "unverified" | "reachable" | "unreachable";
  last_checked_at: string | null;
}

export interface Task {
  id: number;
  week_index: number;
  scheduled_date: string;
  due_date: string;
  title: string;
  description: string;
  task_type: "learn" | "practice" | "project" | "revision" | "assessment" | "mock_interview";
  skill: string | null;
  estimated_hours: number;
  status: "pending" | "completed" | "skipped" | "superseded";
  origin: "generated" | "replanned" | "remediation";
  completed_at: string | null;
  self_rating: number | null;
  notes: string;
  resources: ResourceRef[];
  overdue: boolean;
}

export interface EvaluationIssue {
  code: string;
  severity: "error" | "warning";
  message: string;
  skill: string | null;
}

export interface ReadinessComponent {
  name: string;
  weight: number;
  value: number;
  explanation: string;
}

export interface Readiness {
  score: number;
  components: ReadinessComponent[];
  previous_score: number | null;
  delta: number | null;
  disclaimer: string;
}

export interface Roadmap {
  id: number;
  version: number;
  title: string;
  target_role: string;
  start_date: string;
  end_date: string;
  weekly_hours: number;
  study_days_per_week: number;
  summary: {
    capacity_hours: number;
    planned_hours: number;
    covered_skills: string[];
    uncovered_gaps: string[];
    assumptions: string[];
    evaluation?: { valid: boolean; issues: EvaluationIssue[]; checks: { name: string; passed: boolean; detail: string }[]; readiness: Readiness; explanation: string } | null;
  };
  revision_log: { at: string; version: number; reason: string; [k: string]: unknown }[];
  progress: { completed_hours: number; total_hours: number; percent: number; completed_tasks: number; total_tasks: number; overdue_tasks: number };
  weeks: { index: number; start: string; end: string; milestone: string; focus: string[]; planned_hours: number; completed_hours: number; tasks: Task[] }[];
}

export interface Criterion {
  id: string;
  description: string;
  weight: number;
  met: number;
  evidence: string | null;
}

export interface QuestionResponse {
  answer_text: string;
  code: string | null;
  score: number;
  criteria: Criterion[];
  feedback: string;
  mistakes: string[];
  hints_used: number;
  evaluator: string;
  code_execution: { status: string; passed: number; total: number; detail: string; static_issues: string[] } | null;
  submitted_at: string;
}

export interface Question {
  id: number;
  order_index: number;
  kind: "open" | "code" | "behavioral";
  category: string;
  prompt: string;
  skills: string[];
  is_follow_up: boolean;
  hints_available: number;
  hints_revealed: string[];
  code_function: string | null;
  response: QuestionResponse | null;
}

export interface InterviewSession {
  id: number;
  category: string;
  target_role: string;
  difficulty: string;
  status: "in_progress" | "completed" | "abandoned";
  max_questions: number;
  overall_score: number | null;
  summary: {
    overall?: number;
    competencies?: Record<string, number>;
    categories?: Record<string, number>;
    recurring_weaknesses?: string[];
    weak_skills?: string[];
    recommendations?: string[];
    ended_early?: boolean;
    disclaimer?: string;
  };
  started_at: string;
  completed_at: string | null;
  questions: Question[];
  current_question: Question | null;
}

export interface Application {
  id: number;
  job_id: number | null;
  company: string;
  title: string;
  job_url: string | null;
  status: ApplicationStatus;
  applied_on: string | null;
  next_step: string;
  next_step_due: string | null;
  notes: string;
  status_history: { status: string; at: string }[];
  created_at: string;
  updated_at: string;
}

export interface WorkflowStep {
  node: string;
  status: "running" | "succeeded" | "partial" | "failed" | "awaiting_input";
  started_at: string;
  finished_at?: string;
  duration_ms?: number;
  detail?: string;
}

export interface Workflow {
  id: string;
  kind: "career_analysis" | "replan";
  status: WorkflowStatus;
  current_node: string | null;
  steps: WorkflowStep[];
  pending_question: { type: string; field: string; question: string; options: string[]; suggested: string | null } | null;
  result: {
    readiness?: number;
    explanation?: string;
    warnings?: string[];
    issues?: EvaluationIssue[];
    demo_data?: boolean;
    revisions?: number;
    changes?: Record<string, unknown>;
    errors?: { message: string }[];
  } | null;
  error: { code: string; message: string } | null;
  revision_count: number;
  created_at: string;
  finished_at: string | null;
}

export interface Dashboard {
  jobs: { discovered: number; relevant: number; saved: number; demo: number };
  applications: Record<ApplicationStatus, number>;
  skill_gaps: { matched: number; partial: number; missing: number; total: number; completion: number; history: GapSnapshot[] };
  roadmap: { id: number; version: number; completed_hours: number; total_hours: number; percent: number; completed_tasks: number; total_tasks: number; end_date: string } | null;
  upcoming_milestones: { week: number; start: string; end: string; milestone: string }[];
  overdue_tasks: number;
  interviews: { completed: number; trend: { session_id: number; date: string; category: string; score: number }[] };
  readiness: { latest: Readiness | null; trend: { date: string; score: number }[] };
  recent_improvements: { skill: string; from: string; to: string; evidence_from: number; evidence_to: number }[];
  remaining_priorities: { skill: string; status: GapStatus; priority: number }[];
  reminders: { application_id: number; company: string; title: string; next_step: string; due: string; overdue: boolean }[];
  last_workflow: { id: string; kind: string; status: WorkflowStatus; created_at: string } | null;
  modes: { demo_mode: boolean; llm_provider: string; job_providers: string[] };
}
