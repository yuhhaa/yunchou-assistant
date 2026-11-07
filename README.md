# 运筹助手

城市配送异常处置与最小扰动恢复应用。Vue 3 前端、FastAPI 后端、PostgreSQL、Redis/Celery，支持调度审批、司机回执、执行反馈、DeepSeek直连及武汉模拟地图。

## 首次运行

需要 Git、Docker Desktop（Linux容器）、Node.js。本次已验证 Node 24.11.1、npm 11.6.2；Python运行在Docker中，本机不必安装Python。

Windows PowerShell，在仓库根目录运行：

```powershell
git clone https://github.com/yuhhaa/yunchou-assistant.git
cd yunchou-assistant
powershell -NoProfile -ExecutionPolicy Bypass -File backend/scripts/init-env.ps1
docker compose --env-file .env up -d --build --wait
docker compose --env-file .env exec -T api python -m app.seed
cd frontend
npm ci
Copy-Item .env.example .env.local
npm run dev -- --port 5177 --strictPort
```

打开 http://127.0.0.1:5177 。API文档：http://127.0.0.1:8000/docs 。只对新空数据库seed一次；已有环境更新不重新seed。初始化脚本拒绝覆盖已有.env，生成独立随机凭据。

账号：dispatcher、driver01至driver05。密码读取根目录私有.env中的DEMO_DISPATCHER_PASSWORD、DEMO_DRIVER_PASSWORD。每台新环境密码独立，不使用开发者电脑的账号密码。

默认AGENT_MODE=local和SIMULATION业务数据，可在不配置模型、地图Key的情况下验证业务流程。地图未配置时显示提示，不影响任务列表和业务操作。1仓库、5车、20单，武汉点位及虚线明确为模拟。

## 配置和部署边界

- [地图、模型、已有数据升级和生产说明](docs/部署与配置.md)
- [后端说明](backend/README.md) · [接口说明](backend/API.md)
- [完整OpenAPI](backend/openapi.json) · [DeepSeek内部工具契约](docs/deepseek-tool-contract.json)
- [前端、AI及地图契约](docs/赛题一前端与AI地图对齐文档.md)

JS Key与配套jscode、后端地图Web服务Key、DeepSeek API Key均由部署者自己申请并配置。仓库不包含这些凭据、数据库、依赖目录或构建产物。

上述步骤是本机开发/演示运行方式。公网生产部署还需HTTPS、访问控制、备份和前端同源高德安全代理；Vite开发代理不会进入dist，不将本地验证等同生产验收。

## 验证

前端目录执行npm run build、npm test。后端目录使用Python3.12与uv执行uv sync --frozen、uv run pytest -q。可用专用TEST_DATABASE_URL、TEST_REDIS_URL运行PostgreSQL/Redis集成测试，不连接正式队列。

已完成实际浏览器调用真实后端的登录、上报、分析、候选、审批、司机接受、生效、到站/完成及local补问验证。真实DeepSeek服务与实际道路规划需部署者另行验收。原AgentArts运行依赖已移除。
