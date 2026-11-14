import { test, expect } from "./test";
import type { Page, Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import {
  configureDemoScenario,
  resetFixtures,
  mockRequest,
} from "../src/mock/transport";
import { adaptWuhanMap } from "../src/services/maps/wuhanAdapter";
import { drawRouteLayers } from "../src/components/maps/routeLayers";
import {
  demoMapPath,
  getConversations,
  getConversationMessages,
} from "../src/api/extended";
import type { Context, Schema } from "../src/api/types";

test.beforeEach(() => {
  resetFixtures();
  configureDemoScenario("wuhan", "DISPATCHER");
});
const envelope = (data: unknown, error: unknown = null) => ({
  code: error ? "ERROR" : "OK",
  message: "OK",
  data,
  error,
  request_id: "UPGRADE_TEST",
  operation_id: null,
  meta: {
    contract_version: "1.0",
    data_time: new Date().toISOString(),
    replayed: false,
  },
});
const fail = (r: Route, status = 409) =>
  r.fulfill({
    status,
    json: envelope(null, {
      code: "PLAN_STALE",
      reason: "候选地图版本已过期",
      next_action: "REFRESH_CONTEXT",
      retryable: false,
      missing_fields: [],
      details: null,
    }),
  });
async function sdk(page: Page) {
  await page.route("**/_AMapService/_status", (r) =>
    r.fulfill({ json: { configured: true } }),
  );
  await page.route("https://webapi.amap.com/maps?**", (r) =>
    r.fulfill({
      contentType: "application/javascript",
      body: readFileSync("tests/fixtures/amap-sdk-stub.js", "utf8"),
    }),
  );
}
async function local(page: Page, role = "司机演示") {
  await sdk(page);
  await page.goto("/");
  await page.getByRole("button", { name: "武汉联调本地样例" }).click();
  await page.getByRole("button", { name: role, exact: true }).click();
}
async function generate(page: Page) {
  await page.goto("/#/incidents");
  await page.getByRole("button", { name: "执行影响分析", exact: true }).click();
  await expect(page.getByText("当前有效分析")).toBeVisible();
  await page.getByRole("button", { name: "生成恢复候选", exact: true }).click();
  await expect(page.getByRole("link", { name: /比较.*候选/ })).toBeVisible();
  await page.goto("/#/plans");
}
async function api(
  page: Page,
  role: "DRIVER" | "DISPATCHER" = "DRIVER",
  options: { conflict?: boolean; outage?: boolean; routing?: boolean } = {},
) {
  configureDemoScenario("wuhan", role);
  const maps: { query: string; authorization: string }[] = [],
    posts: { path: string; body: any; key?: string }[] = [];
  let contextReads = 0;
  await sdk(page);
  await page.route("**/api/v1/**", async (r) => {
    const req = r.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", ""),
      body = req.postDataJSON();
    if (req.method() === "POST")
      posts.push({ path, body, key: req.headers()["idempotency-key"] });
    if (path === "/auth/login")
      return r.fulfill({
        json: envelope({
          access_token: "JWT_TEST_ONLY",
          token_type: "Bearer",
          expires_in: 3600,
          user: {
            user_id: "NEW_TEST",
            username: "test",
            role,
            vehicle_id: role === "DRIVER" ? "V01" : null,
          },
        }),
      });
    if (path === "/map/demo") {
      maps.push({
        query: url.search,
        authorization: req.headers().authorization,
      });
      if (options.outage) return r.abort("connectionreset");
      if (options.conflict && url.searchParams.has("plan_id")) return fail(r);
    }
    if (path === "/map" && options.routing)
      return r.fulfill({
        json: envelope({
          configuration_status: "CONFIGURED",
          routing_ready: true,
          routing_version: "SERVER_ROUTING_V1",
        }),
      });
    if (path === "/map/routing/refresh")
      return r.fulfill({
        status: 202,
        json: envelope({
          job_id: "MAP_JOB",
          job_type: "MAP_ROUTING",
          job_status: "QUEUED",
          poll_after_seconds: 0,
        }),
      });
    if (path === "/jobs/MAP_JOB")
      return r.fulfill({
        json: envelope({
          job_id: "MAP_JOB",
          job_type: "MAP_ROUTING",
          result_kind: "MAP_ROUTING",
          job_status: "SUCCEEDED",
          poll_after_seconds: 1,
          error: null,
          result: {
            routing_version: "SERVER_ROUTING_V2",
            source: "AMAP",
            coordinate_system: "GCJ02",
            routing_profile: "DRIVING_CAR",
            node_versions: { W: 1 },
            node_count: 9,
            published_at: new Date().toISOString(),
          },
        }),
      });
    try {
      let data = await mockRequest<any>(path + url.search, body ?? undefined);
      if (path === "/delivery-context") {
        contextReads++;
        if (options.routing) data.routing_ready = true;
      }
      return r.fulfill({
        status:
          req.method() === "POST" &&
          ["/chat", "/impact-analyses", "/recovery-plans"].includes(path)
            ? 202
            : 200,
        json: envelope(data),
      });
    } catch (e) {
      return fail(r, (e as any).status || 500);
    }
  });
  await page.goto("/");
  await page.getByLabel("工作账号").fill("test");
  await page.getByLabel("登录密码").fill("TEST_ONLY");
  await page.getByRole("button", { name: "登录工作空间", exact: true }).click();
  await expect(page).toHaveURL(role === "DRIVER" ? /driver$/ : /overview$/);
  return { maps, posts, contextReads: () => contextReads };
}

test("35路径契约服务：候选参数成对、历史按next_offset分页", async () => {
  expect(() => demoMapPath("", "P")).toThrow();
  expect(demoMapPath("V01", "real/id")).toBe(
    "/map/demo?vehicle_id=V01&plan_id=real%2Fid",
  );
  const paths: string[] = [];
  const read = async <T>(p: string) => {
    paths.push(p);
    return { items: [], next_offset: null } as T;
  };
  await getConversations(20, undefined, read);
  await getConversationMessages("c/1", 40, undefined, read);
  expect(paths).toEqual([
    "/conversations?limit=20&offset=20",
    "/conversations/c%2F1/messages?limit=20&offset=40",
  ]);
});
test("武汉适配器：9点5车、无GPS、空数值、严格版本与几何", async () => {
  const context = await mockRequest<Context>("/delivery-context");
  const data = await mockRequest<Schema["DemoMapOutput"]>("/map/demo");
  const input = {
    data,
    context,
    stops: context.route_stops,
    vehicleId: "",
    planId: null,
    local: true,
  };
  const model = adaptWuhanMap(input);
  expect(model.markers).toHaveLength(9);
  expect(model.layers).toHaveLength(5);
  expect(model.markers.filter((m) => m.kind === "vehicle")).toHaveLength(0);
  expect(model.markers.filter((m) => m.kind === "warehouse")).toHaveLength(1);
  expect(model.center).toEqual(data.center);
  expect(
    model.layers.every(
      (l) =>
        l.distanceM === null &&
        l.durationSeconds === null &&
        l.geometryKind === "SCHEMATIC",
    ),
  ).toBe(true);
  const bad = structuredClone(data);
  bad.routes[0]!.route_revision = 999;
  expect(adaptWuhanMap({ ...input, data: bad }).layers).toHaveLength(4);
  bad.routes[1]!.geometry!.coordinates[0] = [0, 0];
  expect(adaptWuhanMap({ ...input, data: bad }).layers).toHaveLength(3);
  const preview = structuredClone(data);
  preview.routes[0]!.route_kind = "CANDIDATE_PREVIEW";
  expect(adaptWuhanMap({ ...input, data: preview }).issues).toContain(
    "候选预览不能作为当前路线",
  );
  configureDemoScenario("wuhan", "DRIVER");
  const own = await mockRequest<Schema["DemoMapOutput"]>("/map/demo");
  expect(own.points).toHaveLength(4);
  await expect(mockRequest("/map/demo?vehicle_id=V02")).rejects.toMatchObject({
    status: 404,
  });
});
test("SCHEMATIC虚线与ROAD实线保持独立，候选不成为CURRENT", () => {
  const lines: any[] = [];
  const sdk = {
    Polyline: class {
      constructor(o: any) {
        lines.push(o);
      }
    },
  } as any;
  drawRouteLayers(sdk, {} as any, [
    {
      role: "original",
      geometryKind: "SCHEMATIC",
      coordinates: [
        [114, 30],
        [114.1, 30.1],
      ],
    },
    {
      role: "candidate",
      geometryKind: "SCHEMATIC",
      coordinates: [
        [114, 30],
        [114.2, 30.2],
      ],
    },
    {
      role: "current",
      geometryKind: "ROAD",
      coordinates: [
        [114, 30],
        [114.3, 30.3],
      ],
    },
  ]);
  expect(lines.map((l) => l.strokeStyle)).toEqual([
    "dashed",
    "dashed",
    "solid",
  ]);
  expect(lines[1].strokeColor).toBe("#d86a21");
});
test("司机JWT默认作用域、任务与Marker双向选中", async ({ page }) => {
  const { maps } = await api(page);
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-business-points",
    "4",
  );
  expect(maps[0]).toEqual({ query: "", authorization: "Bearer JWT_TEST_ONLY" });
  await page.locator(".route-stop").nth(1).click();
  await expect(
    page.locator('.amap-delivery-marker[data-node="B"]'),
  ).toHaveClass(/selected/);
  await page.locator('.amap-delivery-marker[data-node="C"]').click();
  await expect(page.locator(".route-stop").nth(2)).toHaveClass(/selected/);
  await expect(page.locator(".amap-delivery-marker.vehicle")).toHaveCount(0);
  await expect(page.locator(".map-footer")).toContainText(
    "模拟连线，不代表实际行驶道路",
  );
});
test("调度默认车队、车辆筛选和实际候选切换同步更新几何", async ({ page }) => {
  const { maps, posts } = await api(page, "DISPATCHER");
  await generate(page);
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-route-layers",
    "2",
  );
  const first = await page.locator(".map-canvas").getAttribute("data-plan");
  const a = await page.evaluate(
    () => (window as any).__amapTest.lines.at(-1).path,
  );
  await page.locator(".candidate-tabs button").last().click();
  await expect(page.locator(".map-canvas")).not.toHaveAttribute(
    "data-plan",
    first!,
  );
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).__amapTest.lines.at(-1).path),
    )
    .not.toEqual(a);
  expect(
    maps
      .filter((m) => m.query.includes("plan_id="))
      .every((m) => m.query.includes("vehicle_id=V01")),
  ).toBe(true);
  expect(posts.some((p) => p.path === "/map/routing/refresh")).toBe(false);
  await page.goto("/#/incidents");
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-business-points",
    "9",
  );
  await page.getByLabel("工作台选择车辆").selectOption("V02");
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-business-points",
    "6",
  );
});
test("候选409清空预览、刷新业务对象，不当作有效路线", async ({ page }) => {
  const state = await api(page, "DISPATCHER", { conflict: true });
  await generate(page);
  await expect(page.locator(".map-data-feedback")).toContainText(
    "候选地图已失效",
  );
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-route-layers",
    "0",
  );
  expect(state.contextReads()).toBeGreaterThan(1);
  const n = state.maps.length;
  await page.waitForTimeout(300);
  expect(state.maps.length).toBe(n);
});
test("业务地图断线不静默切换本地样例，任务与底图独立", async ({ page }) => {
  await api(page, "DRIVER", { outage: true });
  await expect(page.locator(".map-data-feedback")).toContainText(
    "无法连接业务服务",
  );
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-sdk-ready",
    "true",
  );
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-business-points",
    "0",
  );
  await expect(page.locator(".route-stop")).toHaveCount(3);
  await expect(
    page.getByRole("button", { name: "上报配送异常", exact: true }),
  ).toBeEnabled();
});
test("历史列表恢复与分页后沿用conversation_id，不重放旧计算", async ({
  page,
}) => {
  const state = await api(page);
  const meta = {
    conversation_id: "SAVED_CONV",
    created_at: new Date().toISOString(),
    active_turn_id: "TURN_NEW",
    selected_order_no: "O001",
    incident_id: null,
  };
  await page.route("**/api/v1/conversations**", (r) => {
    const u = new URL(r.request().url());
    if (u.pathname.endsWith("/messages")) {
      const second = u.searchParams.get("offset") === "20";
      return r.fulfill({
        json: envelope({
          conversation_id: "SAVED_CONV",
          items: [
            {
              turn_id: second ? "TURN_NEW" : "TURN_OLD",
              job_id: second ? "J_NEW" : "J_OLD",
              status: "SUCCEEDED",
              created_at: second
                ? "2026-11-04T12:01:00Z"
                : "2026-11-04T12:00:00Z",
              user_message: second ? "预计20分钟" : "堵车了",
              assistant_reply: second ? "请确认下一步" : "预计多久？",
            },
          ],
          next_offset: second ? null : 20,
        }),
      });
    }
    return r.fulfill({ json: envelope({ items: [meta], next_offset: null }) });
  });
  await page.getByRole("button", { name: "打开智能助手" }).click();
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await page.getByRole("button", { name: /SAVED_CONV/ }).click();
  await expect(page.getByText("活动轮次尚未读取")).toBeVisible();
  await page.getByRole("button", { name: "加载更多消息" }).click();
  await expect(page.locator(".chat-message")).toHaveCount(4);
  await page.getByLabel("发送给智能助手").fill("继续补充");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect
    .poll(() => state.posts.filter((p) => p.path === "/chat").length)
    .toBe(1);
  expect(state.posts.find((p) => p.path === "/chat")!.body).toEqual({
    message: "继续补充",
    conversation_id: "SAVED_CONV",
  });
});
test("MAP_ROUTING手动门控与结果分派，不伪装成聊天或计划生效", async ({
  page,
}) => {
  const state = await api(page, "DISPATCHER", { routing: true });
  await page.goto("/#/incidents");
  expect(state.posts.some((p) => p.path === "/map/routing/refresh")).toBe(
    false,
  );
  await page.getByRole("button", { name: "刷新道路路由", exact: true }).click();
  await expect(page.getByText(/路由版本 SERVER_ROUTING_V2/)).toBeVisible();
  expect(
    state.posts.find((p) => p.path === "/map/routing/refresh")!.body,
  ).toEqual({ expected_routing_version: "SERVER_ROUTING_V1" });
  expect(
    state.posts.find((p) => p.path === "/map/routing/refresh")!.key,
  ).toBeTruthy();
  await expect(page.locator(".chat-message")).toHaveCount(0);
});
test("道路服务未就绪禁止刷新，地图SDK失败仍保留武汉任务", async ({ page }) => {
  await local(page, "调度员演示");
  await page.route("https://webapi.amap.com/maps?**", (r) => r.abort("failed"));
  await page.goto("/#/incidents");
  await expect(
    page.getByRole("button", { name: "刷新道路路由", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".linked-order")).toHaveCount(3);
});

test("批准后仅待接收，司机预览与ACTIVE重新读取当前路线", async ({ page }) => {
  await local(page, "调度员演示");
  await generate(page);
  await page.getByRole("button", { name: "批准此方案", exact: true }).click();
  await page.getByRole("button", { name: "确认批准", exact: true }).click();
  await expect(page.locator(".plan-status-bar")).toContainText(
    "已批准 · 待司机接收",
  );
  await expect(page.locator(".map-plan-badge")).toContainText("方案预览");
  await page.getByTitle("退出登录").click();
  await page.getByRole("button", { name: "武汉联调本地样例" }).click();
  await page.getByRole("button", { name: "司机演示", exact: true }).click();
  const planSelect = page.getByLabel("司机地图方案");
  const id = await planSelect
    .locator("option:not(:disabled)")
    .nth(1)
    .getAttribute("value");
  await planSelect.selectOption(id!);
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-route-layers",
    "2",
  );
  await expect(page.locator(".map-plan-badge")).toContainText("方案预览");
  await page.getByRole("button", { name: "接受调整", exact: true }).click();
  await page.getByRole("button", { name: "确认接受", exact: true }).click();
  await expect(page.locator(".map-canvas")).toHaveAttribute(
    "data-plan",
    "current",
  );
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-route-layers",
    "1",
  );
  await expect(page.locator(".map-plan-badge")).toContainText("当前有效路线");
});

