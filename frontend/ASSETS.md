本次正式底图由高德 JS API 2.0 在线提供，保留高德标识、版权、审图号及必要署名；没有截图贴图替代地图，也没有复用第三方品牌设计。原有城市场景及旧底图资料见下方历史记录。

# 资产与设计说明

`public/images/city.png` 是本次通过内置 imagegen 生成的原创城市视觉，已复制进工程，不依赖生成缓存目录。仅用作气氛场景，不表示具体城市、真实车辆位置、路线几何或交通状态。

最终生成提示词：

> Create an original premium cinematic miniature model city photograph for a Chinese urban logistics operations web application. Wide landscape 3:2 composition, high oblique aerial view, exquisitely detailed physical architectural scale model, ivory modern midrise buildings, dark forest green trees, winding river and elegant bridge, small distribution warehouse and tiny delivery vans, quiet Nordic art direction, warm soft afternoon sunlight, pale sage ground, realistic tactile matte materials, tilt shift depth, sophisticated editorial photography. City concentrated toward center and right with open darker forest green park space left, no text, no logos, no interface, no lines or route overlays, no blue neon. High detail, photography quality. Save output as project usable image.

品牌符号与 favicon 由本工程使用简单几何形状原创绘制。森林绿、奶油白与柠檬黄来自用户指定 tokens；异常警示使用独立橙棕、错误使用红棕、成功使用语义绿色。

英文字体 Manrope、数字字体 DM Mono 通过 `@fontsource` 包本地托管，遵循 SIL Open Font License；授权文件保留在相应 npm 包内。中文使用设备已有的苹方、微软雅黑或 Noto Sans SC，不下载受限字体。图标来自 Lucide，遵循 ISC License。

`screenshots/` 是真实运行的前端页面截图，画面中的数据均为显式 DEMO 样例。示例指标不构成真实业务优化结果或性能结论。

2026-11-06：武汉地图点位副本来自用户提供的2026-11-04联调补充包响应样例，仅包含模拟仓库/配送点、示意几何和数据元信息。src/mock/data不包含完整交接资料或凭据。screenshots/wuhan-20261104使用联网高德标准底图，保留官方标识和版权；叠加点/虚线为明确标注的模拟配送，不是实际配送地址、道路或GPS。自动化SDK替身仅用于测试，不作为底图素材。
