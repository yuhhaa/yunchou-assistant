import { test, expect } from "./test";
import type { Page } from "@playwright/test";
import { mockRequest, resetFixtures } from "../src/mock/transport";
import {
  geographyIssue,
  type GeographicRouteData,
} from "../src/components/maps/types";
import type { Context } from "../src/api/types";
import { mkdirSync } from "node:fs";
mkdirSync("screenshots/contest1", { recursive: true });
test.beforeEach(() => resetFixtures());
async function login(page: Page, role = "司机演示") {
  await page.goto("/");
  await page.getByRole("button", { name: "体验独立演示空间" }).click();
  await page.getByRole("button", { name: role }).click();
}
test("地理服务失败保留任务信息与列表操作，不伪造地图或驾驶路线", async ({
  page,
}) => {
  await page.route("**/_AMapService/_status", (r) =>
    r.fulfill({ json: { configured: false } }),
  );
  await page.goto("/tests/fixtures/map-harness.html");
  const map = page.locator(".map-view");
  await expect(map.locator(".amap-load-error")).toBeVisible({ timeout: 18000 });
  await expect(map.locator(".map-node-inspector")).toContainText("O001");
  await expect(page.locator(".route-network-svg")).toHaveCount(0);
  await page.getByRole("button", { name: "列表执行反馈 0" }).click();
  await expect(
    page.getByRole("button", { name: "列表执行反馈 1" }),
  ).toBeVisible();
});
for (const size of [
  { width: 1440, height: 1000 },
  { width: 1280, height: 900 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
]) {
  test(
    "赛题一司机桌面与手机、地图待接入 " + size.width + "px",
    async ({ page }) => {
      await page.setViewportSize(size);
      await login(page);
      const left = await page.locator(".next-stop-card").boundingBox(),
        right = await page.locator(".driver-map-column").boundingBox();
      if (size.width >= 1024) {
        expect(right!.x).toBeGreaterThan(left!.x + left!.width);
        await expect(page.locator(".driver-bottom-nav")).toBeHidden();
      } else await expect(page.locator(".driver-bottom-nav")).toBeVisible();
      const map = page.locator(".driver-map-column .map-view");
      await expect(map).toContainText("真实地理数据尚未接入");
      await expect(page.locator(".delivery-map-marker")).toHaveCount(0);
      await page.locator(".route-stop").filter({ hasText: "O002" }).click();
      await expect(map.locator(".map-node-inspector")).toContainText("O002");
      await page.screenshot({
        path: "screenshots/contest1/driver-" + size.width + ".png",
        fullPage: size.width < 1024,
      });
      await map.getByRole("button", { name: "展开配送地图" }).click();
      await expect(
        page.getByRole("dialog", { name: "我的配送路线" }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
    },
  );
  test(
    "赛题一调度与候选、地图缺项不影响业务 " + size.width + "px",
    async ({ page }) => {
      await page.setViewportSize(size);
      await login(page, "调度员演示");
      if (size.width <= 680)
        await page.getByRole("button", { name: "展开导航" }).click();
      await page.getByRole("link", { name: "异常工作台", exact: true }).click();
      const area = page.locator(".incident-map-workspace");
      await area.locator(".linked-order").filter({ hasText: "O002" }).click();
      await expect(area.locator(".map-node-inspector")).toContainText("O002");
      await expect(area).toContainText("真实地理数据尚未接入");
      await page.screenshot({
        path: "screenshots/contest1/dispatcher-" + size.width + ".png",
        fullPage: size.width < 1024,
      });
      await page.getByRole("button", { name: "执行影响分析" }).click();
      await expect(page.getByText("当前有效分析")).toBeVisible();
      await page.getByRole("button", { name: "生成恢复候选" }).click();
      if (size.width <= 680)
        await page.getByRole("button", { name: "展开导航" }).click();
      await page.getByRole("link", { name: "恢复方案", exact: true }).click();
      await page.locator(".candidate-tabs button").nth(1).click();
      await expect(page.getByText("1 个站点保留原安排")).toBeVisible();
      await page
        .locator(".plan-impact-table .order-select-button")
        .filter({ hasText: "O001" })
        .click();
      await expect(
        page.locator(".plan-map-column .map-node-inspector"),
      ).toContainText("O001");
      await expect(page.locator(".eta-boundary")).toContainText(
        "不将异常后 ETA 当作恢复后 ETA",
      );
      await page.screenshot({
        path: "screenshots/contest1/plans-" + size.width + ".png",
        fullPage: true,
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
    },
  );
}
test("1600px 司机端保留真正桌面双栏", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await login(page);
  expect(
    (await page.locator(".driver-main").boundingBox())!.width,
  ).toBeGreaterThan(1450);
  await expect(page.locator(".driver-bottom-nav")).toBeHidden();
  await page.screenshot({ path: "screenshots/contest1/driver-1600.png" });
});
test("地理适配器拒绝坐标系混用、旧快照、不匹配版本和缺失几何", async () => {
  const context = await mockRequest<Context>(
    "/delivery-context?vehicle_id=V01",
  );
  const coords: [number, number][] = [
    [120, 30],
    [120.01, 30.01],
    [120.02, 30.02],
    [120.03, 30.03],
  ];
  // Explicit synthetic test coordinates; never supplied to the product at runtime.
  const data: GeographicRouteData = {
    coordinateSystem: "WGS84",
    source: "TEST_FIXTURE",
    version: "test-v1",
    observedAt: new Date().toISOString(),
    snapshotId: context.snapshot_id,
    snapshotVersion: context.snapshot_version,
    nodes: [
      ...context.route_stops.map((s, i) => ({
        nodeId: s.node_id,
        coordinates: coords[i]!,
      })),
      { nodeId: context.warehouse_node_id!, coordinates: [119.99, 29.99] },
    ],
    routes: [
      { vehicleId: "V01", planId: null, routeRevision: 1, coordinates: coords },
    ],
  };
  const config = {
    styleUrl: "/test-style.json",
    sourceName: "TEST_ONLY",
    attribution: "test fixtures",
  };
  expect(
    geographyIssue(
      undefined,
      undefined,
      context,
      context.route_stops,
      "V01",
      null,
    ),
  ).toContain("未提供");
  expect(
    geographyIssue(data, config, context, context.route_stops, "V01", null),
  ).toBe("");
  expect(
    geographyIssue(
      { ...data, coordinateSystem: "GCJ02" },
      config,
      context,
      context.route_stops,
      "V01",
      null,
    ),
  ).toContain("WGS84");
  expect(
    geographyIssue(
      { ...data, snapshotId: "OLD" },
      config,
      context,
      context.route_stops,
      "V01",
      null,
    ),
  ).toContain("快照不一致");
  expect(
    geographyIssue(
      { ...data, routes: [{ ...data.routes[0]!, routeRevision: 2 }] },
      config,
      context,
      context.route_stops,
      "V01",
      null,
    ),
  ).toContain("路线版本");
  expect(
    geographyIssue(
      { ...data, nodes: [] },
      config,
      context,
      context.route_stops,
      "V01",
      null,
    ),
  ).toContain("坐标不完整");
  expect(
    geographyIssue(
      data,
      config,
      context,
      context.route_stops,
      "V01",
      "UNRELATED_PLAN",
    ),
  ).toContain("缺少");
});