test("较慢的旧候选响应不能覆盖新选择", async ({ page }) => {
  await api(page, "DISPATCHER");
  await generate(page);
  const original = await page.locator(".map-canvas").getAttribute("data-plan");
  let started = false;
  await page.route("**/api/v1/map/demo?**", async (r) => {
    const u = new URL(r.request().url());
    const data = await mockRequest(
      u.pathname.replace("/api/v1", "") + u.search,
    );
    if (
      u.searchParams.get("plan_id") !== original &&
      u.searchParams.has("plan_id")
    ) {
      started = true;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    await r.fulfill({ json: envelope(data) }).catch(() => {});
  });
  await page.locator(".candidate-tabs button").last().click();
  await expect.poll(() => started).toBe(true);
  await page.locator(".candidate-tabs button").first().click();
  await expect(page.locator(".map-canvas")).toHaveAttribute(
    "data-plan",
    original!,
  );
  await page.waitForTimeout(650);
  await expect(page.locator(".map-canvas")).toHaveAttribute(
    "data-plan",
    original!,
  );
  await expect(page.locator(".amap-workspace")).toHaveAttribute(
    "data-route-layers",
    "2",
  );
  await expect(page.locator(".amap-adapter-warning")).toHaveCount(0);
});

test("离开历史读取或新会话后迟到响应不能恢复旧上下文", async ({ page }) => {
  await api(page);
  const meta = {
    conversation_id: "DELAYED_CONV",
    created_at: new Date().toISOString(),
    active_turn_id: null,
    selected_order_no: "O001",
    incident_id: null,
  };
  let started = false;
  await page.route("**/api/v1/conversations**", async (r) => {
    if (new URL(r.request().url()).pathname.endsWith("/messages")) {
      started = true;
      await new Promise((resolve) => setTimeout(resolve, 400));
      return r
        .fulfill({
          json: envelope({
            conversation_id: meta.conversation_id,
            items: [],
            next_offset: null,
          }),
        })
        .catch(() => {});
    }
    return r.fulfill({ json: envelope({ items: [meta], next_offset: null }) });
  });
  await page.getByRole("button", { name: "打开智能助手" }).click();
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await page.getByRole("button", { name: /DELAYED_CONV/ }).click();
  await expect.poll(() => started).toBe(true);
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await page.waitForTimeout(550);
  await expect(page.locator(".chat-context")).toContainText("新异常描述");
  await expect(page.locator(".history-notice")).toHaveCount(0);
});

test("历史活动作业不自动轮询，主动恢复后正确更新本轮结果", async ({ page }) => {
  await api(page);
  let polls = 0;
  await page.route("**/api/v1/conversations**", (r) => {
    const meta = {
      conversation_id: "PENDING_CONV",
      created_at: new Date().toISOString(),
      active_turn_id: "PENDING_TURN",
      selected_order_no: "O001",
      incident_id: null,
    };
    return r.fulfill({
      json: envelope(
        new URL(r.request().url()).pathname.endsWith("/messages")
          ? {
              conversation_id: meta.conversation_id,
              items: [
                {
                  turn_id: "PENDING_TURN",
                  job_id: "PENDING_JOB",
                  status: "RUNNING",
                  created_at: meta.created_at,
                  user_message: "堵车了",
                  assistant_reply: null,
                },
              ],
              next_offset: null,
            }
          : { items: [meta], next_offset: null },
      ),
    });
  });
  await page.route("**/api/v1/jobs/PENDING_JOB", (r) => {
    polls++;
    return r.fulfill({
      json: envelope({
        job_id: "PENDING_JOB",
        job_type: "CHAT",
        result_kind: "CHAT",
        job_status: "SUCCEEDED",
        poll_after_seconds: 1,
        error: null,
        result: {
          reply: "请补充预计延误",
          business_state: "NEEDS_INFO",
          next_action: "ASK_USER",
          incident_id: null,
          job_ids: [],
          plan_ids: [],
          evidence_refs: [],
        },
      }),
    });
  });
  await page.getByRole("button", { name: "打开智能助手" }).click();
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await page.getByRole("button", { name: /PENDING_CONV/ }).click();
  await expect(
    page.getByRole("button", { name: "恢复本轮查询", exact: true }),
  ).toBeVisible();
  expect(polls).toBe(0);
  await page.getByRole("button", { name: "恢复本轮查询", exact: true }).click();
  await expect(page.locator(".chat-message.assistant")).toContainText(
    "请补充预计延误",
  );
  expect(polls).toBe(1);
  await expect(page.locator(".chat-message.user")).toHaveCount(1);
});

test("MAP_ROUTING不确定写入按Status分派并用原键原体重试", async ({ page }) => {
  await api(page, "DISPATCHER", { routing: true });
  const writes: { key: string; body: string }[] = [];
  await page.route("**/api/v1/map/routing/refresh", (r) => {
    writes.push({
      key: r.request().headers()["idempotency-key"]!,
      body: r.request().postData()!,
    });
    return writes.length === 1
      ? r.abort("connectionreset")
      : r.fulfill({
          status: 202,
          json: envelope({
            job_id: "MAP_JOB",
            job_type: "MAP_ROUTING",
            job_status: "QUEUED",
            poll_after_seconds: 0,
          }),
        });
  });
  await page.route("**/api/v1/status?**", (r) =>
    r.fulfill({
      json: envelope({
        object_type: "REQUEST",
        object_id: writes[0]!.key,
        job_status: "SUCCEEDED",
        result_kind: "MAP_ROUTING",
        error: null,
        result: {
          routing_version: "STATUS_ROUTING",
          source: "AMAP",
          coordinate_system: "GCJ02",
          routing_profile: "DRIVING_CAR",
          node_versions: { W: 1 },
          node_count: 9,
          published_at: new Date().toISOString(),
        },
      }),
    }),
  );
  await page.goto("/#/incidents");
  await page.getByRole("button", { name: "刷新道路路由", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "按原键重试", exact: true }),
  ).toBeVisible();
  expect(writes).toHaveLength(1);
  await page.getByRole("button", { name: "恢复查询", exact: true }).click();
  await expect(page.getByText(/路由版本 STATUS_ROUTING/)).toBeVisible();
  expect(writes).toHaveLength(1);
  await page.getByRole("button", { name: "按原键重试", exact: true }).click();
  await expect(page.getByText(/路由版本 SERVER_ROUTING_V2/)).toBeVisible();
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
});

