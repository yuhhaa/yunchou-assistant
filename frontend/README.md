# 运筹助手——面向城市配送的 AI 异常处置与最小扰动恢复决策系统

基于 Vue 3、TypeScript、Vite、Pinia、Vue Router 与 Lucide 的城市配送异常处置前端。已提供调度与司机工作空间、原创微缩城市资产、独立演示数据层及真实业务 API 客户端。

当前版本已按赛题一迁移，并对齐 2026-11-04 补充包的 **35 路径 OpenAPI 1.0**。已接入武汉模拟地图、会话历史和 MAP_ROUTING 类型分派；真实道路、DeepSeek 和云部署尚待验收。见 本地团队对接资料（不随Web仓库上传） 与 [验证记录](VALIDATION.md)。

司机工作区本次响应式精修及五档截图见 [司机升级记录](docs/driver-responsive-upgrade.md)。

最新产品精修（2026-11-06）：协作抽屉、运营总览和全站中文/低装饰布局已更新。移除了字符背景和营销口号；75项回归及末次5项面板复测通过。见 [产品精修记录](docs/product-ui-refinement.md)。

地图使用高德 JS API 2.0 标准浅色真实底图。武汉模拟仓库及 A–H 配送点来自交付包；SCHEMATIC 虚线不代表行驶道路，不计算 ETA 或距离，也不冒充车辆GPS。配置见 [高德接入说明](docs/amap-integration.md)，待办见 本地团队对接资料（不随Web仓库上传）。

2026-11-06 UI升级历史：曾加入字符欢迎区，已在后续产品精修中移除；当前保留六步流程、事件状态带、风险/方案微图表和司机执行进度。来源与取舍见 [设计调研](docs/ui-inspiration-research.md)，前后截图和测试见 [设计实施报告](docs/ui-redesign-report.md)。无新增依赖，地图与业务契约保持。

## 本地运行

需要 Node.js 22.12+ 或 24 LTS。

```powershell
cd D:\school\华为ICT\ict
npm ci
npm run dev
```

打开终端输出的本地地址，默认 `http://127.0.0.1:5173`。本次开发预览服务运行在 `http://127.0.0.1:5175`。

登录页点击「体验独立演示空间」可选择调度员或司机。演示数据与业务服务完全分离，界面始终标注 DEMO；真实请求失败不会自动转入演示模式。演示状态仅存在于当前页面内存，刷新会重置。可先以调度员生成并批准候选，再退出并选择司机演示，接收同一页面内的任务调整。

点击「武汉联调本地样例」可验证新版地图与候选联动。坐标来自后端交付的响应样例，任务、聊天及方案由独立本地规则模拟；不代表实时 API 或真实 AI 结果。高德底图仍需本机 Key 和安全代理，联网加载。

真实登录需要先启动交付后端。在项目根目录创建 `.env.local`，只填写代理配置：

```dotenv
API_PROXY_TARGET=http://127.0.0.1:8000
```

修改后重启 Vite。使用后端账号 `dispatcher` 或 `driver01` 至 `driver05`，手动输入私下交付的密码。前端没有内置密码。请勿把上级目录的后端 `.env` 复制进本工程，也不要把平台密钥配置为 `VITE_*`。

## 页面和功能

| 页面 | 能力 |
| --- | --- |
| 登录 | JWT 登录、后端角色识别、独立演示入口、过期重新登录 |
| 城市总览 | 原创微缩城市场景、车辆范围切换、选中车辆订单、授权车队规模、异常摘要、订单与停靠顺序 |
| 异常工作台 | 武汉车队地图、车辆筛选、模拟点位与路线、联动订单列表、授权事件列表、本地搜索筛选、影响分析、候选生成、时间窗核实、转人工 |
| 恢复方案 | 原计划与候选切换、风险节点、订单双向联动、候选切换、原快照与候选逐站对照、保留安排、变更清单、后端指标与约束、版本与到期提示、批准和拒绝 |
| 司机工作空间 | 桌面双栏与胶囊导航、可展开的地图区域与本人停靠顺序、本人车辆、下一站、收货截止、剩余任务、配送时间线、三类异常上报、接受或拒绝方案、到达及完成反馈 |
| 智能协作面板 | 自然语言描述、关联事件/订单、聊天任务轮询、AI 补问、同会话续轮、历史会话分页与恢复、候选入口、计算任务状态 |

司机页面在 1024px 起使用任务与地图双栏，在 768px 及以下使用任务优先单栏和底部导航；调度页面兼容桌面、平板及手机。所有动效支持 `prefers-reduced-motion`。模态框支持键盘焦点限制、Escape 关闭和焦点恢复。微缩城市资产只用于视觉场景；地理数据未交付时不绘制虚构配送坐标或驾驶路线，列表中的停靠顺序来自当前快照。

## 接口契约与状态

前端所需接口类型已保留在 `src/api/schema.d.ts`，开发、类型检查和构建不依赖交接目录。原始 OpenAPI 与交接资料仅在本地保留，不随仓库上传。`npm run generate:api` 是维护者可选命令，需要另行取得原始契约并放回其本地路径。交接文档中对通用字典响应的说明用于补足登录、车辆列表与审批返回类型。旧文档的 Cookie 方案不适用于当前 JWT 后端。浏览器不调用 `agent-tools` 或 `auth/task-token`。

