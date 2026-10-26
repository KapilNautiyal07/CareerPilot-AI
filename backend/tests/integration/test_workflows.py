"""LangGraph workflow integration: routing, interrupts, checkpoint recovery and bounded revision."""

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.db.session import session_scope
from app.models import WorkflowExecution
from tests.conftest import onboard


def _profile_ids(c):
    me = c.get("/api/auth/me").json()
    return me["id"], c.get("/api/profile").json()["id"]


def test_career_analysis_end_to_end(auth_client):
    onboard(auth_client)
    auth_client.post("/api/resumes/sample")
    r = auth_client.post("/api/workflows/career-analysis", json={})
    assert r.status_code == 202
    wf = auth_client.get(f"/api/workflows/{r.json()['execution_id']}").json()
    assert wf["status"] == "succeeded", wf
    nodes = [s["node"] for s in wf["steps"]]
    assert nodes[:4] == ["validate_request", "load_profile", "resume_analysis", "determine_target"]
    assert {"job_research", "resource_retrieval"} <= set(nodes) and nodes[-1] == "persist"
    assert all(s["status"] == "succeeded" for s in wf["steps"])
    res = wf["result"]
    assert res["demo_data"] and res["evaluation_valid"] and 0 < res["readiness"] < 100
    gaps = auth_client.get("/api/gaps").json()
    assert gaps["items"] and gaps["counts"]["matched"] > 0 and gaps["counts"]["missing"] > 0
    top = gaps["items"][0]
    assert top["evidence"] and top["priority_components"]["formula"]
    rm = auth_client.get("/api/roadmap").json()
    assert rm["version"] == 1 and rm["progress"]["total_tasks"] > 10
    assert any(t["resources"] for w in rm["weeks"] for t in w["tasks"])
    assert all(w["planned_hours"] <= rm["weekly_hours"] + 0.01 for w in rm["weeks"])
    dash = auth_client.get("/api/dashboard").json()
    assert dash["jobs"]["discovered"] == dash["jobs"]["demo"] > 0
    assert dash["roadmap"]["total_tasks"] == rm["progress"]["total_tasks"]
    assert dash["readiness"]["latest"]["score"] == res["readiness"]


def test_validation_failure_routes_to_end(auth_client):
    r = auth_client.post("/api/workflows/career-analysis", json={})
    wf = auth_client.get(f"/api/workflows/{r.json()['execution_id']}").json()
    assert wf["status"] == "failed" and "resume" in wf["error"]["message"].lower()
    assert [s["node"] for s in wf["steps"]][-1] == "resume_analysis"


def test_clarification_interrupt_and_resume(auth_client):
    auth_client.post("/api/resumes/sample")  # no onboarding -> no target role
    ex_id = auth_client.post("/api/workflows/career-analysis", json={}).json()["execution_id"]
    wf = auth_client.get(f"/api/workflows/{ex_id}").json()
    assert wf["status"] == "awaiting_input"
    assert wf["pending_question"]["field"] == "target_role" and "Backend Engineer" in wf["pending_question"]["options"]
    assert (
        auth_client.post(f"/api/workflows/{ex_id}/input", json={"target_role": "Backend Engineer"}).status_code == 202
    )
    wf = auth_client.get(f"/api/workflows/{ex_id}").json()
    assert wf["status"] == "succeeded", wf
    # Nodes before the interrupt were not re-run on resume (checkpointed).
    assert [s["node"] for s in wf["steps"]].count("resume_analysis") == 1
    assert auth_client.get("/api/profile").json()["target_role"] == "Backend Engineer"
    again = auth_client.post(f"/api/workflows/{ex_id}/input", json={"target_role": "x" * 5})
    assert again.status_code == 400


def test_checkpoint_resume_after_node_failure(auth_client, monkeypatch):
    """A crash mid-graph resumes from the last checkpoint without repeating completed nodes."""
    import app.graph.career_graph as cg

    onboard(auth_client)
    auth_client.post("/api/resumes/sample")
    user_id, profile_id = _profile_ids(auth_client)
    calls = {"resume": 0, "gap": 0}
    real_resume, real_gap = cg.ResumeAnalysisAgent.analyze, cg.SkillGapAgent.analyze

    def counting_resume(self, *a, **k):
        calls["resume"] += 1
        return real_resume(self, *a, **k)

    def flaky_gap(self, *a, **k):
        calls["gap"] += 1
        if calls["gap"] == 1:
            raise RuntimeError("simulated crash")
        return real_gap(self, *a, **k)

    monkeypatch.setattr(cg.ResumeAnalysisAgent, "analyze", counting_resume)
    monkeypatch.setattr(cg.SkillGapAgent, "analyze", flaky_gap)
    graph = cg.build_career_graph(InMemorySaver())
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    state = {"user_id": user_id, "request": {"reanalyze": True}, "errors": [], "warnings": [], "revision_history": []}
    with pytest.raises(RuntimeError):
        graph.invoke(state, config)
    assert graph.get_state(config).next == ("skill_gap",)
    out = graph.invoke(None, config)  # resume
    assert out["status"] == "succeeded" and calls == {"resume": 1, "gap": 2}


def test_bounded_revision_loop(auth_client, monkeypatch):
    import app.graph.career_graph as cg
    from app.core.config import get_settings

    onboard(auth_client)
    auth_client.post("/api/resumes/sample")
    real_plan = cg.RoadmapAgent.plan
    seen = []

    def plan_dropping_top_gap_first(self, gaps, params, resources):
        plan = real_plan(self, gaps, params, resources)
        seen.append(list(params.forced_skills))
        if not params.forced_skills:  # first attempt "forgets" the top gaps
            plan.covered_skills = []
        return plan

    monkeypatch.setattr(cg.RoadmapAgent, "plan", plan_dropping_top_gap_first)
    ex_id = auth_client.post("/api/workflows/career-analysis", json={}).json()["execution_id"]
    wf = auth_client.get(f"/api/workflows/{ex_id}").json()
    assert wf["status"] == "succeeded" and wf["revision_count"] == 1
    assert seen[0] == [] and len(seen[1]) >= 1  # the revision forced the uncovered gaps in
    assert [s["node"] for s in wf["steps"]].count("revise") == 1

    # When the output never validates, the loop stops at the bound and records the issues.
    monkeypatch.setattr(cg.RoadmapAgent, "plan", lambda self, g, p, r: _always_bad(real_plan(self, g, p, r)))
    with session_scope() as db:
        db.query(WorkflowExecution).delete()
    ex2 = auth_client.post("/api/workflows/career-analysis", json={}).json()["execution_id"]
    wf2 = auth_client.get(f"/api/workflows/{ex2}").json()
    assert wf2["status"] == "partial" and wf2["revision_count"] == get_settings().max_revision_loops
    assert any(i["code"] == "top_gap_uncovered" for i in wf2["result"]["issues"])


def _always_bad(plan):
    plan.covered_skills = []
    return plan


def test_graph_diagrams_are_inspectable(client):
    for kind in ("career", "progress", "interview"):
        mermaid = client.get(f"/api/workflows/graph/{kind}").json()["mermaid"]
        assert "graph TD" in mermaid or "flowchart" in mermaid
    assert "job_research" in client.get("/api/workflows/graph/career").json()["mermaid"]
