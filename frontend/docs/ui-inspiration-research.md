# 运筹助手 UI 灵感调研

当前版本说明：本文保留上一轮设计历史；其中字符矩阵、口号和装饰已在后续精修中撤下，当前实现以 [产品精修记录](product-ui-refinement.md) 为准。

调研日期：2026-11-06。目标是给现有Vue业务系统补足可读性、细节和辨识度，不复制品牌、图片或源码。

## 访问方法与证据边界

通过联网搜索和网页内容提取读取以下公开页面，核对实际标题、说明、组件参数与交互定义。没有登录这些产品的付费后台，没有声称验证其内部性能或用户收益。设计落地是针对运筹助手的原创实现，以下“应用”及“实现”是本项目的设计判断，不是原产品的事实。

用户提到的三张截图本轮没有附加实际图像，因此只依据其文字特点：紧凑任务步骤、局部字符矩阵、真实数据微图表；未声称看过缺失截图。

指定资源访问情况：

- [Mobbin](https://mobbin.com/)：首页可读，说明屏幕/流程库及账户限制；未访问付费screen/flow，不将首页当成已研究多个产品截图。
- [Godly](https://godly.design/)：首页与Gumloop具体作品页可读；作品页列出Desktop/Mobile截图栏目，未把栏目名称当成已播放交互演示。
- [Awwwards](https://www.awwwards.com/)：首页及Grids案例可读；核对元素分类和技术标签，未验证其评分代表本项目可用性。
- [Codrops](https://tympanus.net/codrops/)：首页及终端字效文章可读。
- [Vue Bits](https://vue-bits.dev/)：首页及ASCII Text组件参数/依赖可读；没有安装或复制其组件。
- https://onfleet.com/dispatch 提取返回CRAWL_NOT_FOUND；改读其可用官网首页，不编造不存在页面的内容。

补充浏览器视觉核对：Linear Build返回200并实际查看；Codrops文章在本机浏览器返回403/Just a moment，Vue Bits浏览器加载超时。因此后两项只依据前述成功取得的公开文章/组件文档，不声称已在本机看过完整动效。访问失败不影响原创实现，也不绕过站点限制。

## 案例与具体落地

| 案例 / 实际来源 | 公开资料中可核实的内容 | 本项目采用的位置与实现 | 性能与可用性风险 |
| --- | --- | --- | --- |
| **Linear Cycles / Cycle graph**：[周期](https://linear.app/docs/use-cycles)、[图表](https://linear.app/docs/cycle-graph) | 周期图随实际工作更新；区分scope、started、completed；侧边栏承载细节。文档对各条线的含义有明确解释 | 司机DeliveryProgress分段显示PENDING/ARRIVED/COMPLETED；数字紧靠图形，任务确认才变化 | 不照搬“started算25%完成”的Linear业务规则；物流到达不等于交付完成。无时间序列，不绘制预测趋势 |
| **GitHub Actions visualization graph**：[官方文档](https://docs.github.com/en/actions/monitoring-and-troubleshooting-workflows/monitoring-workflows/using-the-visualization-graph) | job左侧图标区分状态，连接表达依赖，点击job查看日志 | ProcessLedger六步折叠卡：图标、序号、当前步骤、证据展开；使用Vue按钮和aria-expanded，不复制代码 | 不画虚构工具调用图；作业未知/失败不能变成完成。司机确认只在后端ACTIVE时确认整个闭环 |
| **Stripe Web Dashboard**：[官方说明](https://docs.stripe.com/dashboard/basics) | 首页同时呈现业务图表与需要处理的通知；交易列表支持筛选与详情；可调整overview内容 | 调度CasePulse状态带优先提示下一步；RiskRibbon短条与订单、分钟数直接对应，点击联动地图 | 不照搬财务KPI；无数据不生成趋势、百分比或热力图。统计只基于当前授权列表，不冒充全平台总量 |
| **Raycast AI**：[产品页](https://www.raycast.com/core-features/ai) | Quick AI面向小任务；Projects提供专属上下文；常用提示可快速调用 | 助手以当前订单/事件为上下文，欢迎区提供两种草稿入口；按钮只填入编辑框，不自动提交 | 不实现不在契约内的自动化或客户端模型密钥。预填文本不包含虚构分钟数；用户补充后主动发送 |
| **Intercom Fin**：[官方产品](https://www.intercom.com/fin)、[Linear数据接线](https://www.intercom.com/integrations/connectors/linear) | 公开说明强调业务系统数据和对话交接时保留上下文 | 助手将消息、后端RunResult业务卡和已保存异常分层；运行模式说明始终可查 | 不采用其宣传的解决率/客户数/模型性能；不展示私有推理链。Direct AI真实模型未验收的说明保留 |
| **Onfleet Dispatch / delivery platform**：[官网](https://onfleet.com/) | 公开页面说明从dispatch到delivery的可见性，区分司机App、调度、路线及交付反馈 | 保留地图中央工作区和任务列表双向选择，司机桌面信息+地图；视觉改造不改变路线引擎 | 不借用其GPS实时跟踪能力；本项目没有GPS坐标，不画移动车辆。SCHEMATIC仍为明确标注的虚线 |
| **Codrops Terminal-like typography**：[文章](https://tympanus.net/codrops/2024/06/19/hover-animations-for-terminal-like-typography/) | 文章展示字符shuffle、cursor和背景元素hover的几种方法，并说明灵感来源 | 助手空状态原创AsciiSignal：确定性10×32字符、低对比度、有限次数opacity/2px平移；按钮可暂停 | 不复制演示源码或影响业务文字；不打乱订单号。动画不表示模型运行，聊天后组件移除，减少动画时静态 |
| **Vue Bits ASCII Text**：[组件文档](https://vue-bits.dev/text-animations/ascii-text) | 参数包含enableWaves、asciiFontSize、planeBaseHeight等；依赖明确为three | 只吸收“字符纹理可控”的方法，采用纯DOM/CSS原创矩阵，不安装组件 | **放弃其3D实现**：业务侧栏不需要WebGL/Three.js，避免与高德争用图形资源；不引入其源码及许可证义务 |
| **Grids by Obys / Awwwards**：[具体案例](https://www.awwwards.com/sites/grids) | 页面列出黑/白/黄配色、Horizontal Layout、Minimal、Storytelling及导航/加载等元素分类 | 用细分隔线、少量序号、Editorial标题与数字排版建立层次；沿用森林绿品牌而非复制该作品配色 | 不采用crazy mode、横向滚动阅读和长加载动画，避免订单操作障碍。公开案例元数据不能证明其内部可用性 |
| **Gumloop / Godly**：[作品页](https://godly.design/website/gumloop)、[官网](https://www.gumloop.com/) | Godly收录AI产品并列出桌面/手机栏目，官网说明自然语言与业务工具相连、审计与权限 | 借鉴“AI嵌入真实业务”的产品组织方式：欢迎草稿—补问—已保存事实—方案入口，而非另做聊天网站 | 不复制完整agent builder，不增加无关AI功能；只展示已有API支持的事实 |

补充阅读：[Vercel Observability](https://vercel.com/docs/observability)公开区分错误、trace和metrics。仅采用“先明确数据口径再呈现”的信息设计方法，未添加不存在的遥测图表。

## 最终收敛：五项设计方法

1. **事件状态带**：使用授权事件计数和下一步，替代平均占据屏幕的KPI卡片。
2. **六步处置卡**：细线、序号、状态和可展开依据，不展示虚构执行过程。
3. **安静的助手欢迎区**：原创有限字符动效、可暂停、草稿入口；聊天后停止。
4. **有来源的微图表**：延误条、任务保留分段、风险订单条，数值来自已有服务端对象。
5. **司机执行进度**：分开已到达与已完成，桌面双栏与移动任务优先保持。

统一采用现有Manrope/DM Mono/中文系统字体、森林绿/奶油白/柠檬黄。高德底图完全保持标准浅色。没有下载他人的品牌、图像或组件代码；无新增运行依赖。
