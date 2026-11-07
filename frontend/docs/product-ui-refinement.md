# 全站产品质感精修记录

日期：2026-11-06。本轮在现有Vue工程上直接修改呈现层，保留此前未提交的地图和契约工作，没有提交或推送代码。

## 设计审查与对应修正

实际启动并检查了城市总览、异常工作台、方案比较、司机桌面/手机、协作空状态/聊天/历史，以及异常上报与审批弹窗。修改前截图保存在screenshots/product-refinement/before。

| 页面 | 模板感的具体来源 | 本轮处理 |
| --- | --- | --- |
| 协作面板 | 深绿大头部、黄色星光、字符矩阵、英文眉题、大口号、完整CTA、上下文/结果多重套卡 | 重做为“事件协同”：浅色64px标题栏、普通对话图标、当前上下文、真实相关事件、消息/历史列表、固定输入区；删掉字符组件和欢迎口号 |
| 总览 | 巨大标题与副标题、英文口号、装饰场景按钮、信息入口弱，图片被当成营销主视觉 | 场景桌面高度收至440px；标题改为“当前配送”，保留授权订单/在途车辆/异常的实际计数；突出异常工作台和配送任务入口；移除纯场景缩放与十字装饰 |
| 工作台 | 多个重复标题、状态卡外套卡、事件图标过大、列表与详情文字大小杂乱 | 移除重复眉题与大图标，状态带紧凑显示；以细线、清晰列表和六步证据流程组织详情；地图保持业务联动 |
| 恢复方案 | 口号标题、大深绿横幅、指标卡视觉重复，恢复收益与状态容易混在一起 | 页面标题直接为“恢复方案比较”，路线切换改为轻量工具栏；保留原计划/候选、延误、任务调整、约束与审批，减少外层背景/圆角 |
| 司机 | 英文下一站标签、口号、副标题、装饰大箭头，桌面导航像手机胶囊 | 删除口号与箭头、中文“下一站”，更紧凑的任务卡/时间线与矩形导航；桌面信息+地图双栏，手机任务优先和底部导航不变 |
| 登录/弹窗 | 品牌宣言堆叠、英文提示、巨大圆角与花哨标题 | 登录改为直接账号入口；上报弹窗标题改为“上报配送异常”，统一字段/按钮尺寸、收货时间与审批提示保留 |

## 协作抽屉行为

- 默认沿用收起状态，不自动开始聊天或加载历史。
- 桌面宽度390px；1440px及以上打开时为抽屉预留真实空间，关闭后主区恢复；1280px等中等宽度采用覆盖抽屉，避免把地图和表格挤得过窄。手机/平板为全宽工作区。
- 顶部入口统一为普通对话图标和“事件协同”。删除重复的底部悬浮按钮，避免遮挡审批和地图；司机手机仍有原底部协作入口。
- 打开后焦点进入输入框；关闭或Escape后恢复到入口；不会留下空白占位。退出登录时收起，未登录不渲染抽屉。
- 当前上下文不靠前端猜测。相关事件只读展示；尚未关联时明确提示，可通过已有“关联当前异常”操作继续。
- 历史列表保留分页/恢复，优先展示关联订单/事件，其次会话ID和服务端创建时间；空列表用一句话说明。
- 消息、补问、业务状态、已保存事实和重试均保留。没有把聊天JOB成功改写成异常解决或计划生效。
- 简单草稿以文字链接呈现，只填入输入框，仍由用户主动发送。发送支持原按钮及Ctrl/Command+Enter；减少动画偏好时不使用平滑滚动。
- 运行模式说明保留在可展开详情；本地规则、真实模型未验收、业务SIMULATION等边界没有被美化文案掩盖。

## 文件

主要修改：

- src/components/Assistant.vue：协作布局、历史行、相关事件、焦点与快捷键。
- src/App.vue：协作入口、开合空间、移除悬浮入口、中文工作区与页脚。
- src/views/Workspace.vue：各页文案、总览结构、移除装饰。
- src/views/Login.vue：直接登录文案。
- src/components/maps/MapView.vue：中文地图说明，不改变地图引擎与数据适配。
- src/components/design/CasePulse.vue、ProcessLedger.vue、RiskRibbon.vue、PlanInstrument.vue、DeliveryProgress.vue：清理装饰性文字。
- src/components/design/AsciiSignal.vue：删除；隔离测试fixture也移除引用。
- src/product-refinement.css、src/main.ts：集中管理本轮呈现规则，保留森林绿/奶油白/柠檬黄；按钮与导航不再全部胶囊，列表和业务内容优先细分隔线。
- tests/product-refinement.spec.ts：新增五档协作开合、聊天、历史、焦点恢复及角色切换验证。
- tests/workflows.spec.ts、tests/ui-redesign.spec.ts：按真实新标题和已删除装饰更新选择器/断言，其余业务断言保留。
- scripts/verify-product-refinement.mjs：真实浏览器地图、协作历史及关闭后宽度证据。

