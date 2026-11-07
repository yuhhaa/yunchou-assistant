import { test, expect } from "./test";

test("六步处置只从业务证据确认，批准不当作司机确认或ACTIVE", async ({
  page,
}) => {
  await page.goto("/tests/fixtures/design-harness.html");
  const steps = page.locator(".ledger-step");
  await expect(steps).toHaveCount(6);
  await expect(steps.nth(0)).toHaveClass(/done/);
  await expect(steps.nth(1)).toHaveClass(/current/);
  await steps.nth(0).getByRole("button").focus();
  await page.keyboard.press("Enter");
  await expect(steps.nth(0).getByRole("button")).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(steps.nth(0).locator(".ledger-evidence")).toContainText(
    "INC_DEMO_01",
  );
  await page.getByRole("button", { name: "分析已返回", exact: true }).click();
  await page.getByRole("button", { name: "候选已返回", exact: true }).click();
  await expect(steps.nth(3)).toHaveClass(/current/);
  await page.getByRole("button", { name: "调度已批准", exact: true }).click();
  await expect(steps.nth(3)).toHaveClass(/done/);
  await expect(steps.nth(4)).toHaveClass(/current/);
  await expect(steps.nth(5)).not.toHaveClass(/done/);
  await page.getByRole("button", { name: "后端ACTIVE", exact: true }).click();
  await expect(page.locator(".ledger-step.done")).toHaveCount(6);
});
test("失败与UNKNOWN保持独立视觉，微图表缺分析或版本不一致时不绘制", async ({
  page,
}) => {
  await page.goto("/tests/fixtures/design-harness.html");
  await page.getByRole("button", { name: "当前作业失败", exact: true }).click();
  await expect(page.locator(".ledger-step").nth(1)).toHaveClass(/failed/);
  await page.getByRole("button", { name: "未知作业状态", exact: true }).click();
  await expect(page.locator(".ledger-step").nth(1)).toHaveClass(/unknown/);
  await expect(page.locator(".delay-comparison")).toHaveCount(1);
  await page
    .getByRole("button", { name: "分析版本不一致", exact: true })
    .click();
  await expect(page.locator(".delay-comparison")).toHaveCount(0);
  await expect(page.locator(".instrument-missing")).toBeVisible();
  await page.getByRole("button", { name: "移除图表分析", exact: true }).click();
  await expect(page.locator(".delay-comparison")).toHaveCount(0);
});
test("配送分段到达与完成分离，不加载装饰字符动效", async ({ page }) => {
  await page.goto("/tests/fixtures/design-harness.html");
  const progress = page.getByRole("region", { name: "配送执行进度" });
  await page.getByRole("button", { name: "到达第一站", exact: true }).click();
  await expect(progress.getByRole("img")).toHaveAttribute(
    "aria-label",
    "0站完成，1站已到达，共3站",
  );
  await page.getByRole("button", { name: "完成第一站", exact: true }).click();
  await expect(progress.getByRole("img")).toHaveAttribute(
    "aria-label",
    "1站完成，0站已到达，共3站",
  );
  await expect(page.locator(".ascii-signal")).toHaveCount(0);
});
test("协作空状态草稿不自动发送，对话后空状态退出", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "武汉联调本地样例" }).click();
  await page.getByRole("button", { name: "司机演示", exact: true }).click();
  await page.getByRole("button", { name: "打开智能助手", exact: true }).click();
  await expect(page.locator(".assistant-empty")).toBeVisible();
  await expect(page.locator(".ascii-signal")).toHaveCount(0);
  await page.getByRole("button", { name: "道路拥堵", exact: true }).click();
  await expect(page.getByLabel("发送给智能助手")).toHaveValue(
    "前方道路出现拥堵，",
  );
  await expect(page.locator(".chat-message")).toHaveCount(0);
  await page.getByLabel("发送给智能助手").fill("堵车了。");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".chat-message.assistant")).toBeVisible();
  await expect(page.locator(".ascii-signal")).toHaveCount(0);
  await expect(page.locator(".chat-business-result")).toContainText(
    "等待补充信息",
  );
});
for (const width of [1600, 1440, 1280, 768, 390])
  test(`设计升级${width}px：状态带、流程、风险条与地图保持可用`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width <= 768 ? 844 : 1000 });
    await page.goto("/");
    await page.getByRole("button", { name: "武汉联调本地样例" }).click();
    await page.getByRole("button", { name: "调度员演示", exact: true }).click();
    await page.goto("/#/incidents");
    await expect(
      page.getByRole("region", { name: "授权事件状态概览" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "执行影响分析", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "订单时间窗风险分布" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "O002，延误15分钟，存在风险", exact: true })
      .click();
    await expect(page.locator(".map-node-inspector")).toContainText("O002");
    await expect(page.locator(".ledger-step.done")).toHaveCount(2);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });

test("768px方案审批说明和按钮不重叠", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "武汉联调本地样例" }).click();
  await page.getByRole("button", { name: "调度员演示", exact: true }).click();
  await page.goto("/#/incidents");
  await page.getByRole("button", { name: "执行影响分析", exact: true }).click();
  await expect(page.getByText("当前有效分析")).toBeVisible();
  await page.getByRole("button", { name: "生成恢复候选", exact: true }).click();
  await expect(page.getByRole("link", { name: /比较.*候选/ })).toBeVisible();
  await page.goto("/#/plans");
  const approval = await page.locator(".approval-brief").boundingBox(),
    button = await page
      .getByRole("button", { name: "批准此方案", exact: true })
      .boundingBox();
  expect(button!.y).toBeGreaterThanOrEqual(approval!.y + approval!.height);
  const instrument = await page.locator(".plan-instrument").boundingBox(),
    metrics = await page.locator(".decision-metrics").boundingBox();
  expect(metrics!.y).toBeGreaterThan(instrument!.y + instrument!.height);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
