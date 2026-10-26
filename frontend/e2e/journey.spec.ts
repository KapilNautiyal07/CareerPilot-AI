import { expect, type Page, test } from "@playwright/test";

const email = () => `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
const PASSWORD = "e2e-password-123";

async function signUp(page: Page) {
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Riya Test");
  await page.getByLabel("Email").fill(email());
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/onboarding/);
}

async function onboard(page: Page) {
  await page.getByLabel("Target role").fill("Backend Engineer");
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByLabel("Preferred locations").fill("Bengaluru");
  await page.getByText("Hybrid", { exact: true }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByLabel("Study hours per week").fill("8");
  await page.getByRole("button", { name: "Save and continue" }).click();
  await expect(page.getByText("Add your resume")).toBeVisible();
}

test("full journey: onboarding → resume → analysis → roadmap task → interview → updated plan", async ({ page }) => {
  await signUp(page);

  // Validation errors are shown inline before anything is sent.
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Choose or type a target role")).toBeVisible();
  await onboard(page);

  // Upload: use the clearly labeled demo resume, then start the orchestrated workflow.
  await page.getByRole("button", { name: "Use demo resume" }).click();
  await expect(page.getByText("Resume analyzed")).toBeVisible();
  await page.getByRole("button", { name: "Run career analysis" }).click();
  await expect(page).toHaveURL(/\/dashboard\?run=/);
  await expect(page.getByRole("heading", { name: /Career analysis/ })).toBeVisible();
  await expect(page.getByText("All done")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Demo mode:")).toBeVisible();

  // Dashboard shows persisted metrics.
  await expect(page.getByText("Relevant jobs")).toBeVisible();
  await expect(page.getByRole("img", { name: /Readiness: \d+ out of 100/ })).toBeVisible();

  // Resume review shows evidence-backed skills.
  await page.getByRole("link", { name: "Resume" }).first().click();
  await expect(page.getByText("Demonstrated in work or projects")).toBeVisible();
  await expect(page.getByText("Listed only (no described usage)")).toBeVisible();

  // Jobs: ranked, labeled demo listings with match explanations.
  await page.getByRole("link", { name: "Jobs" }).first().click();
  await expect(page.getByText(/\d+ jobs/)).toBeVisible();
  await page.locator("main a[href^='/jobs/']").first().click();
  await expect(page.getByText("Skill comparison")).toBeVisible();
  await expect(page.getByText(/DEMO listing/)).toBeVisible();

  // Roadmap: complete a learning task.
  await page.getByRole("link", { name: "Roadmap" }).first().click();
  await expect(page.getByText("Where you are")).toBeVisible();
  await page.getByText("How this plan was built").click();
  await expect(page.getByText("Validation by the Career Evaluation Agent")).toBeVisible();
  const firstTask = page.getByRole("checkbox").first();
  await firstTask.check();
  await expect(firstTask).toBeChecked();

  // Mock interview with weak answers → results → roadmap update triggered.
  await page.getByRole("link", { name: "Interviews" }).first().click();
  await page.getByLabel("Category").selectOption("python");
  await page.getByLabel("Questions").fill("1");
  await page.getByRole("button", { name: "Start interview" }).click();
  await expect(page).toHaveURL(/\/interviews\/\d+/);
  await expect(page.getByRole("button", { name: "Submit answer" })).toBeVisible();
  for (let i = 0; i < 4; i++) {
    const submit = page.getByRole("button", { name: "Submit answer" });
    if (!(await submit.isVisible().catch(() => false))) break;
    await page.getByLabel(/Your answer|Explain your approach/).fill("I am not sure, I would look it up online and try a few things until it works.");
    await Promise.all([page.waitForResponse((r) => r.url().endsWith("/answer") && r.ok()), submit.click()]);
    await expect(page.getByText("Feedback on your last answer")).toBeVisible();
    await expect(submit).toBeEnabled({ timeout: 5_000 }).catch(() => undefined); // gone once the interview ends
  }
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Roadmap update · (succeeded|partial)/ })).toBeVisible({ timeout: 60_000 });

  await page.getByRole("link", { name: "Open updated roadmap" }).click();
  await expect(page.getByText(/version 2/)).toBeVisible();
  await expect(page.getByText("From assessment").first()).toBeVisible();
});

test("protected pages redirect to login and bad credentials show an error", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login\?next=%2Fdashboard/);
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("wrong-password-1");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Invalid email or password" })).toBeVisible();
});

test("application tracker works on a phone-sized screen @mobile", async ({ page }) => {
  await signUp(page);
  await onboard(page);
  await page.getByRole("button", { name: "Skip for now" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await page.getByRole("link", { name: "Applications" }).click();
  await page.getByRole("button", { name: "Add application" }).click();
  await page.getByLabel("Company").fill("Acme");
  await page.getByLabel("Role").fill("Backend Engineer");
  await page.getByLabel("Job URL").fill("https://jobs.example.com/1");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Acme")).toBeVisible();
  await page.getByLabel("Status for Backend Engineer").selectOption("applied");
  await expect(page.getByText(/History: Planned → Applied/)).toBeVisible();
  const hasHorizontalScroll = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(hasHorizontalScroll).toBe(false);
});
