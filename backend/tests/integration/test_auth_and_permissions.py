from app.core.rate_limit import limiter
from tests.conftest import SAMPLE_RESUME, make_pdf, onboard


def test_register_login_me_logout(client):
    r = client.post("/api/auth/register", json={"email": "Bob@Example.com", "password": "secret-pass-9"})
    assert r.status_code == 201 and r.json()["user"]["email"] == "bob@example.com"
    assert "cp_session" in client.cookies and "password" not in r.text
    assert client.get("/api/auth/me").status_code == 200
    dup = client.post("/api/auth/register", json={"email": "bob@example.com", "password": "secret-pass-9"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "conflict"
    client.headers["X-CSRF-Token"] = client.cookies["cp_csrf"]
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    bad = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrong-pass-1"})
    assert bad.status_code == 401 and bad.json()["error"]["message"] == "Invalid email or password"
    ok = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "secret-pass-9"})
    assert ok.status_code == 200
    # Bearer tokens work for API clients (no CSRF needed without cookies).
    token = ok.json()["access_token"]
    client.cookies.clear()
    assert client.get("/api/profile", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_password_policy_and_validation_envelope(client):
    r = client.post("/api/auth/register", json={"email": "x@example.com", "password": "short"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["details"]


def test_csrf_required_for_cookie_auth(auth_client):
    csrf = auth_client.headers.pop("X-CSRF-Token")
    r = auth_client.put("/api/profile", json={"target_role": "Backend Engineer"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = auth_client.put("/api/profile", json={"target_role": "Backend Engineer"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200


def test_logout_all_invalidates_tokens(auth_client):
    token = auth_client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "correct-horse-1"}
    ).json()["access_token"]
    auth_client.headers["X-CSRF-Token"] = auth_client.cookies["cp_csrf"]
    assert auth_client.post("/api/auth/logout-all").status_code == 204
    assert auth_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_login_rate_limit(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 3)
    limiter.reset()
    codes = [
        client.post("/api/auth/login", json={"email": "n@example.com", "password": "whatever-1"}).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401] and codes[3] == 429


def test_users_cannot_access_each_others_data(make_client):
    a, b = make_client("a@example.com"), make_client("b@example.com")
    onboard(a)
    resume = a.post("/api/resumes", files={"file": ("cv.pdf", make_pdf(SAMPLE_RESUME), "application/pdf")}).json()
    skill_id = a.get("/api/profile").json()["skills"][0]["id"]
    app_id = a.post("/api/applications", json={"company": "Acme", "title": "Engineer"}).json()["id"]
    session = a.post("/api/interviews", json={"category": "python", "num_questions": 1}).json()
    wf = a.post("/api/workflows/career-analysis", json={}).json()["execution_id"]
    task_id = a.get("/api/roadmap").json()["weeks"][0]["tasks"][0]["id"]

    assert b.get(f"/api/resumes/{resume['id']}").status_code == 404
    assert b.delete(f"/api/resumes/{resume['id']}").status_code == 404
    assert b.post(f"/api/resumes/{resume['id']}/compare", json={"job_description": "Python"}).status_code == 404
    assert b.patch(f"/api/profile/skills/{skill_id}", json={"user_confirmed": True}).status_code == 404
    assert b.patch(f"/api/applications/{app_id}", json={"status": "applied"}).status_code == 404
    assert b.get(f"/api/interviews/{session['id']}").status_code == 404
    assert b.post(f"/api/interviews/{session['id']}/answer", json={"answer": "x" * 30}).status_code == 404
    assert b.get(f"/api/workflows/{wf}").status_code == 404
    assert b.patch(f"/api/roadmap/tasks/{task_id}", json={"status": "completed"}).status_code == 404
    assert b.get("/api/roadmap").json() is None and b.get("/api/applications").json() == []
    # Owner still has access.
    assert a.get(f"/api/resumes/{resume['id']}").status_code == 200


def test_unauthenticated_requests_rejected(client):
    for path in ("/api/profile", "/api/dashboard", "/api/jobs", "/api/roadmap", "/api/interviews", "/api/applications"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized", path


def test_account_deletion_removes_data_and_files(auth_client):
    from pathlib import Path

    from app.core.config import get_settings

    onboard(auth_client)
    r = auth_client.post("/api/resumes", files={"file": ("cv.pdf", make_pdf(SAMPLE_RESUME), "application/pdf")})
    storage = Path(get_settings().storage_dir)
    before = len(list(storage.iterdir()))
    assert auth_client.request("DELETE", "/api/auth/me", json={"password": "wrong-pass-1"}).status_code == 401
    assert auth_client.request("DELETE", "/api/auth/me", json={"password": "correct-horse-1"}).status_code == 204
    assert len(list(storage.iterdir())) == before - 1
    assert r.status_code == 201
    login = auth_client.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct-horse-1"})
    assert login.status_code == 401
