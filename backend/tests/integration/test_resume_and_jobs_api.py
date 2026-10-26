from tests.conftest import SAMPLE_RESUME, make_docx, make_pdf, onboard


def test_upload_pdf_analysis_review_and_correction(auth_client):
    onboard(auth_client)
    pdf = make_pdf(SAMPLE_RESUME)
    r = auth_client.post("/api/resumes", files={"file": ("cv.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "analyzed" and body["is_active"]
    skills = {s["skill"] for s in body["analysis"]["skills"]}
    assert {"Flask", "PostgreSQL", "Docker"} <= skills
    # Same file again is deduplicated.
    again = auth_client.post("/api/resumes", files={"file": ("cv.pdf", pdf, "application/pdf")})
    assert again.json()["id"] == body["id"]
    profile = auth_client.get("/api/profile").json()
    assert profile["years_experience"] and profile["experiences"][0]["company"] == "Northwind Analytics"
    redis = next(s for s in profile["skills"] if s["skill"] == "Redis")
    assert redis["evidence_level"] == "listed"
    # User corrections: confirm a skill, fix an experience, add a missing skill.
    assert auth_client.patch(f"/api/profile/skills/{redis['id']}", json={"user_confirmed": True}).status_code == 200
    exp_id = profile["experiences"][0]["id"]
    assert (
        auth_client.patch(f"/api/profile/experiences/{exp_id}", json={"location": "Bengaluru"}).json()["location"]
        == "Bengaluru"
    )
    added = auth_client.post("/api/profile/skills", json={"skill": "k8s", "evidence": "Ran a homelab cluster"})
    assert added.status_code == 201 and added.json()["skill"] == "Kubernetes"
    assert added.json()["evidence_level"] == "user_asserted"
    unknown = auth_client.post("/api/profile/skills", json={"skill": "Telepathy"})
    assert unknown.status_code == 400 and unknown.json()["error"]["code"] == "unknown_skill"
    # Re-analysis keeps user-confirmed and user-added skills.
    auth_client.post(f"/api/resumes/{body['id']}/reanalyze")
    after = {s["skill"]: s for s in auth_client.get("/api/profile").json()["skills"]}
    assert after["Kubernetes"]["source"] == "user" and after["Redis"]["user_confirmed"]


def test_upload_validation(auth_client):
    r = auth_client.post("/api/resumes", files={"file": ("cv.exe", b"MZ" + b"0" * 100, "application/octet-stream")})
    assert r.status_code == 415
    r = auth_client.post("/api/resumes", files={"file": ("cv.pdf", make_docx(SAMPLE_RESUME), "application/pdf")})
    assert r.status_code == 415
    big = b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024 + 10)
    r = auth_client.post("/api/resumes", files={"file": ("cv.pdf", big, "application/pdf")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    ok = auth_client.post(
        "/api/resumes", files={"file": ("cv.docx", make_docx(SAMPLE_RESUME), "application/octet-stream")}
    )
    assert ok.status_code == 201 and ok.json()["content_type"].endswith("document")


def test_resume_to_job_description_comparison(auth_client):
    onboard(auth_client)
    rid = auth_client.post("/api/resumes/sample").json()["id"]
    jd = "Requirements:\n- Python and FastAPI\n- Kubernetes in production\nNice to have:\n- Redis"
    r = auth_client.post(f"/api/resumes/{rid}/compare", json={"job_description": jd})
    assert r.status_code == 200
    body = r.json()
    assert body["tailoring"]["missing_required"] == ["Kubernetes"]
    assert 0 < body["skill_score"] < 100
    statuses = {d["skill"]: d["status"] for d in body["details"]}
    assert statuses["Kubernetes"] == "partial"  # transferable from Docker, never a full match


def test_job_search_filters_details_and_bookmarks(auth_client):
    onboard(auth_client)
    auth_client.post("/api/resumes/sample")
    refresh = auth_client.post("/api/jobs/refresh").json()
    assert refresh["demo_data"] and refresh["after_dedupe"] < refresh["fetched"]
    page = auth_client.get("/api/jobs", params={"page_size": 5}).json()
    assert page["total"] >= 8 and len(page["items"]) == 5
    scores = [j["match"]["score"] for j in page["items"]]
    assert scores == sorted(scores, reverse=True)
    remote = auth_client.get("/api/jobs", params={"arrangement": "remote"}).json()["items"]
    assert remote and all(j["work_arrangement"] == "remote" for j in remote)
    pg = auth_client.get("/api/jobs", params={"skills": ["Kafka"]}).json()["items"]
    assert pg and all("Kafka" in j["required_skills"] + j["preferred_skills"] for j in pg)
    rel = auth_client.get("/api/jobs", params={"q": "payments", "sort": "relevance"}).json()
    assert rel["total"] >= 1
    job_id = page["items"][0]["id"]
    detail = auth_client.get(f"/api/jobs/{job_id}").json()
    assert detail["is_demo"] and "DEMO" in detail["availability_note"]
    assert detail["match"]["details"] and detail["tailoring"]["suggestions"]
    assert auth_client.post(f"/api/jobs/{job_id}/save").json()["is_saved"]
    saved = auth_client.get("/api/jobs", params={"saved": True}).json()["items"]
    assert [j["id"] for j in saved] == [job_id]
    apps = auth_client.get("/api/applications").json()
    assert apps[0]["job_id"] == job_id and apps[0]["status"] == "saved"


def test_application_tracker_lifecycle(auth_client):
    a = auth_client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "title": "Backend Engineer",
            "job_url": "https://jobs.example.com/1",
            "next_step": "Prepare",
            "next_step_due": "2030-01-01",
        },
    ).json()
    bad = auth_client.post(
        "/api/applications", json={"company": "Acme", "title": "X", "job_url": "javascript:alert(1)"}
    )
    assert bad.status_code == 422
    for st in ("planned", "applied", "interviewing", "offered"):
        assert auth_client.patch(f"/api/applications/{a['id']}", json={"status": st}).status_code == 200
    final = auth_client.get("/api/applications").json()[0]
    assert [h["status"] for h in final["status_history"]] == ["saved", "planned", "applied", "interviewing", "offered"]
    assert final["applied_on"] is not None
    assert len(auth_client.get("/api/applications", params={"status": "offered"}).json()) == 1
    assert auth_client.delete(f"/api/applications/{a['id']}").status_code == 204