test("实际位置离开仓库及已到站重复节点仍可显示模拟剩余路线", async () => {
  configureDemoScenario("wuhan", "DRIVER");
  const context = await mockRequest<Context>("/delivery-context");
  const data = await mockRequest<Schema["DemoMapOutput"]>("/map/demo");
  const route = data.routes[0]!;
  context.vehicles[0]!.location_node_id = "B";
  context.route_stops = ["B", "A", "C"].map((n, i) => ({
    ...context.route_stops.find((s) => s.node_id === n)!,
    sequence_no: i + 1,
    status: n === "B" ? ("COMPLETED" as const) : ("PENDING" as const),
  }));
  route.node_sequence = ["B", "A", "C"];
  route.geometry = {
    type: "LineString",
    coordinates: route.node_sequence.map((n) => {
      const p = data.points.find((p) => p.node_id === n)!;
      return [p.longitude, p.latitude];
    }),
  };
  const input = {
    data,
    context,
    stops: context.route_stops,
    vehicleId: "V01",
    planId: null,
    local: false,
  };
  expect(adaptWuhanMap(input).layers).toHaveLength(1);
  context.route_stops[0]!.status = "ARRIVED";
  route.node_sequence = ["B", "B", "A", "C"];
  expect(adaptWuhanMap(input).layers).toHaveLength(1);
});

