import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const TABS = ["COMMAND", "SCHEDULE", "BACKTEST", "SYSTEM"] as const;
const shots = process.env.BDO_SHOTS; // set to a directory to save screenshots

async function open(page: import("@playwright/test").Page, tab: (typeof TABS)[number]) {
  await page.getByRole("tab", { name: tab }).click();
  await expect(page.getByRole("tab", { name: tab })).toHaveAttribute("aria-selected", "true");
}

test("first load is fast and uses the design tokens", async ({ page }) => {
  const started = Date.now();
  await page.goto("/");
  await expect(page.getByRole("tab", { name: "COMMAND" })).toBeVisible();
  expect(Date.now() - started).toBeLessThan(2000);
  const tokens = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    return ["--bg", "--panel", "--text", "--text-dim", "--alert"].map((t) =>
      s.getPropertyValue(t).trim().toLowerCase());
  });
  expect(tokens).toEqual(["#000000", "#0a0a0a", "#f2f2f2", "#8a8a8a", "#ff3b30"]);
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor))
    .toBe("rgb(0, 0, 0)");
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.fonts.check('600 12px "Orbitron"'))).toBe(true);
  expect(await page.evaluate(() => document.fonts.check('12px "Share Tech Mono"'))).toBe(true);
});

test("COMMAND shows live SoC, power and revenue", async ({ page }) => {
  await page.goto("/");
  const soc = page.getByRole("img", { name: /^SOC: \d/ });
  await expect(soc).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("img", { name: /^Power: [+−]?\d/ })).toBeVisible();
  await expect(page.getByText("Revenue today")).toBeVisible();
  await expect(page.getByText("Off schedule")).toBeVisible();
  await expect(page.getByRole("img", { name: "Live power and state of charge" })).toBeVisible({
    timeout: 15_000 });
  const first = await page.getByRole("status").innerText();
  await page.waitForTimeout(2500);
  expect(await page.getByRole("status").innerText()).not.toBe(first); // the clock is moving
  if (shots) await page.screenshot({ path: `${shots}/command.png`, fullPage: true });
});

test("manual override and two-step stop go through the controller", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("img", { name: /^SOC: \d/ })).toBeVisible({ timeout: 15_000 });
  const slider = page.getByRole("slider");
  await expect(slider).toBeDisabled();
  await page.getByRole("radio", { name: "MANUAL" }).click();
  await expect(slider).toBeEnabled();
  await slider.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByText(/STATUS · (MANUAL SETPOINT|SOC AT)/)).toBeVisible({ timeout: 8000 });

  const stop = page.getByRole("button", { name: "Stop", exact: true });
  await stop.click();
  await expect(page.getByRole("button", { name: "Confirm stop" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "MANUAL" })).toBeEnabled(); // not stopped yet
  await page.getByRole("button", { name: "Confirm stop" }).click();
  const resume = page.getByRole("button", { name: "Resume auto" });
  await expect(resume).toBeVisible({ timeout: 8000 });
  await expect(page.getByRole("img", { name: /^Power: 0 kW, IDLE/ })).toBeVisible({ timeout: 8000 });
  await resume.click();
  await expect(page.getByRole("radio", { name: "AUTO" })).toHaveAttribute("aria-checked", "true", {
    timeout: 8000 });
});

test("SCHEDULE plots a day and zooms to 15-minute resolution", async ({ page }) => {
  await page.goto("/");
  await open(page, "SCHEDULE");
  await page.locator("#day-pick").fill("2025-10-14");
  const chart = page.getByRole("img", { name: /Price, dispatch and state of charge/ });
  await expect(chart).toBeVisible();
  await expect(page.getByText("INT 15M").first()).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(97); // header + 96 intervals
  await page.getByRole("button", { name: "1 h" }).click();
  await expect(page.getByText("window 1.00 h")).toBeVisible();
  await chart.hover();
  await expect(page.locator(".readout").first()).toContainText("Price");
  if (shots) {
    await page.getByRole("button", { name: "Day", exact: true }).click();
    await page.screenshot({ path: `${shots}/schedule.png`, fullPage: true });
  }
  await page.locator("#day-pick").fill("2031-01-05");
  await expect(page.getByText(/Prices are never interpolated/)).toBeVisible();
});

