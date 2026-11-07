# 高德地图正式接入说明

日期：2026-11-04。保留原 Vue 3 / TypeScript / Vite、JWT、角色权限、聊天、JOB、审批与回执。本次地图是展示层，不改变业务状态和算法。

## 安装与官方依据

已安装 `@amap/amap-jsapi-loader@1.0.1`（本次查询 npm latest）。通过官方 Loader 异步加载 **JS API 2.0**，2D、`amap://styles/normal` 标准浅色底图，插件仅 `AMap.Scale`，使用 Map、Marker、Polyline、InfoWindow、Pixel；缩放、回到路线由共用工具栏实现。地图页面才动态导入组件；SDK Promise 在同一个浏览器页面内共用。MapLibre 文件保留为备用，正式入口不导入它，构建中不再包含原 1 MB 引擎块。

核对的官方资料：

- https://developer.amap.com/api/javascript-api-v2/guide/abc/amap-vue （页面更新时间 2025-04-27）
- https://developer.amap.com/api/javascript-api-v2/guide/abc/jscode （2025-06-18）
- https://developer.amap.com/api/javascript-api-v2/guide/services/navigation （2024-07-05）
- https://lbs.amap.com/pages/base_service_price （账户类型、服务类别和月配额说明；实际以控制台为准）

Vue 教程中的浏览器明文安全密钥示例未采用。安全文档要求在 SDK 加载前设置 serviceHost，并保留固定的 `/_AMapService` 路径；本项目使用同源绝对地址，不加尾斜线。

## 本机配置

根目录 `.env.local`（浏览器公开配置；必须使用新申请的 Web JS API Key）：

```dotenv
VITE_AMAP_JS_KEY=replace_with_new_web_js_key
VITE_AMAP_SERVICE_HOST=/_AMapService
```

根目录 `.env.amap-server.local`（只在 Node 开发服务中加载）：

```dotenv
AMAP_SECURITY_JSCODE=replace_with_new_server_only_security_code
```

可复制 `.env.example` 和 `.env.amap-server.example`。两个 `.local` 文件均被 Git 忽略；前者仅含公开 Web Key，后者绝不进入 VITE_ 变量、组件、HTML、客户端响应或浏览器请求参数。Node 只在 `vite serve` 时把私有文件加载为进程环境变量，构建时不读取该私有文件。检测到高德安全密钥被配置为 VITE_ 变量时立即停止。

优先使用团队安全代理时，在服务端配置文件设置 `AMAP_PROXY_TARGET=https://backend.example.com`，只允许 HTTPS 源站，不带用户名、密码、路径或查询；此时由团队服务持有安全密钥，本地服务器只做同源转发。它需要实现下述固定前缀和状态探针。

```powershell
npm ci
npm run dev -- --port 5175 --strictPort
```

打开 http://127.0.0.1:5175 。**填好或修改私有服务端配置后完全重启开发进程**。若旧 Node 进程已加载空变量，配置热更新不足以覆盖原值。

到高德控制台为 Web Key 配置当前域名/来源限制；正式域名和开发来源分别核对。不要从历史聊天、日志、文档或 Git 提取旧凭据。本次仅使用当前项目本地新配置，没有修改其值，也未提交它们。

## 本地安全代理

`server/amapProxy.ts` 是仅开发环境的 Vite 中间件，服务监听 `127.0.0.1`，代理拒绝非回环来源。

- `GET /_AMapService/_status`：只返回 `{configured, mode}`，无 Key 或安全密钥。客户端先检查此探针，再下载 SDK；配置存在不等于高德鉴权或地图瓦片已成功。
- `/v4/map/styles` 对应固定 `https://webapi.amap.com/v4/map/styles`。
- 其余仅允许 `/v3/`、`/v4/`、`/v5/` API 路径族中由字母、数字、下划线、连字符和斜线组成的路径，对应固定 `https://restapi.amap.com`。
- 仅 GET；拒绝客户端 jscode、路径穿越和编码分隔符，不接受任意目标 URL，不跟随上游重定向。
- 安全密钥只追加在服务器向高德发起的请求中；不转发业务 JWT、Cookie、Authorization，不输出请求 URL 或原始错误；屏蔽包含安全密钥的上游响应，限制响应体大小与超时。
- 该中间件不是 FastAPI 新接口，也不是生产服务；没有修改队友后端。

## 生产部署

Vite 的开发中间件不会进入 `dist/`，`vite preview` 也不提供它。必须由后端或 Nginx 正式实现同源 `/_AMapService`，并提供上述无敏感值的 `_status` 探针（就绪时 `configured:true`）。未提供探针时，前端明确提示鉴权未配置，不切换到浏览器明文安全密钥方式。

按官方安全文档部署两个上游映射，私有凭据由服务器配置系统/部署流程注入。Nginx 配置的核心关系如下，**仅为待运维补全并验证的模板**：

```nginx
# 此变量由服务器私有配置渲染，不能出现在前端文件中。
set $amap_jscode "__SERVER_RENDERED_SECRET__";
location = /_AMapService/_status {
  default_type application/json;
  add_header Cache-Control no-store;
  return 200 '{"configured":true}'; # 只能在配置与上游已核验后启用
}
location = /_AMapService/v4/map/styles {
  if ($request_method != GET) { return 405; }
  if ($arg_jscode != "") { return 400; }
  proxy_set_header Cookie "";
  proxy_set_header Authorization "";
  proxy_ssl_server_name on;
  set $args "$args&jscode=$amap_jscode";
  proxy_pass https://webapi.amap.com/v4/map/styles;
}
location ~ ^/_AMapService/(?<amap_path>v[345]/[a-zA-Z0-9_/-]+)$ {
  if ($request_method != GET) { return 405; }
  if ($arg_jscode != "") { return 400; }
  proxy_set_header Cookie "";
  proxy_set_header Authorization "";
  proxy_ssl_server_name on;
  # 变量形式的 proxy_pass 需配置团队受信任的 DNS resolver。
  proxy_pass https://restapi.amap.com/$amap_path?$args&jscode=$amap_jscode;
}
location /_AMapService/ { return 404; }
```