for (const discovery of ["manual", "automatic"] as const) {
  test(`司机从旧异常发现新待确认调整 ${discovery}`, async ({ page }) => {
    await api(page, "DRIVER");
    const base = (await mockRequest<any>("/incidents")).incidents[0];
    const old = { ...base, status: "EXECUTING" };
    const incoming = { ...base, incident_id: "INC_NEW_ACK", status: "AWAITING_ACK" };
    const context = await mockRequest<any>("/delivery-context");
    const analysisJob = await mockRequest<any>("/impact-analyses", {
      incident_id: base.incident_id, snapshot_id: context.snapshot_id,
      expected_incident_version: base.incident_version,
    });
    const analysis = (await mockRequest<any>(`/jobs/${analysisJob.job_id}`)).result;
    const generation = await mockRequest<any>("/recovery-plans", {
      incident_id: base.incident_id, snapshot_id: analysis.snapshot_id,
      expected_incident_version: base.incident_version, analysis_id: analysis.analysis_id,
    });
    const plan = (await mockRequest<any>(`/jobs/${generation.job_id}`)).result.plans[0];
    let published = false;
    await page.route("**/api/v1/incidents**", async (r) => {
      const path = new URL(r.request().url()).pathname;
      const data = path.endsWith("/incidents")
        ? { incidents: published ? [old, incoming] : [old] }
        : { incident: path.includes("INC_NEW_ACK") ? incoming : old, analyses: [],
            plans: path.includes("INC_NEW_ACK") ? [{ ...plan, incident_id: incoming.incident_id,
              status: "APPROVED_PENDING_ACK", approval_mode: "MANUAL" }] : [] };
      return r.fulfill({ json: envelope(data) });
    });
    await page.getByRole("button", { name: "刷新司机异常", exact: true }).click();
    await expect(page.getByRole("button", { name: "接受调整", exact: false })).toHaveCount(0);
    published = true;
    if (discovery === "manual")
      await page.getByRole("button", { name: "刷新司机异常", exact: true }).click();
    await expect(page.getByRole("button", { name: "接受调整", exact: false }))
      .toBeVisible({ timeout: 16000 });
  });
}
