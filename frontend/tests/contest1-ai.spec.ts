import { test, expect } from "./test";
import type { Page } from "@playwright/test";
import { mockRequest, resetFixtures } from "../src/mock/transport";
import { chatBusinessLabel, continuationLabel } from "../src/lib/chat-state";
import type { Schema } from "../src/api/types";
import { mkdirSync } from "node:fs";
mkdirSync("screenshots/contest1", { recursive: true });
test.beforeEach(() => resetFixtures());
type Scenario =
  | "normal"
  | "running"
  | "failed"
  | "unknown"
  | "no-plan"
  | "busy"
  | "needs-action"
  | "denied"
  | "no-orders"
  | "post-delay";
async function setup(
  page: Page,
  scenario: Scenario = "normal",
  role: "DRIVER" | "DISPATCHER" = "DRIVER",
) {
  const posts: Record<string, any>[] = [];
  const polls: string[] = [];
  let turn = 0;
  const envelope = (data: unknown, error: unknown = null) => ({
    code: error ? "ERROR" : "OK",
    message: "OK",
    data,
    error,
    request_id: "contest1_test",
    operation_id: null,
    meta: {
      contract_version: "1.0",
      data_time: new Date().toISOString(),
      replayed: false,
    },
  });
  const result = (overrides: Partial<Schema["RunResult"]> = {}) => ({
    reply: "请问预计延误多少分钟？",
    business_state: "NEEDS_INFO",
    incident_id: null,
    job_ids: [],
    plan_ids: [],
    next_action: "ASK_USER",
    evidence_refs: [],
    ...overrides,
  });
  await page.route("**/api/v1/**", async (r) => {
    const req = r.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", ""),
      body = req.postDataJSON();
    if (path.startsWith("/map/"))
      return r.fulfill({
        status: 503,
        json: envelope(null, {
          code: "MAP_UNAVAILABLE",
          reason: "业务回归夹具未启用地图",
          next_action: "NONE",
          missing_fields: [],
          retryable: false,
          details: null,
        }),
      });
    let data: unknown;
    if (path === "/auth/login")
      data = {
        access_token: "TEST_TOKEN_NOT_REAL",
        token_type: "Bearer",
        expires_in: 3600,
        user: {
          user_id: "CONTEST1_TEST",
          username: role === "DRIVER" ? "driver01" : "dispatcher",
          role,
          vehicle_id: role === "DRIVER" ? "V01" : null,
        },
      };
    else if (path === "/ready")
      data = { status: "READY", agent_mode: "direct_ai", source: "SIMULATION" };
    else if (path === "/chat") {
      posts.push(body);
      if (scenario === "post-delay")
        await new Promise((r) => setTimeout(r, 1200));
      turn++;
      if (scenario === "denied")
        return r.fulfill({
          status: 404,
          json: envelope(null, {
            code: "NOT_FOUND",
            reason: "该任务不存在或无权访问",
            missing_fields: [],
            retryable: false,
            next_action: "REFRESH_CONTEXT",
            details: null,
          }),
        });
      if (scenario === "busy")
        return r.fulfill({
          status: 409,
          json: envelope(null, {
            code: "RESOURCE_BUSY",
            reason: "本会话仍有作业运行，请核对原任务",
            missing_fields: [],
            retryable: false,
            next_action: "POLL",
            details: null,
          }),
        });
      data = {
        conversation_id: body.conversation_id || "CONV_" + turn,
        turn_id: "TURN_" + turn,
        job_id: "CHAT_" + turn,
        job_type: "CHAT",
        job_status: "QUEUED",
        poll_after_seconds: 2,
      };
    } else if (path.startsWith("/jobs/")) {
      const jobId = path.split("/").pop()!;
      polls.push(jobId);
      if (scenario === "running")
        data = {
          job_id: jobId,
          job_type: "CHAT",
          job_status: "RUNNING",
          result_kind: "CHAT",
          result: null,
          error: null,
          poll_after_seconds: 5,
        };
      else if (scenario === "failed" || scenario === "unknown")
        data = {
          job_id: jobId,
          job_type: "CHAT",
          job_status: scenario === "failed" ? "FAILED" : "UNKNOWN",
          result_kind: "CHAT",
          result: null,
          error:
            scenario === "failed"
              ? {
                  code: "MODEL_UNAVAILABLE",
                  reason: "模型服务超时，请稍后核实",
                }
              : null,
          poll_after_seconds: 5,
        };
      else if (jobId === "COMPUTE_1") {
        if (scenario === "no-plan")
          data = {
            job_id: jobId,
            job_type: "PLAN_GENERATION",
            job_status: "SUCCEEDED",
            result_kind: "PLAN_GENERATION",
            result: {
              solution_status: "NO_FEASIBLE_PLAN_IN_SEARCH",
              search_scope: {
                vehicle_ids: ["V01"],
                order_nos: ["O001"],
                budget_ms: 500,
                method: "BOUNDED_SAME_VEHICLE_RESEQUENCE",
                max_permutations: 6,
              },
              plans: [],
              infeasible_reasons: [
                {
                  code: "NO_FEASIBLE",
                  message: "模拟约束下无可行候选，请人工协调",
                  entity_ids: ["O001"],
                },
              ],
              computed_at: new Date().toISOString(),
              plan_group_id: "GROUP_TEST",
              analysis_id: "ANA_TEST",
            },
            error: null,
            poll_after_seconds: 5,
          };
        else
          data = {
            job_id: jobId,
            job_type: "IMPACT_ANALYSIS",
            job_status: "SUCCEEDED",
            result_kind: "IMPACT_ANALYSIS",
            result: {
              analysis_id: "ANA_TEST",
              incident_id: "INC_DEMO_01",
              incident_version: 1,
              snapshot_id: "demo_snapshot",
              analysis_status: "VALID",
              risk_order_count: 1,
              order_impacts: [],
              missing_fields: [],
            },
            error: null,
            poll_after_seconds: 5,
          };
      } else {
        const number = Number(jobId.split("_")[1]);
        const value =
          scenario === "needs-action"
            ? result({
                reply: "请先核实收货时间窗。",
                next_action: "VERIFY_WINDOW",
              })
            : number === 1
              ? result()
              : number === 2
                ? result({
                    reply: "异常已登记，影响计算处理中。",
                    business_state: "PROCESSING",
                    incident_id: "INC_DEMO_01",
                    job_ids: ["COMPUTE_1", "COMPUTE_1"],
                    next_action: "POLL",
                    evidence_refs: ["INC_DEMO_01"],
                  })
                : result({
                    reply: "方案正在等待调度员批准。",
                    business_state: "WAITING_APPROVAL",
                    incident_id: "INC_DEMO_01",
                    next_action: "WAIT_DISPATCHER",
                  });
        data = {
          job_id: jobId,
          job_type: "CHAT",
          job_status: "SUCCEEDED",
          result_kind: "CHAT",
          result: value,
          error: null,
          poll_after_seconds: 5,
        };
      }
    } else {
      data = await mockRequest(path + url.search, body ?? undefined);
      if (scenario === "no-orders" && path === "/delivery-context")
        data = { ...(data as object), orders: [], route_stops: [] };
    }
    await r.fulfill({
      status: path === "/chat" ? 202 : 200,
      json: envelope(data),
    });
  });
  await page.goto("/");
  await page
    .getByLabel("工作账号")
    .fill(role === "DRIVER" ? "driver01" : "dispatcher");
  await page.getByLabel("登录密码").fill("TEST_PASSWORD_ONLY");
  await page.getByRole("button", { name: "登录工作空间", exact: true }).click();
  await expect(page).toHaveURL(role === "DRIVER" ? /driver$/ : /overview$/);
  await page.getByRole("button", { name: "打开智能助手" }).click();
  return { posts, polls };
}
async function send(page: Page, value: string) {
  await page.getByLabel("发送给智能助手").fill(value);
  await page.getByRole("button", { name: "发送消息" }).click();
}
test("赛题一同一 conversation_id 多轮补问、子任务去重、主动继续与业务状态分离", async ({
  page,
}) => {
  const state = await setup(page);
  await send(page, "堵车了。");
  await expect(page.locator(".chat-message.assistant")).toContainText(
    "预计延误多少分钟",
  );
  expect(state.posts[0].incident_id).toBeUndefined();
  await send(page, "预计20分钟。");
  await expect(page.locator(".chat-business-result")).toContainText(
    "关联计算已返回",
  );
  expect(state.posts[1].conversation_id).toBe("CONV_1");
  expect(state.posts).toHaveLength(2);
  expect(state.polls.filter((id) => id === "COMPUTE_1")).toHaveLength(1);
  await expect(page.locator(".chat-confirmed-facts")).toContainText(
    "INC_DEMO_01",
  );
  await expect(
    page.getByRole("button", { name: "计算已返回，继续处理" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "计算已返回，继续处理" }).click();
  await expect(page.locator(".chat-business-result")).toContainText(
    "等待调度员批准",
  );
  expect(state.posts[2].conversation_id).toBe("CONV_1");
  expect(state.posts[2].message).toBe("继续");
  await page.locator(".ai-runtime-status summary").click();
  await expect(page.locator(".ai-runtime-status")).toContainText("真实模型");
  await expect(page.locator(".ai-runtime-status")).toContainText("未验收");
  await page.screenshot({
    path: "screenshots/contest1/driver-ai-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "screenshots/contest1/driver-ai-mobile.png",
    fullPage: true,
  });
});
test("切换订单清除旧草稿与会话，不带旧事件或 conversation_id", async ({
  page,
}) => {
  const state = await setup(page);
  await send(page, "堵车了。");
  await expect(page.locator(".chat-message.assistant")).toBeVisible({ timeout: 15000 });
  await page.getByLabel("关联订单").selectOption("O002");
  await send(page, "另一单也堵车了");
  await expect(page.locator(".chat-message.assistant")).toBeVisible({ timeout: 15000 });
  expect(state.posts[1].conversation_id).toBeUndefined();
  expect(state.posts[1].incident_id).toBeUndefined();
  expect(state.posts[1].selected_order_no).toBe("O002");
});
test("关闭助手取消未完成轮询，并保留任务引用用于恢复", async ({ page }) => {
  const state = await setup(page, "running");
  await send(page, "堵车了");
  await expect(page.locator(".job-progress")).toContainText("后台处理中");
  await page.getByRole("button", { name: "关闭助手" }).click();
  const before = state.polls.length;
  await page.waitForTimeout(5500);
  expect(state.polls).toHaveLength(before);
  await expect(
    page.getByRole("button", { name: "恢复查询", exact: true }),
  ).toBeVisible();
});
test("离开调度页面取消轮询，不自动发继续", async ({ page }) => {
  const state = await setup(page, "running", "DISPATCHER");
  await send(page, "堵车了");
  await expect(page.locator(".job-progress")).toContainText("后台处理中");
  await page.getByRole("link", { name: "异常工作台", exact: true }).click();
  const before = state.polls.length;
  await page.waitForTimeout(5500);
  expect(state.polls).toHaveLength(before);
  expect(state.posts).toHaveLength(1);
});
for (const scenario of ["failed", "unknown"] as const)
  test(`CHAT ${scenario} 不作为业务成功`, async ({ page }) => {
    await setup(page, scenario);
    await send(page, "堵车了");
    await expect(page.locator(".assistant-error")).toContainText(
      scenario === "failed" ? "模型服务超时" : "状态未知",
    );
    await expect(page.locator(".chat-message.assistant")).toHaveCount(0);
    if (scenario === "unknown")
      await expect(
        page.getByRole("button", { name: "恢复本轮查询" }),
      ).toBeVisible();
  });
test("NEEDS_INFO 按 next_action 处理，不一律显示缺字段", async ({ page }) => {
  await setup(page, "needs-action");
  await send(page, "收货时间变化了");
  await expect(page.locator(".chat-business-result")).toContainText(
    "等待下一步操作",
  );
  await expect(page.locator(".chat-business-result")).toContainText(
    "核实收货时间窗",
  );
});
test("无可行候选显示计算原因，不冒充恢复成功", async ({ page }) => {
  await setup(page, "no-plan");
  await send(page, "堵车了");
  await expect(page.locator(".chat-message.assistant")).toBeVisible();
  await send(page, "预计20分钟");
  await expect(page.locator(".chat-calculation-outcome")).toContainText(
    "无可行候选",
  );
  await expect(page.locator(".chat-calculation-outcome")).toContainText(
    "请人工协调",
  );
  await expect(page.locator(".chat-business-result")).toContainText(
    "业务处理中",
  );
});
test("会话 RESOURCE_BUSY 不换键自动重发", async ({ page }) => {
  const state = await setup(page, "busy");
  await send(page, "堵车了");
  await expect(page.locator(".assistant-error")).toContainText("RESOURCE_BUSY");
  expect(state.posts).toHaveLength(1);
  await expect(page.locator(".chat-message.assistant")).toHaveCount(0);
});

test("司机无任务时不伪造下一站或已完成状态", async ({ page }) => {
  await setup(page, "no-orders");
  await page.getByRole("button", { name: "关闭助手" }).click();
  await expect(page.getByText("当前暂无配送任务")).toBeVisible();
  await expect(page.locator(".driver-timeline .route-stop")).toHaveCount(0);
});
test("后端拒绝无权限任务时不展示业务事实或成功回复", async ({ page }) => {
  await setup(page, "denied");
  await send(page, "堵车了");
  await expect(page.locator(".assistant-error")).toContainText("无权访问");
  await expect(page.locator(".chat-message.assistant")).toHaveCount(0);
  await expect(page.locator(".chat-confirmed-facts")).toHaveCount(0);
});

test("聊天受理响应晚于页面切换时只保留引用，不启动旧页面轮询", async ({
  page,
}) => {
  const state = await setup(page, "post-delay", "DISPATCHER");
  await send(page, "堵车了");
  await expect.poll(() => state.posts.length).toBe(1);
  await page.getByRole("link", { name: "异常工作台", exact: true }).click();
  await expect(page.locator(".recovery-banner")).toContainText("CHAT_1");
  await page.waitForTimeout(2400);
  expect(state.polls).toHaveLength(0);
  await expect(
    page.getByRole("button", { name: "恢复查询", exact: true }),
  ).toBeVisible();
});
test("业务状态文案区分待确认、生效与完成", () => {
  const r = (
    business_state: Schema["RunResult"]["business_state"],
    next_action: Schema["RunResult"]["next_action"] = "NONE",
  ): Schema["RunResult"] => ({
    reply: "服务端回复",
    business_state,
    next_action,
    incident_id: null,
    job_ids: [],
    plan_ids: [],
    evidence_refs: [],
  });
  expect(chatBusinessLabel(r("WAITING_ACK", "WAIT_DRIVER"))).toBe(
    "等待司机确认",
  );
  expect(chatBusinessLabel(r("ACTIVE"))).toBe("新计划已生效");
  expect(chatBusinessLabel(r("DONE"))).toBe("本次处置已完成");
  expect(continuationLabel(r("NEEDS_INFO", "ASK_USER"), false)).toBe("");
  expect(continuationLabel(r("NEEDS_INFO", "GENERATE_PLANS"), false)).toContain(
    "生成",
  );
  expect(continuationLabel(r("PROCESSING", "POLL"), true)).toBe("");
});