本轮没有更改src/api、src/stores、后端、算法或鉴权规则；这些目录中Git已存在的改动属于此前联调任务。没有新增依赖。

## 截图对照

全部为实际Edge截图。业务使用明确选择的武汉本地样例，高德底图真实联网；示意虚线不代表道路或GPS。

| 页面 | 修改前 | 修改后 |
| --- | --- | --- |
| 协作1440 | [前](../screenshots/product-refinement/before/assistant-1440.png) | [后](../screenshots/product-refinement/after/assistant-1440.png) |
| 历史1440 | [前](../screenshots/product-refinement/before/history-1440.png) | [后](../screenshots/product-refinement/after/history-1440.png) |
| 总览1440 | [前](../screenshots/product-refinement/before/overview-1440.png) | [后](../screenshots/product-refinement/after/overview-1440.png) |
| 工作台1440 | [前](../screenshots/product-refinement/before/incidents-1440.png) | [后](../screenshots/product-refinement/after/dispatcher-1440.png) |
| 方案1440 | [前](../screenshots/product-refinement/before/plans-1440.png) | [后](../screenshots/product-refinement/after/plans-1440.png) |
| 司机1440 | [前](../screenshots/product-refinement/before/driver-1440.png) | [后](../screenshots/product-refinement/after/driver-1440.png) |
| 司机390 | [前](../screenshots/product-refinement/before/driver-390.png) | [后](../screenshots/product-refinement/after/driver-390.png) |
| 上报弹窗 | [前](../screenshots/product-refinement/before/report-dialog.png) | [后](../screenshots/product-refinement/after/report-dialog.png) |
| 审批弹窗 | [前](../screenshots/product-refinement/before/approval-dialog.png) | [后](../screenshots/product-refinement/after/approval-dialog.png) |

五档全量截图和手机协作/历史见[截图索引](../screenshots/product-refinement/README.md)。初次1440px地图截图出现底图尚未绘制的中间态，已等待实际地图完成后复拍并查看；不把空背景当成底图成功。

## 验证

- npm run typecheck：通过。
- npm run build：通过。主JS246.96kB / gzip87.12kB，高德懒加载块14.48kB / gzip6.02kB，CSS124.09kB / gzip24.23kB。未新增字体、地图引擎或动画依赖；已移除产品字符组件。
- 完整75项浏览器回归通过（1.9分钟）：原70项 + 新增5项；最后增加只读事件摘要后，五档协作5项再次通过。
- 实际1600/1440/1280/768/390px：司机、调度、方案共15个地图场景SDK和瓦片返回成功，无整页横向溢出；5个协作场景均验证关闭后宽度恢复、历史行可用、无横向溢出。
- 保留JWT、DRIVER/DISPATCHER路由、conversation_id、多轮补问、JOB及子任务、失败/UNKNOWN、历史恢复、幂等重试、版本、审批待接收、ACTIVE和配送完成边界。
- 异常上报与审批弹窗实际打开检查；表单与确认按钮不被协作入口遮挡。手机安全停车提示保留。

## 剩余限制

没有发现本轮待修复的阻断性布局问题。中等宽度采用覆盖抽屉是明确的空间取舍，查看被覆盖区域时需先收起；真实长时间使用、超长中文内容和大规模历史列表还需实际用户试用反馈，当前测试不等于生产可用性研究。

真实后端、真实DeepSeek、车辆GPS和道路几何尚未验收的限制沿用前次联调记录；本轮使用明确的本地样例/受控响应，不声称重新完成真实业务后端验收。高德标准浅色底图、官方版权和武汉SIMULATION标识持续保留。

运行：npm run dev -- --port 5175；测试：npm test；截图：node scripts/verify-product-refinement.mjs。
