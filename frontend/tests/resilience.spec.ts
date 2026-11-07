import { test, expect, type Page, type Route } from "@playwright/test";
import { mockRequest, resetFixtures } from "../src/mock/transport";
test.beforeEach(() => resetFixtures());
type Options = {
  expired?: boolean;
  conflict?: boolean;
  failJob?: boolean;
  disconnect?: boolean;
  unauthorized?: boolean;
  expiresSoon?: boolean;
};
async function setup(page: Page, options: Options = {}) {
  const attempts: { key: string; body: string }[] = [];
  let queue = false,
    refused = false;
  const envelope = (data: unknown, error: unknown = null) => ({
    code: error ? "ERROR" : "OK",
    message: "OK",
    data,
    error,
    request_id: "test_resilience",
    operation_id: null,
    meta: {
      contract_version: "1.0",
      data_time: new Date().toISOString(),
      replayed: false,
    },
  });
  const fail = (r: Route, status: number, code: string, reason: string) =>
    r.fulfill({
      status,
      json: envelope(null, {
        code,
        reason,
        missing_fields: [],
        retryable: false,
        next_action: status === 401 ? "LOGIN" : "REFRESH_CONTEXT",
        details: null,
      }),
    });
  await page.route("**/api/v1/**", async (r) => {
    const req = r.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", ""),
      body = req.postDataJSON();
    if (path.startsWith("/map/"))
      return fail(r, 503, "MAP_UNAVAILABLE", "业务回归夹具未启用地图");
    if (path === "/auth/login")
      return r.fulfill({
        json: envelope({
          access_token: "TEST_ONLY_TOKEN",
          token_type: "Bearer",
          expires_in: 3600,
          user: {
            user_id: "TEST_RESILIENCE",
            username: "dispatcher",
            role: "DISPATCHER",
            vehicle_id: null,
          },
        }),
      });
    if (options.unauthorized && path === "/delivery-context")
      return fail(r, 401, "UNAUTHORIZED", "登录已失效");
    if (path === "/impact-analyses" && options.disconnect) {
      attempts.push({
        key: req.headers()["idempotency-key"],
        body: req.postData()!,
      });
      if (!refused) {
        refused = true;
        return r.abort("connectionreset");
      }
    }
    if (options.conflict && path.endsWith("/approve"))
      return fail(r, 409, "STATE_CONFLICT", "方案已被其他调度员处理，请刷新");
    let data: any = await mockRequest(path + url.search, body ?? undefined);
    if (options.expiresSoon && path.endsWith("/workspace"))
      data.plans = data.plans.map((p: any) => ({
        ...p,
        expires_at: new Date(Date.now() + 1000).toISOString(),
      }));
    if (options.expired) {
      if (path.endsWith("/workspace"))
        data.plans = data.plans.map((p: any) => ({
          ...p,
          expires_at: "2026-10-26T09:00:00+08:00",
        }));
      if (path.startsWith("/plans/"))
        data = { ...data, expires_at: "2026-10-26T09:00:00+08:00" };
    }
    if (options.failJob && path.startsWith("/jobs/"))
      data = {
        ...data,
        job_status: "FAILED",
        result: null,
        error: { code: "COMPUTE_FAILED", reason: "约束计算失败，需要人工核查" },
      };
    await r.fulfill({
      status: ["/impact-analyses", "/recovery-plans", "/chat"].includes(path)
        ? 202
        : 200,
      json: envelope(data),
    });
  });
  await page.goto("/");
  await page.getByLabel("工作账号").fill("dispatcher");
  await page.getByLabel("登录密码").fill("TEST_PASSWORD_ONLY");
  await page.getByRole("button", { name: "登录工作空间", exact: true }).click();
  return { attempts };
}
async function analyze(page: Page) {
  await page.getByRole("link", { name: "异常工作台", exact: true }).click();
  await page.getByRole("button", { name: "执行影响分析" }).click();
}
async function generate(page: Page) {
  await analyze(page);
  await expect(page.getByText("当前有效分析")).toBeVisible();
  await page.getByRole("button", { name: "生成恢复候选" }).click();
  await expect(page.getByRole("link", { name: /比较.*候选/ })).toBeVisible();
  await page.getByRole("link", { name: "恢复方案", exact: true }).click();
}
test("过期方案展示原因与重新分析入口，不允许批准", async ({ page }) => {
  await setup(page, { expired: true });
  await generate(page);
  await expect(page.getByText("当前方案已失效，不能继续审批。")).toBeVisible();
  await expect(page.getByRole("button", { name: "批准此方案" })).toBeDisabled();
  await expect(
    page.getByRole("link", { name: "重新分析", exact: true }),
  ).toBeVisible();
});
test("409 业务版本冲突显示服务原因，不伪造成功", async ({ page }) => {
  await setup(page, { conflict: true });
  await generate(page);
  await page.getByRole("button", { name: "批准此方案" }).click();
  await page.getByRole("button", { name: "确认批准" }).click();
  await expect(page.getByRole("alert")).toContainText("STATE_CONFLICT");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator(".plan-status-bar")).toContainText("待审批");
});
test("HTTP 200 的 JOB FAILED 仍显示任务失败", async ({ page }) => {
  await setup(page, { failJob: true });
  await analyze(page);
  await expect(page.getByRole("alert")).toContainText("约束计算失败");
  await expect(page.getByText("当前有效分析")).not.toBeVisible();
});
test("网络中断后保留同一幂等键与请求体，恢复重试不创建新动作", async ({
  page,
}) => {
  const state = await setup(page, { disconnect: true });
  await analyze(page);
  await expect(page.getByRole("alert")).toContainText("无法连接业务服务");
  await expect(page.getByRole("button", { name: "按原键重试" })).toBeVisible();
  await page.getByRole("button", { name: "按原键重试" }).click();
  await expect(page.getByText("当前有效分析")).toBeVisible();
  expect(state.attempts).toHaveLength(2);
  expect(state.attempts[0]).toEqual(state.attempts[1]);
  await expect(
    page.getByRole("button", { name: "按原键重试" }),
  ).not.toBeVisible();
});
test("401 清理内存令牌并返回登录，登录信息不存储到浏览器", async ({ page }) => {
  await setup(page, { unauthorized: true });
  await expect(page).toHaveURL(/login$/);
  await expect(
    page.getByRole("heading", { name: "进入工作空间" }),
  ).toBeVisible();
  const storage = await page.evaluate(() =>
    JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
    }),
  );
  expect(storage).not.toContain("TEST_ONLY_TOKEN");
  expect(storage).not.toContain("TEST_PASSWORD_ONLY");
});

test("仿真业务时钟固定时，候选仍按真实时间到期", async ({ page }) => {
  await page.clock.install();
  await setup(page, { expiresSoon: true });
  await generate(page);
  await expect(page.getByRole("button", { name: "批准此方案" })).toBeEnabled();
  await page.clock.fastForward(2500);
  await expect(page.getByText("当前方案已失效，不能继续审批。")).toBeVisible();
  await expect(page.getByRole("button", { name: "批准此方案" })).toBeDisabled();
});
