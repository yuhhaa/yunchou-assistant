import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import { existsSync } from "node:fs";
import { loadEnvFile } from "node:process";
import { amapDevelopmentProxy } from "./server/amapProxy";
export default defineConfig(({ mode, command }) => {
  if (command === "serve" && existsSync(".env.amap-server.local"))
    loadEnvFile(".env.amap-server.local");
  const env = loadEnv(mode, process.cwd(), "");
  if (
    Object.keys(env).some((name) =>
      /^VITE_.*AMAP.*(SECURITY|JSCODE)/i.test(name),
    )
  )
    throw new Error("高德安全密钥不得配置为 VITE_ 变量，请移至服务端配置。");
  return {
    plugins: [vue(), amapDevelopmentProxy()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      watch: {
        ignored: [
          "**/playwright-report/**",
          "**/test-results/**",
          "**/screenshots/**",
        ],
      },
      proxy: {
        "/api": {
          target: env.API_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