test("BACKTEST shows the headline and switches scenarios", async ({ page }) => {
  await page.goto("/");
  await open(page, "BACKTEST");
  await expect(page.getByText("Uplift vs naive rule")).toBeVisible();
  await expect(page.getByRole("img", { name: /Monthly revenue/ })).toBeVisible();
  await expect(page.getByText("0 CONSTRAINT VIOLATIONS")).toBeVisible();
  // The forecast model is a fourth series once it has been run for this scenario.
  await expect(page.getByText("Forecast model keeps")).toBeVisible();
  await expect(page.locator(".legend").getByText("forecast model")).toBeVisible();
  await expect(page.locator(".chart").filter({ hasText: "Hover a month" })
    .locator("rect.dimfill")).toHaveCount(12);
  // v2: revenue by market next to day-ahead only. Reserve prices need an ENTSO-E token, so
  // a stack without one (CI) offers the run instead of showing the result.
  const stored = await (await page.request.get("/api/backtest?year=2025&duration=2")).json();
  if (stored.markets) {
    await expect(page.getByRole("img", { name: /Revenue by market/ })).toBeVisible();
    await expect(page.getByText(/vs day-ahead only/)).toBeVisible();
  } else {
    await expect(page.getByRole("button", { name: "Run multi-market backtest" })).toBeVisible();
  }
  if (shots) {
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${shots}/backtest.png`, fullPage: true });
  }
  await page.getByRole("radio", { name: "4H" }).click();
  await expect(page.getByText(/2,000 KWH/)).toBeVisible();
  await page.getByRole("button", { name: /HIGHEST SPREAD/ }).click();
  await expect(page.getByRole("tab", { name: "SCHEDULE" })).toHaveAttribute("aria-selected", "true");
});

test("SYSTEM shows link, registers and feed; a fault turns the alert accent on", async ({ page }) => {
  await page.goto("/");
  await open(page, "SYSTEM");
  await expect(page.getByRole("img", { name: /Modbus link up/ })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("cell", { name: "WSET" })).toBeVisible();
  await expect(page.getByText("Last fetch")).toBeVisible();
  await page.getByRole("button", { name: "Inject simulated fault" }).click();
  await expect(page.getByText("DEVICE FAULT").first()).toBeVisible({ timeout: 10_000 });
  await open(page, "COMMAND");
  const banner = page.getByRole("alert").filter({ hasText: /forced to 0 kW/ });
  await expect(banner).toBeVisible({ timeout: 10_000 });
  expect(await banner.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(255, 59, 48)");
  await expect(page.getByRole("img", { name: /^Power: 0 kW, IDLE/ })).toBeVisible({ timeout: 10_000 });
  await open(page, "SYSTEM");
  await expect(page.getByRole("cell", { name: "SAFETY" }).first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("cell", { name: "WSET" })).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/system.png`, fullPage: true });
  await page.getByRole("button", { name: "Clear simulated fault" }).click();
  await expect(page.getByRole("button", { name: "Inject simulated fault" })).toBeVisible({
    timeout: 10_000 });
});

test("every screen passes the contrast check", async ({ page }) => {
  await page.goto("/");
  for (const tab of TABS) {
    await open(page, tab);
    await page.waitForTimeout(1500);
    const result = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
    expect(result.violations.flatMap((v) => v.nodes.map((n) => `${tab}: ${n.html}`))).toEqual([]);
  }
});

test("language toggle switches the interface to German and back, and is remembered", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.getByRole("radio", { name: "DE" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "de");
  await expect(page.getByRole("tab", { name: "LEITSTAND" })).toBeVisible();
  await expect(page.getByText("Erlös heute")).toBeVisible();
  await page.getByRole("tab", { name: "BACKTEST" }).click();
  await expect(page.getByText("Erlös Optimierer")).toBeVisible();
  await expect(page.getByText("27.456").first()).toBeVisible(); // German number format
  await expect(page.locator(".legend").getByText("Prognosemodell")).toBeVisible();
  if (shots) {
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${shots}/backtest-de.png`, fullPage: true });
  }
  await page.getByRole("tab", { name: "FAHRPLAN" }).click();
  await expect(page.getByText("Planübersicht")).toBeVisible();
  await page.getByRole("tab", { name: "SYSTEM" }).click();
  await expect(page.getByText("Registerwerte")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "LEITSTAND" })).toBeVisible(); // remembered
  await page.getByRole("radio", { name: "EN" }).click();
  await expect(page.getByRole("tab", { name: "COMMAND" })).toBeVisible();
  await expect(page.getByText("Revenue today")).toBeVisible();
});

test("tabs are keyboard navigable", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "COMMAND" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "SCHEDULE" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "SCHEDULE" })).toHaveAttribute("aria-selected", "true");
});

test("reduced motion switches every animation off", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.locator(".spin").first()).toBeVisible();
  const running = await page.evaluate(() =>
    [...document.querySelectorAll("*")].filter((el) => getComputedStyle(el).animationName !== "none")
      .length);
  expect(running).toBe(0);
});

test("phone shows COMMAND only; tablet stacks panels", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/");
  await expect(page.getByRole("tab", { name: "SCHEDULE" })).toBeHidden();
  await expect(page.getByRole("region", { name: "State of charge" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  if (shots) await page.screenshot({ path: `${shots}/phone.png`, fullPage: true });
  await page.setViewportSize({ width: 820, height: 1100 });
  const boxes = await page.locator(".command > .panel").evaluateAll((els) =>
    els.map((el) => el.getBoundingClientRect().left));
  expect(new Set(boxes.map(Math.round)).size).toBe(1);
});
