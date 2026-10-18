// Captures dashboard screenshots from a running stack for the README and visual review.
// Usage: VEYL_URL=http://127.0.0.1:3000 VEYL_WORKSPACE=demo VEYL_EMAIL=... VEYL_PASSWORD=... \
//        VEYL_QUESTION="What flights may employees book?" node scripts/screenshots.mjs
// Optional member pass: VEYL_MEMBER_EMAIL, VEYL_MEMBER_PASSWORD, VEYL_MEMBER_QUESTION.
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const base = process.env.VEYL_URL ?? "http://127.0.0.1:3000";
const out = new URL("../../docs/screenshots/", import.meta.url);
await mkdir(out, { recursive: true });

const browser = await chromium.launch();
// Reduced motion: screenshots show the settled page, never a frame of an animation.
const VIEW = { viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" };

async function signIn(email, password) {
  const context = await browser.newContext(VIEW);
  const page = await context.newPage();
  await page.goto(`${base}/login`);
  await page.getByLabel("Workspace").fill(process.env.VEYL_WORKSPACE);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/chat");
  return page;
}

async function ask(page, question) {
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Ask" }).click();
  await page.getByRole("article").first().waitFor({ timeout: 60_000 });
}

const shot = (page, name) => page.screenshot({ path: fileURLToPath(new URL(`${name}.png`, out)) });
const rows = (page) => page.locator("tbody tr").first().waitFor();

const login = await (await browser.newContext(VIEW)).newPage();
await login.goto(`${base}/login`);
await shot(login, "login");

const admin = await signIn(process.env.VEYL_EMAIL, process.env.VEYL_PASSWORD);
await ask(admin, process.env.VEYL_QUESTION ?? "What flights may employees book?");
await shot(admin, "ask");

await admin.goto(`${base}/documents`);
await rows(admin);
await shot(admin, "documents");
await admin.getByRole("table").getByRole("link").first().click();
await admin.waitForURL(/\/documents\/[0-9a-f-]+$/);
await admin.getByRole("heading", { name: "Who can read this" }).waitFor();
await admin.getByRole("group", { name: "Also share with" }).waitFor();
await shot(admin, "document");

await admin.goto(`${base}/admin/users`);
await rows(admin);
await shot(admin, "people");
await admin.goto(`${base}/admin/audit`);
await rows(admin);
await shot(admin, "audit");

if (process.env.VEYL_MEMBER_EMAIL) {
  const member = await signIn(process.env.VEYL_MEMBER_EMAIL, process.env.VEYL_MEMBER_PASSWORD);
  await ask(member, process.env.VEYL_MEMBER_QUESTION ?? "What are the salary bands?");
  await shot(member, "member-ask");
  await member.goto(`${base}/documents`);
  await rows(member);
  await shot(member, "member-documents");
}

await browser.close();
console.log("Saved screenshots to docs/screenshots/");
