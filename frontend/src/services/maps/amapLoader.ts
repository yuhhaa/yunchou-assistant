import AMapLoader from "@amap/amap-jsapi-loader";
import type { AmapSDK, LngLat } from "./amapSdk";
import type { InjectionKey } from "vue";
export const amapLoaderKey: InjectionKey<() => Promise<AmapSDK>> =
  Symbol("amap-loader");

declare global {
  interface Window {
    _AMapSecurityConfig?: { serviceHost: string };
  }
}
let shared: Promise<AmapSDK> | null = null;
let loadedSignature = "";
export const demoCity = {
  name: "武汉",
  center: [114.3, 30.59] as LngLat,
  zoom: 12,
};
export function amapConfiguration() {
  const key = import.meta.env.VITE_AMAP_JS_KEY?.trim() || "";
  const path = import.meta.env.VITE_AMAP_SERVICE_HOST?.trim() || "";
  let host = "";
  try {
    const url = new URL(path, window.location.origin);
    if (
      path &&
      url.origin === window.location.origin &&
      url.pathname === "/_AMapService" &&
      !url.search &&
      !url.hash
    )
      host = url.origin + url.pathname;
  } catch {
    /* Only an explicitly configured same-origin service is accepted. */
  }
  return { key, host, configured: /^[a-f0-9]{32}$/i.test(key) && !!host };
}
export async function loadAmap(config = amapConfiguration()): Promise<AmapSDK> {
  if (!config.configured)
    throw new Error("地图鉴权未配置：请配置新的 Web Key 和同源安全代理。");
  const signature = config.key + config.host;
  if (shared && loadedSignature !== signature)
    throw new Error("地图配置已变化，请刷新页面。");
  if (shared) return shared;
  loadedSignature = signature;
  shared = (async () => {
    try {
      const status = await fetch(config.host + "/_status", {
        signal: AbortSignal.timeout(8000),
        credentials: "same-origin",
      });
      if (!status.ok || !(await status.json()).configured) throw new Error();
    } catch {
      throw new Error("地图鉴权未配置：安全代理尚未就绪，请检查服务端配置。");
    }
    // Must run before the first SDK script is requested. No security credential enters this object.
    window._AMapSecurityConfig = { serviceHost: config.host };
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        AMapLoader.load({
          key: config.key,
          version: "2.0",
          plugins: ["AMap.Scale"],
        }) as Promise<AmapSDK>,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error()), 22000);
        }),
      ]);
    } catch {
      throw new Error("高德地图加载失败：请检查网络、Key 来源限制和安全代理。");
    } finally {
      clearTimeout(timer);
    }
  })().catch((error) => {
    shared = null;
    (AMapLoader as typeof AMapLoader & { reset: () => void }).reset();
    throw error;
  });
  return shared;
}
