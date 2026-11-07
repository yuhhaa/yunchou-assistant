import { test, expect } from "./test";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { createAmapMiddleware, amapTarget } from "../server/amapProxy";
import { adaptMapData } from "../src/services/maps/mapDataAdapter";
import { mockRequest, resetFixtures } from "../src/mock/transport";
import type { Context } from "../src/api/types";
import type { GeographicRouteData } from "../src/components/maps/types";

test("官方 Loader 单次加载，地图更新不重建，选择/候选/销毁正常", async ({
  page,
}) => {
  let scripts = 0;
  await page.route("**/_AMapService/_status", (r) =>
    r.fulfill({ json: { configured: true } }),
  );
  await page.route("https://webapi.amap.com/maps?**", (r) => {
    scripts++;
    return r.fulfill({
      contentType: "application/javascript",
      body: readFileSync("tests/fixtures/amap-sdk-stub.js", "utf8"),
    });
  });
  await page.goto("/tests/fixtures/map-harness.html");
  const map = page.locator(".map-view");
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-sdk-ready",
    "true",
  );
  expect(scripts).toBe(1);
  const fits = await page.evaluate(() => (window as any).__amapTest.fit);
  await page.getByRole("button", { name: "刷新相同路线" }).click();
  await page.waitForTimeout(60);
  expect(await page.evaluate(() => (window as any).__amapTest.fit)).toBe(fits);
  await page
    .getByRole("button", { name: "选择订单 O002", exact: true })
    .click();
  await expect(map.locator(".map-node-inspector")).toContainText("O002");
  await expect(map.locator(".amap-delivery-info")).toContainText("O002");
  await expect
    .poll(() => page.evaluate(() => (window as any).__amapTest.focus))
    .toBeGreaterThan(0);
  await page.getByRole("button", { name: /选择配送点 O003/ }).click();
  await expect(map.locator(".map-node-inspector")).toContainText("O003");
  await page.getByRole("button", { name: "切换候选" }).click();
  await expect(map.locator(".map-canvas")).toHaveAttribute(
    "data-plan",
    "TEST_PLAN",
  );
  await expect(map.locator(".map-plan-badge")).toContainText("方案预览");
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as any).__amapTest.lines.some(
          (l: any) => l.strokeColor === "#d86a21",
        ),
      ),
    )
    .toBeTruthy();
  expect(await page.evaluate(() => (window as any).__amapTest.created)).toBe(1);
  await page.getByRole("button", { name: "并发加载 SDK" }).click();
  expect(scripts).toBe(1);
  await page.getByRole("button", { name: "放大地图", exact: true }).click();
  expect(await page.evaluate(() => (window as any).__amapTest.zoom)).toBe(1);
  await map.getByRole("button", { name: "展开配送地图" }).click();
  await expect(
    page.getByRole("dialog", { name: "地图故障测试" }),
  ).toBeVisible();
  expect(await page.evaluate(() => (window as any).__amapTest.created)).toBe(1);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "挂载或卸载地图" }).click();
  expect(await page.evaluate(() => (window as any).__amapTest.destroyed)).toBe(
    1,
  );
  await page.getByRole("button", { name: "挂载或卸载地图" }).click();
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-sdk-ready",
    "true",
  );
  expect(scripts).toBe(1);
  const security = await page.evaluate(
    () => (window as any)._AMapSecurityConfig,
  );
  expect(Object.keys(security)).toEqual(["serviceHost"]);
  expect(security.serviceHost).toContain("/_AMapService");
});

test("安全代理未就绪不请求 SDK，服务失败可重试且列表可用", async ({ page }) => {
  let configured = false,
    scripts = 0;
  await page.route("**/_AMapService/_status", (r) =>
    r.fulfill({ json: { configured } }),
  );
  await page.route("https://webapi.amap.com/maps?**", (r) => {
    scripts++;
    return r.abort("failed");
  });
  await page.goto("/tests/fixtures/map-harness.html");
  await expect(page.locator(".amap-load-error")).toContainText(
    "地图鉴权未配置",
  );
  expect(scripts).toBe(0);
  configured = true;
  await page.getByRole("button", { name: "重新加载地图" }).click();
  await expect(page.locator(".amap-load-error")).toContainText(
    "高德地图加载失败",
  );
  expect(scripts).toBe(1);
  await page.getByRole("button", { name: "列表执行反馈 0" }).click();
  await expect(
    page.getByRole("button", { name: "列表执行反馈 1" }),
  ).toBeVisible();
  await expect(page.locator(".route-network-svg")).toHaveCount(0);
});

