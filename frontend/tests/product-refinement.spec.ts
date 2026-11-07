import { test, expect } from "./test";
for (const width of [1600, 1440, 1280, 768, 390])
  test(`协作抽屉${width}px：默认收起、无装饰、关闭恢复页面及键盘焦点`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width <= 768 ? 844 : 1000 });
    await page.goto("/");
    await page.getByRole("button", { name: "武汉联调本地样例" }).click();
    await page.getByRole("button", { name: "调度员演示", exact: true }).click();
    await expect(page.locator(".assistant-panel")).toHaveCount(0);
    const before = (await page.locator(".city-stage").boundingBox())!;
    const opener = page.getByRole("button", {
      name: "打开智能助手",
      exact: true,
    });
    await opener.click();
    await expect(
      page.getByRole("complementary", { name: "事件协同", exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("发送给智能助手")).toBeFocused();
    await expect(
      page.locator(".ascii-signal,.assistant-intro,.assistant-mark"),
    ).toHaveCount(0);
    const box = (await page.locator(".assistant-panel").boundingBox())!;
    expect(box.width).toBeLessThanOrEqual(width <= 768 ? width : 420);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "历史会话", exact: true }).click();
    await expect(page.getByText("当前没有已保存会话。")).toBeVisible();
    await page.getByRole("button", { name: "历史会话", exact: true }).click();
    await page.getByLabel("发送给智能助手").fill("堵车了。");
    await page.getByRole("button", { name: "发送消息", exact: true }).click();
    await expect(page.locator(".chat-message.assistant")).toBeVisible();
    await page.getByRole("button", { name: "历史会话", exact: true }).click();
    await expect(page.locator(".history-item")).toHaveCount(1);
    await page.locator(".history-item").click();
    await expect(page.locator(".chat-message.user")).toHaveCount(1);
    await page.getByRole("button", { name: "关闭助手", exact: true }).click();
    await expect(page.locator(".assistant-panel")).toHaveCount(0);
    await expect(opener).toBeFocused();
    const after = (await page.locator(".city-stage").boundingBox())!;
    expect(after.width).toBeCloseTo(before.width, 0);
    await opener.click();
    await page.keyboard.press("Escape");
    await expect(page.locator(".assistant-panel")).toHaveCount(0);
    await page.getByTitle("退出登录").click();
    await expect(page.locator(".assistant-panel")).toHaveCount(0);
    await page.getByRole("button", { name: "武汉联调本地样例" }).click();
    await page.getByRole("button", { name: "司机演示", exact: true }).click();
    await expect(page.locator(".driver-main")).toBeVisible();
  });
