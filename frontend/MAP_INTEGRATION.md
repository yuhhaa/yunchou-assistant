# 武汉配送地图集成

更新：2026-11-06。当前正式入口为高德 JS API 2.0 标准浅色底图，旧 SVG 节点图不作为地图回退；MapLibre 保留代码但未加载。

浏览器接口以 2026-11-04 补充包 OpenAPI 1.0 为准，共35路径。地图数据来自 GET /api/v1/map/demo，经现有JWT客户端和独立 wuhanAdapter 适配。SCHEMATIC使用虚线，持续标注武汉模拟配送，不代表实际行驶道路；不伪造GPS、距离或ETA。

司机默认本车地图；调度默认全车队，可筛选车辆。候选同时发送真实 vehicle_id、plan_id 并核对 plan_version，原路线低对比度，候选橙色；失效409移除旧预览。ACTIVE仍只认业务服务。

地图不可用时任务时间线、订单、异常、审批及回执继续可用。没有业务点位时不补造坐标。ROAD实线渲染架构保留，正式道路服务尚待验收，不自动请求规划或刷新。

- 本地团队对接资料（不随Web仓库上传）
- [高德安装、安全代理和运行](docs/amap-integration.md)
- 本地团队对接资料（不随Web仓库上传）
- [验证与截图](VALIDATION.md)

历史截图位于screenshots/upgrade、screenshots/contest1及screenshots/amap，不代表本轮新版地图状态。本轮实际浏览器截图位于screenshots/wuhan-20261104。
