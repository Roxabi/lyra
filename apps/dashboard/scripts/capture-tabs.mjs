#!/usr/bin/env node
/** Capture dashboard tab screenshots for redesign audit. */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

const BASE = process.env.DASHBOARD_URL ?? "http://127.0.0.1:5175";
const OUT = process.argv[2] ?? join(process.cwd(), "artifacts/dashboard-redesign/before");

const TABS = [
  { name: "overview", path: "/" },
  { name: "design-system", path: "/design-system" },
  { name: "chat", path: "/chat" },
  { name: "agents-list", path: "/agents" },
  { name: "agents-detail", path: "/agents/lyra" },
  { name: "jobs", path: "/jobs" },
  { name: "fleet", path: "/fleet" },
  { name: "ops", path: "/ops" },
];

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

for (const tab of TABS) {
  await page.goto(`${BASE}${tab.path}`, { waitUntil: "networkidle", timeout: 30_000 });
  await page.waitForTimeout(400);
  const file = join(OUT, `${tab.name}.png`);
  await page.screenshot({ path: file, fullPage: tab.path !== "/chat" });
  console.log(`captured ${file}`);
}

await browser.close();