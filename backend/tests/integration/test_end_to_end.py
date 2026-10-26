"""Full user journey through the API, verifying that assessment results change recommendations."""

from tests.conftest import SAMPLE_RESUME, make_pdf, onboard

WEAK_ANSWER = "I am not really sure about this one, I would probably search online and try something out."


def test_full_journey_assessments_influence_recommendations(auth_client):
    c = auth_client
    # 1. Onboarding + resume upload
    onboard(c)
    assert c.get("/api/auth/me").json()["onboarding_completed"]
    up = c.post("/api/resumes", files={"file": ("cv.pdf", make_pdf(SAMPLE_RESUME), "application/pdf")})
    assert up.status_code == 201
    # 2. Analyze skills, discover jobs, generate the roadmap (orchestrated workflow)
    ex = c.post("/api/workflows/career-analysis", json={}).json()["execution_id"]
    assert c.get(f"/api/workflows/{ex}").json()["status"] == "succeeded"
    assert c.get("/api/jobs").json()["total"] > 0
    gaps_before = {g["skill"]: g for g in c.get("/api/gaps").json()["items"]}
    rm = c.get("/api/roadmap").json()
    readiness_before = c.get("/api/dashboard").json()["readiness"]["latest"]["score"]
    # 3. Complete a learning task
    first = rm["weeks"][0]["tasks"][0]
    done = c.patch(f"/api/roadmap/tasks/{first['id']}", json={"status": "completed", "self_rating": 4})
    assert done.status_code == 200 and done.json()["completed_at"]
    # 4. Mock interview on Python: poor answers on every question
    s = c.post("/api/interviews", json={"category": "python", "num_questions": 2}).json()
    assert s["current_question"] and s["status"] == "in_progress"
    hint = c.post(f"/api/interviews/{s['id']}/hint").json()
    assert len(hint["hints_revealed"]) == 1
    replan_id = None
    for _ in range(10):
        r = c.post(f"/api/interviews/{s['id']}/answer", json={"answer": WEAK_ANSWER}).json()
        assert "score" in r["evaluation"]
        if r["session"]["status"] == "completed":
            replan_id = r["replan_execution_id"]
            break
    final = c.get(f"/api/interviews/{s['id']}").json()
    assert final["status"] == "completed" and final["overall_score"] < 40
    assert any(q["is_follow_up"] for q in final["questions"])  # adaptive follow-up was asked
    assert final["questions"][0]["response"]["hints_used"] == 1
    assert "Python" in final["summary"]["weak_skills"]
    history = c.get("/api/assessments", params={"source": "interview"}).json()
    assert any(a["skill"] == "Python" and a["score"] < 40 for a in history)
    # 5. The interview triggered re-evaluation + roadmap update
    assert replan_id is not None
    replan = c.get(f"/api/workflows/{replan_id}").json()
    assert replan["status"] in ("succeeded", "partial"), replan
    assert "Python" in replan["result"]["changes"]["remediation"]
    gaps_after = {g["skill"]: g for g in c.get("/api/gaps").json()["items"]}
    assert gaps_before["Python"]["status"] == "matched" and gaps_after["Python"]["status"] != "matched"
    assert any("assessment" in e.lower() for e in gaps_after["Python"]["evidence"])
    rm2 = c.get("/api/roadmap").json()
    assert rm2["version"] == 2 and rm2["revision_log"][-1]["reason"] == "interview_completed"
    tasks = [t for w in rm2["weeks"] for t in w["tasks"]]
    assert any(t["skill"] == "Python" and t["origin"] == "remediation" for t in tasks)
    assert any(t["id"] == first["id"] and t["status"] == "completed" for t in tasks)  # history kept
    dash = c.get("/api/dashboard").json()
    assert dash["readiness"]["latest"]["score"] < readiness_before
    assert len(dash["readiness"]["trend"]) == 2 and dash["interviews"]["completed"] == 1
    assert dash["roadmap"]["completed_tasks"] == 1 and len(dash["skill_gaps"]["history"]) == 2


def test_interview_end_early_and_code_question(auth_client):
    onboard(auth_client)
    s = auth_client.post("/api/interviews", json={"category": "dsa", "num_questions": 3}).json()
    q = s["current_question"]
    assert q["kind"] in ("code", "open")
    if q["kind"] == "code":
        code = f"def {q['code_function']}(*args):\n    return None\n"
        r = auth_client.post(
            f"/api/interviews/{s['id']}/answer", json={"answer": "Brute force, O(n^2).", "code": code}
        ).json()
        assert r["evaluation"]["code_execution"]["status"] == "not_executed"
    end = auth_client.post(f"/api/interviews/{s['id']}/end").json()
    assert end["session"]["status"] in ("completed", "abandoned")
    assert auth_client.post(f"/api/interviews/{s['id']}/answer", json={"answer": "late"}).status_code == 409
