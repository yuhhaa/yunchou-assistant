# UI/UX 创意升级实施报告

当前版本说明：本文保留上一轮设计历史；其中字符矩阵、口号和装饰已在后续精修中撤下，当前实现以 [产品精修记录](product-ui-refinement.md) 为准。

日期：2026-11-06。沿用现有 Vue 3 / TypeScript / Vite 工程，在已完成的赛题一业务与高德接入上升级呈现；没有重建工程。改动未提交或推送。

## 调研与设计取舍

完整来源、访问边界和10个案例见[灵感调研](ui-inspiration-research.md)。指定的Mobbin、Godly、Awwwards、Codrops、Vue Bits均实际访问了公开页面。没有声称访问付费后台或缺失的三张截图。

最终采用五项方法：事件状态带、可展开的六步处置卡、助手局部字符欢迎区、有来源的方案/风险微图表、司机执行进度。统一沿用森林绿、奶油白、柠檬黄和现有字体，地图保持标准高德浅色。

放弃：Vue Bits ASCII Text所需Three.js、WebGL字符平面、GSAP依赖、粒子、持续Canvas、全站滚动劫持、打乱业务文字的字符动画、没有数据的热力图/收益百分比，以及把Linear的started计入完成比例的业务规则。原因分别是资源负担、地图竞争、阅读干扰及缺少业务依据。

## 改造前发现与各页变化

| 页面 | 改造前观察 | 已实施 |
| --- | --- | --- |
| 登录与公共导航 | 品牌已成型，但入口与业务页的细线/排版节奏不够统一 | 公共surface/rule/type/motion变量、表单标题与分隔层次、胶囊导航反馈、操作按钮轻微抬升；登录、角色与密码行为不变 |
| 城市总览 | 已有成熟微缩场景，不宜用动效覆盖 | 保留原创城市场景和信息栏，统一导航、字距和助手入口，场景仍明确非地理底图；没有新增假指标 |
| 调度工作台 | 异常与地图有联动，当前处理位置不够突出，原流程只四段 | CasePulse以授权事件分组计数，点击定位该组首个事件；选中事件内侧标线；六步流程按业务证据逐级区分当前/等待/确认/失败/未知 |
| 异常影响 | 主要依赖数据表，跨订单比较需要反复读数字 | RiskRibbon按每个订单的delay_minutes绘制短条，值与单位并列，点击订单继续使用现有地图选择；原数据表保留 |
| 恢复方案 | 地图与指标齐全，但最小扰动概念不够直观 | PlanInstrument展示同一分析版本的逐单延误合计和候选总延误，标签明确各自来源；以每个任务一段显示保留/调整。原metrics、约束、审批和失效提醒保留 |
| 智能助手 | 空状态为常规文案，正式消息和业务结果层次弱 | 原创字符矩阵、可暂停、两类草稿入口；深绿消息与细边业务卡分层。聊天后移除欢迎组件；保留历史会话与运行模式说明。明确“异步作业”与业务结果区别 |
| 司机桌面/移动 | 双栏与地图已可用，但逐站执行缺少紧凑总览 | DeliveryProgress基于RouteStop.status分段；ARRIVED与COMPLETED分色并分别统计。下一站轻量方向记号、时间线选中线；宽屏双栏和移动底部导航保持，异常与回执操作不变 |

## 关键交互与数据依据

- ProcessLedger：真实登记对象确认第一步；有效同事件/版本分析确认第二步；当前版本可用候选确认生成；APPROVED_PENDING_ACK只确认调度审批；司机确认/计划生效仅在后端ACTIVE时确认，不推测回执人数。由当前按钮请求关联的JOB可显示失败/UNKNOWN；不把另一事件的作业套入当前流程。
- 每一步是可键盘操作的button，带aria-expanded/aria-controls；展开只读证据，不触发审批或业务写入。
- RiskRibbon：条长仅为同一分析内delay_minutes/max的显示比例；不标造“风险概率”。数值和原表格保持一致。
- PlanInstrument：只有analysis_id、incident_id、incident_version与VALID分析匹配时绘制延误对照；缺数据时说明缺项。候选条为plan.metrics.total_delay_minutes，原安排条为关联分析中逐单延误合计，不是前端预测恢复后ETA。
- 保留分段是target_routes中未出现在changes内的任务计数；不编造优化百分比。
- DeliveryProgress：段数对应实际任务数，已到达不计完成，取消单独显示；没有按经过时间自动前进。
- AsciiSignal：原创确定性10×32字符，由10个span绘制；CSS opacity与2px transform最多运行3次，不使用JS定时器、Canvas或Three.js。装饰不代表AI在执行，正式聊天后卸载，减少动画偏好为静态，用户可暂停。

