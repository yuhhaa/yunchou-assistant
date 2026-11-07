import { test as base, expect } from "@playwright/test";
// Business regressions must remain deterministic and must not consume external map quota.
// Real SDK/tile/interaction verification is scripts/verify-amap.mjs, without interception.
export const test = base.extend({
  page: async ({ page }, use) => {
    await page.route("https://webapi.amap.com/maps?**", (route) =>
      route.abort("blockedbyclient"),
    );
    await use(page);
  },
});
export { expect };