上线前需要补充路径规范化、限流、超时、响应大小限制与错误脱敏，不向浏览器转发上游 Location；访问与错误日志不得记录带 jscode 的上游 URL，不把上游响应中的安全密钥回传。生产模板未在本次部署或验收。

## 组件与数据边界

| 文件 | 职责 |
| --- | --- |
| `src/services/maps/amapLoader.ts` | 公开配置检查、代理探针、加载前 serviceHost、安全错误、共享 SDK Promise |
| `src/components/maps/AmapMap.vue` | 地图实例、销毁、可见性/尺寸观察、视野稳定、信息窗、重试 |
| `src/components/maps/MapView.vue` | 双端共用容器、候选状态、业务详情、展开与键盘焦点 |
| `src/components/maps/MapToolbar.vue` | 缩放、回到当前路线 |
| `src/components/maps/mapMarkers.ts` | 安全 DOM Marker 与信息卡，不拼接业务 HTML |
| `src/components/maps/routeLayers.ts` | 深绿当前线、灰蓝原计划、橙色候选，道路几何与白色外描边 |
| `src/services/maps/mapDataAdapter.ts` | GCJ-02、来源/时间、授权范围、快照/路线/方案版本、停靠序列检查 |
| `server/amapProxy.ts` | 服务端开发安全代理 |
| `src/components/maps/amap.css` | 标准底图外的业务面板、双端与三栏工作台布局 |

2026-11-04 最新 OpenAPI 仍为 1.0，现有 **35 个路径**。已新增 /map/demo、地图状态/节点/道路能力及分页会话历史。src/services/maps/wuhanAdapter.ts 将新版 points/routes 转成独立 MapModel；src/composables/useDeliveryMaps.ts 管理 JWT 数据读取、作用域、取消与候选过期。

地图优先采用 data.center；无业务数据时使用武汉公开演示视野 [114.3,30.59]，不代表真实车队位置。武汉模拟仓库和配送点来自交付包响应，持续注明 SIMULATION；SCHEMATIC 使用虚线，ROAD 使用实线，不把 null 距离或时长变成零。不显示虚构车辆位置，不调用浏览器定位。

实际 plan_id 与 vehicle_id 同时传给候选 /map/demo，校验 plan_version/route_revision/站点顺序。409清除旧预览并刷新业务对象；只有后端ACTIVE才重新作为当前路线读取。地图不改变算法ETA、迟到与硬约束。没有自动调用 Driving/TruckDriving 或道路刷新，真实道路服务尚未验收。

登录页可显式选择“武汉联调本地样例”，坐标来自交付包，任务与计算是独立本地规则，绝非实时API结果。旧演示入口保留。当前本机业务后端拒绝连接，交付临时域名DNS失败，不静默回退Mock。

## 验证与截图

```powershell
npm run typecheck
npm run build
npm test
node scripts/verify-amap.mjs
```

业务回归阻止外部 SDK 请求，以保证稳定并控制用量；地图加载成功路径使用标明 TEST DOUBLE 的隔离 SDK 替身。`scripts/verify-amap.mjs` 则完全不拦截 SDK 或网络，用真实浏览器检查 1600、1440、1280、768、390px 的司机、调度、方案页面，截图到 `screenshots/wuhan-20261104/`，网络与交互摘要到 `runtime-verification.json`。该文件不记录查询参数或凭据。两类验证不能互相替代。

首次真实探测已观察到 SDK `/maps`、道路/建筑/POI protobuf 瓦片、图标请求返回 200；截图可见高德浅色底图、道路街区、地名与官方版权。最终五档检查和完整回归结果在本次验证结束后追加。

## 服务用量与权限

用拥有该应用的账号登录 https://console.amap.com/ ，在应用管理找到“运筹助手”及对应 Web Key；到用量详情/配额、账单和服务权限页面核对 JS 地图初始化、路径规划等服务的调用量、剩余额度、QPS 和告警。具体菜单与额度以当前账号控制台显示为准。

官方旧流量限制页面与现行计费页口径可能不同，不能直接套用旧日配额。个人认证、企业认证及许可状态影响月配额与权限。本次没有读取账号控制台，不能确认你的剩余额度或货车服务权限。浏览器只验证地图初始化和基础操作，没有用 Driving 替代后端算法，也没有自动批量请求规划。未来如启用道路预览，应由用户主动触发，对相同坐标序列、路线/方案版本做会话级去重，并单列高德来源与计算时间。

## 当前验证与边界（2026-11-06）

详见 [验证记录](../VALIDATION.md) 与 本地团队对接资料（不随Web仓库上传）。当前真实浏览器截图位于 screenshots/wuhan-20261104/；旧 screenshots/amap/ 仅记录没有业务点位时的历史底图状态，不作为新版验收。

自动化使用显式受控API与SDK替身；实际截图脚本不拦截地图SDK或瓦片。底图加载成功、武汉模拟点线正常，不代表业务后端、真实GPS、真实道路、真实DeepSeek或生产代理已验收。

本轮未修改真实凭据，没有提交或Push。
