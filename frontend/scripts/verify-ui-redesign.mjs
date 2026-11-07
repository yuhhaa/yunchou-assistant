// Real AMap SDK and tiles; explicit local Wuhan package sample, no interception.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
const root = "screenshots/ui-redesign/after";
const baseURL = process.env.PREVIEW_URL || "http://127.0.0.1:5175";
mkdirSync(root, { recursive: true });
const browser = await chromium.launch({ channel: "msedge" });
const report = [];
async function login(page, role) {
  await page.goto(baseURL);
  await page.getByRole("button", { name: "武汉联调本地样例" }).click();
  await page.getByRole("button", { name: role, exact: true }).click();
}
async function snapshot(page, name, network) {
  const map = page.locator(".amap-workspace").first();
  await map.waitFor();
  await page.waitForFunction(
    () =>
      Number(
        document
          .querySelector(".amap-workspace")
          ?.getAttribute("data-business-points"),
      ) > 0,
  );
  await page.waitForFunction(
    () => {
      const map = document.querySelector(".amap-workspace");
      return (
        map?.getAttribute("data-sdk-ready") === "true" ||
        !!map?.querySelector(".amap-load-error")
      );
    },
    undefined,
    { timeout: 45000 },
  );
  await page.waitForTimeout(1200);
  let interaction = null;
  if (
    name === "driver-1600" &&
    (await map.getAttribute("data-sdk-ready")) === "true"
  ) {
    const digest = async () =>
      createHash("sha256")
        .update(await map.screenshot())
        .digest("hex");
    const before = await digest();
    await map.getByRole("button", { name: "放大地图", exact: true }).click();
    await page.waitForTimeout(1400);
    const zoomed = await digest();
    const box = await map.boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.55);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.68, box.y + box.height * 0.65, {
      steps: 12,
    });
    await page.mouse.up();
    await page.waitForTimeout(1400);
    const dragged = await digest();
    await map
      .getByRole("button", { name: "回到当前路线", exact: true })
      .click();
    await page.waitForTimeout(1000);
    interaction = {
      zoomChangedMap: before !== zoomed,
      dragChangedMap: zoomed !== dragged,
    };
  }
  await page.screenshot({ path: `${root}/${name}.png`, fullPage: true });
  report.push({
    name,
    sdkComplete: (await map.getAttribute("data-sdk-ready")) === "true",
    sdkResponses: network.sdk,
    tileResponses: network.tiles,
    mapFailures: network.failed,
    cancelledMapRequests: network.cancelled,
    failureReasons: network.reasons,
    businessPoints: Number(await map.getAttribute("data-business-points")),
    displayedError: (await map.locator(".amap-load-error b").count())
      ? await map.locator(".amap-load-error b").textContent()
      : null,
    noPageOverflow: await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    demoBusiness: true,
    routeLayers: Number(await map.getAttribute("data-route-layers")),
    localWuhanPackageSample: true,
    interaction,
  });
}
function watchNetwork(page) {
  const result = { sdk: 0, tiles: 0, failed: 0, cancelled: 0, reasons: {} };
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (
      url.hostname === "webapi.amap.com" &&
      url.pathname === "/maps" &&
      response.ok()
    )
      result.sdk++;
    if (
      /(?:amap\.com|autonavi\.com)$/.test(url.hostname) &&
      /tiles?|maptile|vtile|vectormap/i.test(url.pathname) &&
      response.ok()
    )
      result.tiles++;
  });
  page.on("requestfailed", (request) => {
    if (/(?:amap\.com|autonavi\.com)$/.test(new URL(request.url()).hostname)) {
      const reason = request.failure()?.errorText || "UNKNOWN";
      if (reason.includes("ERR_ABORTED")) result.cancelled++;
      else {
        result.failed++;
        result.reasons[reason] = (result.reasons[reason] || 0) + 1;
      }
    }
  });
  return result;
}
try {
  for (const width of [1600, 1440, 1280, 768, 390]) {
    const height = width <= 768 ? 844 : 1000;
    let page = await browser.newPage({
      viewport: { width, height },
      reducedMotion: "reduce",
    });
    let network = watchNetwork(page);
    await login(page, "司机演示");
    await snapshot(page, `driver-${width}`, network);
    await page.close();
    page = await browser.newPage({
      viewport: { width, height },
      reducedMotion: "reduce",
    });
    network = watchNetwork(page);
    await login(page, "调度员演示");
    await page.goto(baseURL + "/#/incidents");
    await snapshot(page, `dispatcher-${width}`, network);
    await page
      .getByRole("button", { name: "执行影响分析", exact: true })
      .click();
    await page.getByText("当前有效分析").waitFor();
    await page
      .getByRole("button", { name: "生成恢复候选", exact: true })
      .click();
    await page.getByRole("link", { name: /比较.*候选/ }).waitFor();
    await page.goto(baseURL + "/#/plans");
    await page.locator(".candidate-tabs button").last().click();
    await snapshot(page, `plans-${width}`, network);
    if (width === 1440) {
      await page.goto(baseURL + "/#/incidents");
      await page.locator(".risk-ribbon").waitFor();
      await page.locator(".ledger-step").nth(1).getByRole("button").click();
      await page.locator(".amap-workspace[data-sdk-ready=true]").waitFor();
      await page.waitForTimeout(1500);
      await page.screenshot({
        path: `${root}/dispatcher-analysis-1440.png`,
        fullPage: true,
      });
    }
    await page.close();
  }
} finally {
  await browser.close();
  writeFileSync(
    `${root}/runtime-verification.json`,
    JSON.stringify(
      {
        date: "2026-11-06",
        scope:
          "Actual AMap SDK, tiles and interaction; no request interception; explicitly chosen local Wuhan package samples, no live business API",
        report,
      },
      null,
      2,
    ),
  );
}
console.log(JSON.stringify(report, null, 2));
