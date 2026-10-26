import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GapBadge, QualityFlags, ScoreRing } from "@/components/domain";
import { TaskChecklist } from "@/components/task-checklist";
import { Alert } from "@/components/ui/primitives";
import { WorkflowPanel } from "@/components/workflow-panel";
import type { Task, Workflow } from "@/types/api";

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

describe("domain components", () => {
  it("labels gap status and score accessibly", () => {
    render(<><GapBadge status="partial" /><ScoreRing value={72.4} label="Readiness" /></>);
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Readiness: 72 out of 100" })).toBeInTheDocument();
  });

  it("always labels demo listings", () => {
    render(<QualityFlags flags={["demo_data", "stale", "unverifiable"]} hide={["unverifiable"]} />);
    expect(screen.getByText("Demo data")).toBeInTheDocument();
    expect(screen.getByText("Possibly closed")).toBeInTheDocument();
    expect(screen.queryByText("Unverified")).not.toBeInTheDocument();
  });

  it("renders error alerts with role=alert", () => {
    render(<Alert tone="error" title="Failed">Details</Alert>);
    expect(screen.getByRole("alert")).toHaveTextContent("Failed");
  });
});

const task: Task = {
  id: 7, week_index: 1, scheduled_date: "2026-01-05", due_date: "2026-01-11", title: "Learn Redis fundamentals",
  description: "Study core concepts.", task_type: "learn", skill: "Redis", estimated_hours: 2, status: "pending",
  origin: "remediation", completed_at: null, self_rating: null, notes: "", overdue: true,
  resources: [{ id: 1, title: "Redis docs", url: "https://redis.io/docs/", provider: "Redis", resource_type: "docs", is_free: true, verification_status: "unverified", last_checked_at: null }],
};

describe("TaskChecklist", () => {
  it("marks a task complete through the API", async () => {
    const fetch = vi.fn().mockImplementation(() => json({ ...task, status: "completed" }));
    vi.stubGlobal("fetch", fetch);
    wrap(<TaskChecklist tasks={[task]} showResources />);
    expect(screen.getByText("Overdue")).toBeInTheDocument();
    expect(screen.getByText("From assessment")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Redis docs/ })).toHaveAttribute("rel", "noopener noreferrer");
    await userEvent.click(screen.getByRole("checkbox", { name: /Learn Redis fundamentals/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/roadmap/tasks/7");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ status: "completed" });
  });
});

describe("WorkflowPanel", () => {
  const base: Workflow = {
    id: "wf-1", kind: "career_analysis", status: "awaiting_input", current_node: "determine_target",
    steps: [
      { node: "resume_analysis", status: "succeeded", started_at: "", duration_ms: 1200 },
      { node: "determine_target", status: "awaiting_input", started_at: "" },
    ],
    pending_question: { type: "clarification", field: "target_role", question: "Which role are you preparing for?", options: ["Backend Engineer", "Data Engineer"], suggested: null },
    result: null, error: null, revision_count: 0, created_at: "", finished_at: null,
  };

  it("shows agent steps and submits the clarification answer", async () => {
    const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) =>
      init?.method === "POST" ? json({ execution_id: "wf-1" }, 202) : json(base),
    );
    vi.stubGlobal("fetch", fetch);
    wrap(<WorkflowPanel executionId="wf-1" />);
    expect(await screen.findByText("Resume Analysis Agent")).toBeInTheDocument();
    expect(screen.getByText("1.2s")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Which role are you preparing for?"), "Data Engineer");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(fetch.mock.calls.some(([u, i]) => u === "/api/workflows/wf-1/input" && i?.method === "POST")).toBe(true));
    const post = fetch.mock.calls.find(([u]) => u === "/api/workflows/wf-1/input")!;
    expect(JSON.parse(post[1].body)).toEqual({ target_role: "Data Engineer" });
  });

  it("reports failures clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => json({ ...base, status: "failed", pending_question: null, error: { code: "x", message: "Upload a resume first" } })));
    wrap(<WorkflowPanel executionId="wf-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload a resume first");
  });
});