test("地理适配器校验 GCJ-02、快照、序列与道路来源，不连虚构直线", async () => {
  resetFixtures();
  const context = await mockRequest<Context>(
    "/delivery-context?vehicle_id=V01",
  );
  const data: GeographicRouteData = {
    coordinateSystem: "GCJ02",
    dataKind: "BUSINESS",
    source: "TEST",
    version: "1",
    observedAt: new Date().toISOString(),
    snapshotId: context.snapshot_id,
    snapshotVersion: context.snapshot_version,
    nodes: context.route_stops.map((s, i) => ({
      nodeId: s.node_id,
      coordinates: [120 + i * 0.01, 30 + i * 0.01],
    })),
    routes: [],
  };
  const input = {
    data,
    context,
    stops: context.route_stops,
    vehicleId: "V01",
    planId: null,
    demoAllowed: false,
  };
  expect(
    adaptMapData(input).markers.filter((p) => p.kind === "stop"),
  ).toHaveLength(4);
  expect(adaptMapData(input).layers).toHaveLength(0);
  expect(
    adaptMapData({ ...input, data: { ...data, coordinateSystem: "WGS84" } })
      .markers,
  ).toHaveLength(0);
  expect(
    adaptMapData({ ...input, data: { ...data, snapshotVersion: 999 } }).markers,
  ).toHaveLength(0);
  expect(
    adaptMapData({ ...input, data: { ...data, dataKind: "DEMO" } }).markers,
  ).toHaveLength(0);
  const route = {
    vehicleId: "V01",
    planId: null,
    planVersion: null,
    routeRevision: 1,
    geometryKind: "ROAD" as const,
    stopIds: context.route_stops.map((s) => s.stop_id),
    coordinates: [
      [120, 30],
      [120.01, 30.01],
    ] as [number, number][],
  };
  expect(
    adaptMapData({ ...input, data: { ...data, routes: [route] } }).layers,
  ).toHaveLength(1);
  expect(
    adaptMapData({
      ...input,
      data: { ...data, routes: [{ ...route, stopIds: [] }] },
    }).layers,
  ).toHaveLength(0);
  expect(
    adaptMapData({
      ...input,
      data: { ...data, routes: [{ ...route, routeRevision: 99 }] },
    }).layers,
  ).toHaveLength(0);
});

test("本地安全代理只允许固定高德路径，拒绝非法路径/方法，缺配置不泄露凭据", async () => {
  const savedCode = process.env.AMAP_SECURITY_JSCODE,
    savedTarget = process.env.AMAP_PROXY_TARGET;
  delete process.env.AMAP_SECURITY_JSCODE;
  delete process.env.AMAP_PROXY_TARGET;
  const server = createServer((req, res) =>
    createAmapMiddleware()(req, res, () => {
      res.statusCode = 404;
      res.end();
    }),
  );
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number },
    origin = "http://127.0.0.1:" + address.port;
  try {
    const status = await (await fetch(origin + "/_AMapService/_status")).json();
    expect(status.configured).toBe(false);
    expect(
      (await fetch(origin + "/_AMapService/v3/direction/driving")).status,
    ).toBe(503);
    expect(
      (
        await fetch(origin + "/_AMapService/v3/direction/driving", {
          method: "POST",
        })
      ).status,
    ).toBe(405);
    expect(
      (
        await fetch(origin + "/_AMapService/_status", {
          headers: { Referer: "https://foreign.example/" },
        })
      ).status,
    ).toBe(403);
    expect(
      amapTarget("/_AMapService/https://attacker.example/", "TEST"),
    ).toBeNull();
    expect(amapTarget("/_AMapService/v3/../../path", "TEST")).toBeNull();
    expect(
      amapTarget("/_AMapService/v3/direction/driving?jscode=CLIENT", "TEST"),
    ).toBeNull();
    const url = amapTarget("/_AMapService/v4/map/styles?key=PUBLIC", "TEST");
    expect(url?.hostname).toBe("webapi.amap.com");
    expect(url?.searchParams.get("jscode")).toBe("TEST");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    if (savedCode === undefined) delete process.env.AMAP_SECURITY_JSCODE;
    else process.env.AMAP_SECURITY_JSCODE = savedCode;
    if (savedTarget === undefined) delete process.env.AMAP_PROXY_TARGET;
    else process.env.AMAP_PROXY_TARGET = savedTarget;
  }
});
