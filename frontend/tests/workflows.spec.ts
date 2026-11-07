import { test, expect } from "./test";
import type { Page } from "@playwright/test";
import { mockRequest, resetFixtures } from "../src/mock/transport";
test.beforeEach(() => resetFixtures());
import { mkdirSync } from "node:fs";
mkdirSync("screenshots", { recursive: true });
async function demo(page: Page, role = "调度员演示") {
  await page.goto("/");
  await page.getByRole("button", { name: "体验独立演示空间" }).click();
  await page.getByRole("button", { name: role }).click();
}
async function candidates(page: Page) {
  await page.getByRole("link", { name: "异常工作台", exact: true }).click();
  await page.getByRole("button", { name: "执行影响分析" }).click();
  await expect(page.getByText("当前有效分析")).toBeVisible();
  await page.getByRole("button", { name: "生成恢复候选" }).click();
  await expect(page.getByRole("link", { name: /比较.*个候选/ })).toBeVisible();
  await page.getByRole("link", { name: "恢复方案", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "最小扰动恢复" }),
  ).toBeVisible();
}
test("调度演示：总览、分析、比较、审批后必须等待司机回执", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await demo(page);
  await expect(page.getByText("DEMO · 演示数据")).toBeVisible();
  await expect(page.getByRole("heading", { name: "当前配送" })).toBeVisible();
  await page.screenshot({
    path: "screenshots/01-overview.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "配送任务与地图", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "配送任务与地图" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭节点网络" }).click();
  await candidates(page);
  await expect(page.getByText("2 个站点保留原安排")).toBeVisible();
  await expect(
    page.getByText("指标来自独立样例，不代表实际优化效果。"),
  ).toBeVisible();
  await page.screenshot({
    path: "screenshots/03-plan-comparison.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "批准此方案" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "批准后进入等待司机确认状态",
  );
  await page.getByRole("button", { name: "确认批准" }).click();
  await expect(page.locator(".plan-status-bar")).toContainText(
    "已批准 · 待司机接收",
  );
  await expect(page.getByRole("button", { name: "批准此方案" })).toBeDisabled();
  expect(errors).toEqual([]);
  await page.getByRole("link", { name: "异常工作台", exact: true }).click();
  await page.screenshot({
    path: "screenshots/02-incident-workspace.png",
    fullPage: true,
  });
  // Switch only presentation role; fixture business state persists in this browser session.
  await page.getByTitle("退出登录").click();
  await page.getByRole("button", { name: "体验独立演示空间" }).click();
  await page.getByRole("button", { name: "司机演示" }).click();
  await expect(
    page.getByRole("heading", { name: "有新的任务调整" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "接受调整" }).click();
  await page.getByRole("button", { name: "确认接受" }).click();
  await expect(
    page.getByRole("heading", { name: "有新的任务调整" }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "今日配送路线" }),
  ).toBeVisible();
});
test("司机移动端：安全提示、异常上报、到达与完成分离、角色路由保护", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await demo(page, "司机演示");
  await expect(
    page.getByText("请在安全停车后查看任务、上报异常或确认方案。"),
  ).toBeVisible();
  await page.screenshot({
    path: "screenshots/04-driver-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "上报配送异常", exact: true }).click();
  await page.getByLabel("预计延误（分钟）").fill("0");
  await page.getByLabel("现场描述").fill("停车等待，当前延误为零。");
  await page.getByRole("button", { name: "提交异常", exact: true }).click();
  await expect(page.locator(".feedback.notice")).toContainText("异常已登记");
  await page.locator(".route-stop").first().click();
  await expect(
    page.getByRole("button", { name: "确认配送完成" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "确认到达", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认配送完成" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "确认配送完成" }).click();
  await expect(page.locator(".stop-execution")).toContainText("已完成");
  await page.goto("/#/plans");
  await expect(page).toHaveURL(/\/driver$/);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
test("移动调度与登录布局无横向溢出，助手可展开收起", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await demo(page);
  await page.screenshot({
    path: "screenshots/05-overview-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "展开导航" }).click();
  await page.getByRole("link", { name: "异常工作台", exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "打开智能助手" }).click();
  await expect(
    page.getByRole("complementary", { name: "事件协同" }),
  ).toBeVisible();
  await page.getByLabel("发送给智能助手").fill("前面堵车了，估计晚20分钟");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".chat-message.assistant")).toContainText(
    "演示业务已登记异常",
  );
  await page.getByRole("button", { name: "关闭助手" }).click();
  await expect(
    page.getByRole("complementary", { name: "事件协同" }),
  ).not.toBeVisible();
});
test("真实模式登录失败不会切换为 Mock", async ({ page }) => {
  await page.route("**/api/v1/auth/login", (r) =>
    r.fulfill({
      status: 503,
      json: {
        code: "UNAVAILABLE",
        message: "测试服务不可用",
        data: null,
        error: {
          code: "UNAVAILABLE",
          reason: "测试服务不可用",
          missing_fields: [],
          retryable: true,
          next_action: "LOGIN",
          details: null,
        },
        request_id: "test_network",
        operation_id: null,
        meta: {
          contract_version: "1.0",
          data_time: new Date().toISOString(),
          replayed: false,
        },
      },
    }),
  );
  await page.goto("/");
  await page.getByLabel("工作账号").fill("dispatcher");
  await page.getByLabel("登录密码").fill("test-only-password");
  await page.getByRole("button", { name: "登录工作空间", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("测试服务不可用");
  await expect(page).toHaveURL(/login/);
  await expect(page.getByText("DEMO · 演示数据")).not.toBeVisible();
});
test("API 契约链路：Bearer、幂等键、202轮询、审批、重复提交防护", async ({
  page,
}) => {
  const writes: { path: string; headers: Record<string, string>; body: any }[] =
    [];
  let jobPolls = 0;
  await page.route("**/api/v1/**", async (r) => {
    const req = r.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    const body = req.postDataJSON();
    let data: unknown;
    let status = 200;
    if (path === "/auth/login")
      data = {
        access_token: "TEST_TOKEN_NOT_REAL",
        token_type: "Bearer",
        expires_in: 3600,
        user: {
          user_id: "TEST_DISPATCHER",
          username: "dispatcher",
          role: "DISPATCHER",
          vehicle_id: null,
        },
      };
    else {
      if (path === "/delivery-context")
        expect(url.searchParams.get("vehicle_id")).toBeTruthy();
      expect(req.headers().authorization).toBe("Bearer TEST_TOKEN_NOT_REAL");
      expect(req.headers()["x-contract-version"]).toBe("1.0");
      if (path.startsWith("/map/"))
        return r.fulfill({
          status: 503,
          json: {
            code: "MAP_UNAVAILABLE",
            message: "业务回归夹具未启用地图",
            data: null,
            error: null,
            request_id: "req_map_test",
            operation_id: null,
            meta: {
              contract_version: "1.0",
              data_time: new Date().toISOString(),
              replayed: false,
            },
          },
        });
      if (req.method() === "POST") {
        expect(req.headers()["idempotency-key"]).toMatch(/^[A-Za-z0-9_-]+$/);
        writes.push({ path, headers: req.headers(), body });
      }
      data = await mockRequest(path + url.search, body ?? undefined);
      if (["/impact-analyses", "/recovery-plans", "/chat"].includes(path))
        status = 202;
      if (path.startsWith("/jobs/")) {
        jobPolls++;
        if (jobPolls === 1)
          data = { ...(data as any), job_status: "RUNNING", result: null };
      }
    }
    await r.fulfill({
      status,
      json: {
        code: status === 202 ? "ACCEPTED" : "OK",
        message: "OK",
        data,
        error: null,
        request_id: "req_test",
        operation_id: null,
        meta: {
          contract_version: "1.0",
          data_time: new Date().toISOString(),
          replayed: false,
        },
      },
    });
  });
  await page.goto("/");
  await page.getByLabel("工作账号").fill("dispatcher");
  await page.getByLabel("登录密码").fill("TEST_ONLY");
  await page.getByRole("button", { name: "登录工作空间", exact: true }).click();
  await expect(page.locator(".environment")).toBeVisible();
  await candidates(page);
  expect(jobPolls).toBeGreaterThanOrEqual(3);
  await page.getByRole("button", { name: "批准此方案" }).click();
  await page.getByRole("button", { name: "确认批准" }).dblclick();
  await expect(page.locator(".plan-status-bar")).toContainText("待司机接收");
  const impact = writes.find((w) => w.path === "/impact-analyses")!;
  expect(Object.keys(impact.body).sort()).toEqual([
    "expected_incident_version",
    "incident_id",
    "snapshot_id",
  ]);
  const generation = writes.find((w) => w.path === "/recovery-plans")!;
  expect(generation.body.strategy_profile).toBe("MIN_CHANGE_V1");
  expect(writes.filter((w) => w.path.endsWith("/approve"))).toHaveLength(1);
  expect(new Set(writes.map((w) => w.headers["idempotency-key"])).size).toBe(
    writes.length,
  );
});

test("车辆范围切换只显示对应快照，车队规模保持独立", async ({ page }) => {
  await demo(page);
  await expect(page.locator(".fleet-major")).toContainText("04");
  await expect(page.locator(".fleet-pair")).toContainText("05");
  await page.getByLabel("选择配送车辆").selectOption("V02");
  await expect(page.locator(".fleet-major")).toContainText("02");
  await expect(page.locator(".fleet-head")).toContainText("V02");
  await page
    .getByRole("button", { name: "配送任务与地图", exact: true })
    .click();
  await expect(page.locator(".network-panel")).toContainText("南岸配送站");
  await expect(page.locator(".network-panel")).not.toContainText(
    "滨江生活中心",
  );
});
