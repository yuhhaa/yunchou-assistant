import { test, expect } from "./test";
import { mkdirSync } from "node:fs";

mkdirSync("screenshots/driver-responsive/after", { recursive: true });
for (const width of [1600, 1440, 1280, 768, 390]) {
  test(`司机响应式工作区 ${width}px：布局、触屏操作与地图展开`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width <= 768 ? 844 : 1000 });
    await page.goto("/");
    await page.getByRole("button", { name: "体验独立演示空间" }).click();
    await page.getByRole("button", { name: "司机演示", exact: true }).click();
    const next = page.locator(".next-stop-card");
    const route = page.locator(".driver-map-column");
    const map = route.locator(".map-view");
    await expect(next).toContainText("滨江生活中心");
    const n = (await next.boundingBox())!;
    const r = (await route.boundingBox())!;
    if (width >= 1024) {
      expect(r.x).toBeGreaterThan(n.x + n.width);
      expect(n.height).toBeLessThan(310);
      expect(n.y).toBeLessThan(230);
      expect(r.width / n.width).toBeGreaterThan(1.5);
      expect((await page.locator(".driver-main").boundingBox())!.width).toBe(
        width,
      );
      await expect(page.locator(".driver-bottom-nav")).toBeHidden();
      await expect(
        page.getByRole("navigation", { name: "司机工作导航" }),
      ).toBeVisible();
      await page.getByRole("button", { name: "我的路线", exact: true }).click();
      await expect(
        page.getByRole("button", { name: "我的路线", exact: true }),
      ).toHaveClass(/active/);
      await page.getByRole("button", { name: "今日配送", exact: true }).click();
    } else {
      await expect(page.locator(".driver-desktop-nav")).toBeHidden();
      await expect(page.locator(".driver-bottom-nav")).toBeVisible();
      const timeline = (await page.locator(".driver-timeline").boundingBox())!;
      expect(timeline.y).toBeLessThan(r.y);
      expect(
        (await map.locator(".map-canvas").boundingBox())!.height,
      ).toBeLessThanOrEqual(190);
      if (width === 768) expect(n.height).toBeLessThan(270);
    }
    await page.locator(".route-stop").filter({ hasText: "O002" }).click();
    await expect(map.locator(".map-node-inspector")).toContainText("O002");
    await expect(map).toContainText("真实地理数据尚未接入");
    await expect(page.locator(".delivery-map-marker")).toHaveCount(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: `screenshots/driver-responsive/after/driver-${width}.png`,
      fullPage: true,
    });
    await map.getByRole("button", { name: "展开配送地图" }).click();
    const expanded = page.getByRole("dialog", { name: "我的配送路线" });
    await expect(expanded).toBeVisible();
    expect(
      (await expanded.locator(".map-canvas").boundingBox())!.height,
    ).toBeGreaterThan(250);
    await page.keyboard.press("Escape");
    await expect(
      map.getByRole("button", { name: "展开配送地图" }),
    ).toBeFocused();
    await page
      .locator(".next-stop-card")
      .getByRole("button", { name: "上报配送异常" })
      .click();
    await expect(
      page.getByRole("dialog", { name: "上报配送异常" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
}