JWT、conversation_id、JOB轮询、历史恢复、幂等重试、版本检查、审批、司机回执、ACTIVE与完成状态保留原实现。Workspace仅给原analyze/generate调用添加呈现用作业归属跟踪，没有改变请求或业务规则。地图数据、路线算法和标准底图未修改。

## 修改文件

新增公共组件：

- src/components/design/AsciiSignal.vue
- src/components/design/CasePulse.vue
- src/components/design/ProcessLedger.vue
- src/components/design/RiskRibbon.vue
- src/components/design/PlanInstrument.vue
- src/components/design/DeliveryProgress.vue

集成与样式：src/design-upgrade.css、src/main.ts、src/views/Workspace.vue、src/components/Assistant.vue。Login.vue与App.vue采用公共样式，无需修改鉴权和导航逻辑。

测试/截图：tests/ui-redesign.spec.ts、tests/fixtures/design-harness.html、tests/fixtures/design-harness.ts、scripts/verify-ui-redesign.mjs。组件测试状态仅存在于隔离fixture，不进入产品路由。

文档：本报告、ui-inspiration-research.md、README.md与VALIDATION.md追加入口。前次未提交的地图/契约改动保留，没有覆盖无关工作。

## 前后截图

截图全部来自运行中的Edge。业务为显式“武汉联调本地样例”；地图为未拦截的真实高德SDK和瓦片。没有使用生成图或静态设计图作为实现证据。

| 场景 | 改造前 | 改造后 |
| --- | --- | --- |
| 登录1440 | [before](../screenshots/ui-redesign/before/login-1440.png) | [after](../screenshots/ui-redesign/after/login-1440.png) |
| 总览1440 | [before](../screenshots/ui-redesign/before/overview-1440.png) | [after](../screenshots/ui-redesign/after/overview-1440.png) |
| 调度1440 | [before](../screenshots/ui-redesign/before/dispatcher-1440.png) | [after](../screenshots/ui-redesign/after/dispatcher-1440.png) |
| 方案1440 | [before](../screenshots/ui-redesign/before/plans-1440.png) | [after](../screenshots/ui-redesign/after/plans-1440.png) |
| 助手1440 | [before](../screenshots/ui-redesign/before/assistant-1440.png) | [after](../screenshots/ui-redesign/after/assistant-1440.png) |
| 司机1440 | [before](../screenshots/ui-redesign/before/driver-1440.png) | [after](../screenshots/ui-redesign/after/driver-1440.png) |
| 司机390 | [before](../screenshots/ui-redesign/before/driver-390.png) | [after](../screenshots/ui-redesign/after/driver-390.png) |

补充：[分析风险条与展开流程](../screenshots/ui-redesign/after/dispatcher-analysis-1440.png)、[助手手机](../screenshots/ui-redesign/after/assistant-390.png)、[正式聊天](../screenshots/ui-redesign/after/assistant-chat-390.png)。完整五档索引见[截图目录](../screenshots/ui-redesign/README.md)。

## 响应式、性能与依赖

1600/1440/1280/768/390px均实际运行，司机/调度/方案共15个地图场景：15/15 SDK与瓦片成功，15/15无整页横向溢出；缩放、拖动与回到路线正常。视野切换会取消部分瓦片请求，非取消的地图请求失败0。摘要见screenshots/ui-redesign/after/runtime-verification.json。

新交互通过prefers-reduced-motion测试；手机流程按钮至少44px，保留安全停车提示。实际复核后修正了助手消息内边距、调度头部占高，并修复768px方案页原网格定位导致的审批说明与按钮排布问题，保留足够地图面积。

**无新增npm依赖**，没有复制第三方代码，故无新增依赖许可证。现有Lucide使用ISC，Manrope/DM Mono遵循SIL OFL，Vue使用MIT；高德SDK继续遵循其服务条款并保留版权。

生产构建主JS为249.84kB / gzip88.61kB，相比上一轮238.65/84.77增加11.19/3.84kB；CSS110.51kB / gzip21.76kB；高德懒加载块14.48kB / gzip6.02kB保持。没有引入新的地图引擎、字体、图片或背景网络请求。以上是构建体积与交互检查，不是生产Lighthouse或真实司机场景性能认证。

## 验证与运行

npm run typecheck、npm run build已通过。最终 **70项完整浏览器测试全部通过（1.9分钟）**：原60项回归 + 新增10项设计测试，包含平板审批内容不遮挡。HTML报告位于playwright-report/index.html。

```powershell
npm run dev -- --port 5175
npm test
node scripts/verify-ui-redesign.mjs
```

预览http://127.0.0.1:5175，显式选择“武汉联调本地样例”。真实后端本轮仍未联网验收，真实DeepSeek、道路规划、GPS和生产部署的限制与上轮相同；本次视觉升级不构成这些能力的完成证明。