| 业务动作 | 已接入接口 |
| --- | --- |
| 登录 | `POST /api/v1/auth/login` |
| 车辆与快照 | `GET /vehicles`、`GET /delivery-context?vehicle_id=...` |
| 事件读取与创建 | `GET /incidents`、`POST /incidents`、`GET /incidents/{id}/workspace` |
| 影响与恢复 | `POST /impact-analyses`、`POST /recovery-plans`、`GET /jobs/{id}` |
| 计划读取与审批 | `GET /plans/{id}`、`POST /plans/{id}/approve`、`POST /plans/{id}/reject` |
| 司机回执 | `POST /plans/{id}/receipts` |
| 现场执行 | `POST /stops/{id}/completion`，ARRIVED 与 COMPLETED 分开 |
| 协作与人工操作 | `POST /chat`、`POST /incidents/{id}/verify-window`、`POST /incidents/{id}/manual-handoff` |
| 不确定操作恢复 | `GET /status?object_type=REQUEST&object_id=<原幂等键>` |
| 武汉模拟地图 | `GET /map/demo`；候选同时携带实际 `vehicle_id` 与 `plan_id` |
| 地图状态与道路能力 | `GET /map`、`GET /map/route`、`GET /map/geocode`、`PUT /map/nodes/{node_id}`、`POST /map/routing/refresh`（后四项预留/显式操作，未自动请求道路） |
| 会话历史 | `GET /conversations`、`GET /conversations/{conversation_id}/messages`，按 `next_offset` 分页 |

表中未写前缀的接口均位于 `/api/v1`。事件详情通过 workspace 的强类型 `incident` 获取，因此不额外请求重复的事件详情接口。列表契约没有分页和服务端筛选参数，搜索与筛选只在已授权列表内进行。

- JWT 只存在于内存，刷新后必须重新登录；没有虚构刷新令牌或服务器退出接口。
- 业务写请求使用独立 `Idempotency-Key`。不确定结果保留原键和原请求体，先查询操作或按原键恢复，不自动换键。
- `sessionStorage` 仅保存当前用户的待恢复操作及任务引用；不保存登录密码、JWT 或平台凭据。重新登录校验用户 ID，切换账号清除恢复引用。
- HTTP 202 表示受理，继续查询 JOB；HTTP 200 内的 `job_status=FAILED` 仍按失败处理。达到前端轮询上限后保留任务引用，不宣称任务失败。
- 审批前重新读取计划并比对版本，冲突后刷新。批准只表示待司机回执；司机接受不代表任务完成；ACTIVE 仅从后端响应/查询读取。
- 配送业务时间可固定在 SIMULATION，但候选、快照有效期按真实时间判断。批准响应提供预留截止时间时，单独显示；没有该字段时不编造。
- 配送上下文是所选车辆的范围，不是车队订单汇总。车队车辆数来自 `/vehicles`。

## 构建和测试

```powershell
npm run generate:api
npm run typecheck
npm run build
npm test
```

测试默认使用本机 Microsoft Edge 无头浏览器；非 Windows 环境可在 `playwright.config.ts` 移除 `channel: "msedge"`，并执行 `npx playwright install chromium`。测试服务端口为 5175；HTML 报告在 `playwright-report/index.html`，最新结果见 VALIDATION.md。接口测试使用显式拦截的测试响应，不能作为真实后端验收证明。

## 联调结果和仍依赖的内容

2026-11-03：前端预览返回 HTTP 200，本机默认业务后端 `127.0.0.1:8000` 拒绝连接，本地探测记录不随 Web 仓库上传。Direct AI 源码在交付包中存在，但无法确认合并、部署、真实模型与队列链路已验收。未读取真实 `.env`，未调用真实业务写接口。

后端启动后，需用两个角色验证：登录与越权、异步分析、真实指标、审批资源预留、多司机回执、ACTIVE 路线刷新、完成反馈、过期与依赖冲突、聊天补问、历史恢复和断网恢复。真实优化结果、路线 ETA、风险分析、可行性与 AI 回复均依赖后端；字段未提供时显示待确认。当前契约已有分页会话历史，但不提供独立历史原路线、刷新令牌和通知已读接口。

生产构建输出 `dist/`，需由同源 Web 服务器将 `/api` 转发到后端。工程采用 Hash 路由，无需为页面路由配置 history fallback。当前交付仍为 SIMULATION 用途，尚未配置公网部署。

## 工程结构

```text
src/api/          统一请求客户端、契约类型、业务类型
src/stores/       身份、上下文、事件、任务与恢复流程
src/mock/         显式启用的演示传输层
src/components/   路线时间线、智能协作面板
src/views/        登录、调度和司机业务页面
src/lib/          语义状态及时间格式化
src/styles.css    统一 tokens、响应式与交互动效
tests/            正常业务流程及异常恢复测试
public/images/    原创城市场景
screenshots/      桌面和手机页面验证截图
handoff/          原始交付契约
reference/        需求文档文本提取，便于对照
```

视觉资产来源、字体授权与生成提示词见 `ASSETS.md`。本工程未复用 Luma 或 Claude 的品牌、Logo、图片、字体或受保护素材。
