import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, buildQuery, errorMessage } from "@/lib/api";
import { formatSalary, pct, relativeDays, scoreTone, titleCase } from "@/lib/utils";

function mockFetch(status: number, body: unknown) {
  // A fresh Response per call: bodies can only be read once.
  const fn = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    ),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "cp_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("api client", () => {
  it("sends the CSRF cookie value on unsafe methods only", async () => {
    document.cookie = "cp_csrf=tok123";
    const fetch = mockFetch(200, { ok: true });
    await api("/profile", { method: "PUT", body: { a: 1 } });
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/profile");
    expect(init.headers["X-CSRF-Token"]).toBe("tok123");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"a":1}');
    await api("/profile");
    expect(fetch.mock.calls[1][1].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("does not set a JSON content type for multipart uploads", async () => {
    const fetch = mockFetch(201, { id: 1 });
    const form = new FormData();
    form.append("file", new Blob(["x"]), "cv.pdf");
    await api("/resumes", { form });
    const init = fetch.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBe(form);
  });

  it("parses the backend error envelope", async () => {
    mockFetch(409, { error: { code: "conflict", message: "Already exists", details: null } });
    await expect(api("/x", { method: "POST", body: {} })).rejects.toMatchObject({ status: 409, code: "conflict", message: "Already exists" });
  });

  it("explains a bare 5xx from the proxy as an unreachable backend", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response("Internal Server Error", { status: 500 }))));
    await expect(api("/auth/register", { body: {} })).rejects.toMatchObject({ code: "backend_unreachable" });
  });

  it("formats validation errors with the field name", () => {
    const err = new ApiError(422, "validation_error", "Request validation failed", [
      { loc: ["body", "weekly_study_hours"], msg: "Input should be less than or equal to 80" },
    ]);
    expect(errorMessage(err)).toBe("weekly_study_hours: Input should be less than or equal to 80");
  });

  it("returns undefined for 204 and maps network failures", async () => {
    mockFetch(204, undefined);
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/x")).rejects.toMatchObject({ code: "network_error" });
  });

  it("builds query strings, skipping empty values and repeating arrays", () => {
    expect(buildQuery({ q: "go", empty: "", n: undefined, skills: ["A", "B"], saved: false })).toBe("?q=go&skills=A&skills=B&saved=false");
    expect(buildQuery({})).toBe("");
  });
});

describe("utils", () => {
  it("formats values", () => {
    expect(pct(0.425)).toBe("43%");
    expect(pct(null)).toBe("—");
    expect(titleCase("system_design")).toBe("System Design");
    expect(scoreTone(80)).toBe("success");
    expect(scoreTone(50)).toBe("warning");
    expect(scoreTone(10)).toBe("danger");
    expect(scoreTone(null)).toBe("muted");
    expect(formatSalary(null, null, "USD")).toBeNull();
    // Compact notation follows the user's locale (e.g. "130K" or "1.3L"), so only check the shape.
    expect(formatSalary(90000, 130000, "USD")).toMatch(/^USD [^\s–]+–[^\s–]+$/);
    expect(relativeDays(null)).toBe("unknown date");
    expect(relativeDays(new Date().toISOString())).toBe("today");
  });
});
