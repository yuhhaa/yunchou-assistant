# 运筹助手赛题一后端与前端 AI 地图对齐文档

2026年11月2日更新。契约版本1.0，地图增量迁移0004。

本版面向前端、AI接入和后端部署同学。运筹助手已转向“配送异常恢复AI应用”的架构：浏览器调用业务后端，后端直接调用DeepSeek，内部受控函数执行业务工具；地图服务由后端调用高德。AgentArts运行配置、插件导入和平台回调不再是接入步骤。

现有配送任务、异常、影响分析、候选比较、人工审批、司机接收和实际配送回执继续使用。新增会话历史、地图点位、地理编码、真实道路几何与异步路网刷新。真实DeepSeek Key、高德Web服务Key、真实配送点位和云资源尚需实际配置与验收。

## 1 职责与入口

| 对接方 | 负责内容 | 接口和凭据 |
| --- | --- | --- |
| 前端 | 登录、司机与调度页面、聊天、轮询、地图展示、人工操作 | 后端API地址和测试登录账号；不持有模型或地图Web服务Key |
| 后端 | 用户权限、事实、版本、任务、计算、审批、回执、AI工具执行及地图服务调用 | 私有配置中的签名密钥、DeepSeek Key、高德Web服务Key |
| AI接入负责人 | 确认DeepSeek模型开通、API地址、模型名与真实调用效果 | 模型配置交给后端，不再配置AgentArts插件 |
| 地图前端 | 用GCJ02点位和道路几何渲染地图，区分当前与候选路线 | 如使用高德JS底图，前端自行管理对应JS类型Key；不能拿后端Web服务Key替代 |
| 部署负责人 | 计算环境、RDS PostgreSQL、Redis、HTTPS、域名、备份和证据 | 参见deploy/huaweicloud；实际地址由部署后提供 |

本机根地址 `http://127.0.0.1:8000`，业务前缀 `/api/v1`，Swagger `/docs`，完整机器定义 `/openapi.json` 或 `backend/openapi.json`。本机地址不能转发给其他电脑当成可用服务器。旧trycloudflare地址不作为本版默认联调地址；公网入口需重新验证。

代码更新后对照最新完整OpenAPI生成前端类型。旧 `agent-tools.openapi.json` 只用于兼容历史外部五工具，不是本版DeepSeek工具定义文件，也不需要向任何平台导入。

## 2 身份 通用响应与可靠请求

`POST /api/v1/auth/login` 请求 `{ "username":"driver01", "password":"实际测试密码" }`，返回 `access_token`、`token_type`、`expires_in`、`user`。用户字段为 `user_id,username,role,vehicle_id`，角色DRIVER或DISPATCHER。演示账号dispatcher和driver01至driver05，密码由后端单独提供或查看私有.env；新机器生成独立密码。

浏览器业务请求使用 `Authorization: Bearer <access_token>`。业务POST以及地图PUT要求 `Idempotency-Key`；登录和auth/task-token除外。每个明确的新动作一个键，重试沿用同键和原请求体。键为1至256位字母、数字、下划线或连字符，不能用一个键提交不同动作。

建议请求携带 `X-Contract-Version: 1.0`。可选 `X-Request-ID` 和 `X-Trace-ID` 为1至64位字母、数字、下划线或连字符；请求号每次HTTP尝试新生成，同一链路可复用trace。业务ID通常最长64位；REQUEST查询的ID为原幂等键，可长256位。

统一返回：

```json
{
  "code": "OK",
  "message": "OK",
  "data": {},
  "error": null,
  "request_id": "本次请求ID",
  "operation_id": null,
  "meta": {
    "contract_version": "1.0",
    "data_time": "2026-11-02T10:00:00+08:00",
    "replayed": false
  }
}
```

所有示例中的业务ID文字应替换为实际响应ID，不将说明文字作为请求值。JSON整数不能传字符串或布尔值，时间必须含时区，未知输入字段被拒绝；字段单位为分钟、kg、m3、米，地图duration为秒。

HTTP202仅表示已受理；先保存job_id，再查询原任务。查询HTTP200后还要判断 `data.job_status` 和 `data.error`。顶层error代表本次请求错误，JOB内部error代表被查询的任务失败，两者不能混淆。

断网或写入超时先查 `GET /status?object_type=REQUEST&object_id=原键`。返回OPERATION后沿job_id或resource_id继续查询；写操作成功不代表计算完成。没查到时可等待后重查或原键原体恢复，不能立即换键重复新建。重复结果 `meta.replayed=true`，原键保留至少24小时，过期不自动重做。

## 3 前端主流程

### 3.1 司机与调度

1. 登录后查询 `GET /delivery-context?order_no=O001`，取snapshot_id、有效期、车辆route_revision、订单version及所选车辆完整相关路线。调度可用vehicle_id明确选车，司机默认本人车。
2. 结构化上报走 `POST /incidents`，自然语言走 `POST /chat`。道路延误需明确分钟数；窗口变化先人工核实；故障转人工。
3. 影响分析 `POST /impact-analyses`，202后查询 `GET /jobs/{job_id}`；成功且analysis_status=VALID时取analysis_id。
4. `POST /recovery-plans` 使用同一匹配的事件版本、snapshot_id及analysis_id；结果FEASIBLE或NO_FEASIBLE_PLAN_IN_SEARCH。后者是有限搜索没有找到候选，展示原因与人工入口。
5. 调度从 `GET /incidents` 进入 `GET /incidents/{id}/workspace`，获取incident、analyses、plans，比较后批准。不能通过司机job_id绕过任务归属。
6. `POST /plans/{id}/approve` 只预留，返回APPROVED_PENDING_ACK与reservation_expires_at；司机使用最新plan_version提交receipts。全部相关司机接受后才ACTIVE。
7. ACTIVE后刷新上下文，切换实际路线；实际到站、完成分别走completion。每次使用最新route_revision，先ARRIVED再COMPLETED，不能跳过更早未完成站点。

候选比较重点字段：metrics中的迟到、改动订单、换车、逆序、总距离；changes、constraints、target_routes。当前搜索是有界同车重排，已装车货物不自动换车。历史候选可见不代表可继续批准，按最新状态及版本处理。

### 3.2 聊天与历史

`POST /chat` 输入：message必填，conversation_id、selected_order_no、incident_id可选。首轮省略conversation_id，后续沿用返回值；调度新会话选择订单或事件。message去除前后空白后1至4000字符。

```json
{
  "message": "仓库出口堵车，预计延误20分钟",
  "selected_order_no": "O001"
}
```

202 data为conversation_id、turn_id、job_id、job_type=CHAT、job_status=QUEUED、poll_after_seconds=2。一个会话同时只允许一个运行轮次；RESOURCE_BUSY时查询现有任务，原消息重试保持原键和原体。

`GET /jobs/{CHAT任务ID}` 的result为RunResult。CHAT完成仍可能只是PROCESSING；result.job_ids指向计算子JOB。子任务完成不会自动触发下一轮AI，用户可点击“继续”发送新的明确消息，不无限自动循环。

新增历史接口均仅允许本人，调度员也不能读取其他用户会话：

| 接口 | 返回data | 分页 |
| --- | --- | --- |
| GET /conversations | items包含conversation_id、created_at、active_turn_id、selected_order_no、incident_id | limit默认20，1至100；offset默认0，最大10000；next_offset为null表示末页 |
| GET /conversations/{id}/messages | conversation_id及items；每项含turn_id、job_id、status、created_at、user_message、assistant_reply | 同上；每项代表一个用户轮次，按较早到较晚排列 |

未完成或失败轮次的assistant_reply可为null。接口不返回原始工具状态、凭据或模型内部推理。模型仅使用同一业务选择片段的有限历史，更换订单或事件后不沿用旧事件上下文。

### 3.3 轮询与状态

计算JOB首次按2秒，之后遵循poll_after_seconds，通常RUNNING为5秒。页面卸载取消计时器，返回时可按原job_id继续。失败保留job_id、request_id、operation_id，不能以UI超时自行创建重复任务。

| 对象 | 状态 |
| --- | --- |
| JOB | QUEUED、RUNNING、SUCCEEDED、FAILED、UNKNOWN |
| Incident | NEEDS_VERIFICATION、READY_FOR_ANALYSIS、ANALYZING、AWAITING_APPROVAL、AWAITING_ACK、EXECUTING、RESOLVED、MANUAL_REVIEW |
| Plan | PENDING_APPROVAL、APPROVED_PENDING_ACK、ACTIVE、STALE、REJECTED、SUPERSEDED |
| CHAT业务状态 | NEEDS_INFO、PROCESSING、WAITING_APPROVAL、WAITING_ACK、ACTIVE、MANUAL_REVIEW、DONE |

incident_version只随计算事实变化增加；plan_version是方案自身版本；route_revision是实际路线版本；snapshot_id绑定保存的计算输入。四者不能互换。默认快照/候选最长300秒，批准预留最长60秒，期限使用真实时间，独立于SIMULATION业务时钟。

next_action联合对象状态使用：ASK_USER补信息、ANALYZE_IMPACT分析、GENERATE_PLANS生成、REFRESH_CONTEXT刷新、VERIFY_WINDOW核实、POLL轮询、WAIT_DISPATCHER/WAIT_DRIVER等待、MANUAL_REVIEW人工、RETRY_SAME_KEY恢复原请求、LOGIN登录、NONE无自动动作。

## 4 后端到DeepSeek对齐

### 4.1 配置

| 私有配置 | 内容 |
| --- | --- |
| AGENT_MODE | local为明确的本地规则模拟，deepseek为真实模型调用；不再接受agentarts |
| DEEPSEEK_BASE_URL | 默认https://api.deepseek.com，后端附加/chat/completions；不要重复填写该后缀 |
| DEEPSEEK_MODEL | 按实际账号开通模型配置；当前官方示例deepseek-flash，可配置其他已开通名称 |
| DEEPSEEK_API_KEY | 仅后端持有的API Key，不是网页登录口令或聊天订阅凭据 |

真实Key填入根目录私有.env；不交给前端、不写URL或Git仓库。若使用第三方托管的DeepSeek，需确认其端点和所支持参数；本版按DeepSeek官方普通Chat Completions协议实现，不宣称所有兼容代理均已测试。

后端发起非流式 `POST <base_url>/chat/completions`，Authorization为Bearer Key，messages使用system、user、assistant及工具响应。使用普通tools函数协议和JSON对象输出，thinking设为disabled；不依赖beta strict。

### 4.2 模型输入及权限

模型只接收当前业务时间、经授权选择的业务上下文、有限历史、用户消息及授权工具结果。历史最多6个先前轮次、12条消息和12000字符。登录JWT、任务token、工具服务密钥、动作幂等键和内部执行权不进入提示词。

五个内部工具仍为get_delivery_context、upsert_incident、analyze_impact、generate_recovery_plans、get_operation_status。参数按当前严格schema校验，拒绝未知字段及伪造身份。模型不能直接审批、代司机接收、记录实际配送完成或修改地图点位。

模型返回tool_calls后，后端调用内部ToolGateway；服务端根据当前用户与资源根执行，不按模型提供的身份执行。工具响应形状为 `{ok:true,data:...}` 或 `{ok:false,error:{code,next_action,missing_fields}}`。错误不能当成业务成功。

单轮最多8次工具调用，45秒总模型等待预算，连接最长3秒；持续等待交由前端查询原JOB。网络超时、输出不合约或虚构业务ID时，后端优先从持久回执恢复已接受的事件与作业，并重新授权，不能重复创建。

### 4.3 最终输出

模型最终输出严格RunResult JSON：reply、business_state、incident_id、job_ids、plan_ids、next_action、evidence_refs七个字段。incident_id可为null，数组可为空；reply最长12000字符。后端按数据库事实重建状态、回复与证据，前端读取后端JOB，不直接信任模型原文。

```json
{
  "reply": "计算任务已受理，正在处理，请按任务编号查询。",
  "business_state": "PROCESSING",
  "incident_id": "实际事件ID",
  "job_ids": ["实际计算任务ID"],
  "plan_ids": [],
  "next_action": "POLL",
  "evidence_refs": []
}
```

无明确分钟数时应NEEDS_INFO/ASK_USER；已生成候选为WAITING_APPROVAL；批准待回执为WAITING_ACK；只有数据库证实才ACTIVE或DONE。reply不作为独立状态判断条件。

当前没有独立的抽取置信度或延误区间输出字段，不应在前端虚构这些值。异常具体字段从关联Incident读取；任务与证据可以展示，不提供百分比进度或内部思维链。

### 4.4 四类接入示例的验收口径

| 场景 | 预期行为 | 本地验证与真实验收 |
| --- | --- | --- |
| 正常识别 | 用户明确道路延误分钟，查询上下文、保存事件、受理分析并返回原ID | mock验证协议与持久行为；真实语义效果要用实际Key验收 |
| 缺少信息 | 不猜分钟或窗口，返回补问；下一轮根据历史补全事实 | 历史隔离与缺信息路径可测；不把规则模拟称为真实AI |
| 无法确认 | 保持待核实或人工处理，不凭模型文本修改业务状态 | 版本、权限与事实核对由后端约束 |
| 工具或计算失败 | 读取error、保留原REQUEST/JOB，恢复已有写入，不另造成功状态 | HTTP错误、错误输出和回执恢复有针对性测试；真实服务错误另记调用证据 |

以下是格式示例，不是真实模型调用记录。业务引用必须来自实际响应，状态由后端事实核对。

正常受理示例（计算尚未完成）：

```json
{"reply":"计算任务已受理，请查询原任务。","business_state":"PROCESSING","incident_id":"inc_example","job_ids":["job_example"],"plan_ids":[],"next_action":"POLL","evidence_refs":[]}
```

缺少信息示例：

```json
{"reply":"请提供预计延误的明确分钟数。","business_state":"NEEDS_INFO","incident_id":null,"job_ids":[],"plan_ids":[],"next_action":"ASK_USER","evidence_refs":[]}
```

无法自动确认、转人工示例：

```json
{"reply":"该事件需要人工核实后继续。","business_state":"MANUAL_REVIEW","incident_id":"inc_example","job_ids":[],"plan_ids":[],"next_action":"MANUAL_REVIEW","evidence_refs":[]}
```

工具失败时，模型收到的是错误工具消息，例如以下content；它不能据此生成成功证据。浏览器查询失败计算时读取JOB的FAILED和error，保留原job_id。

```json
{"ok":false,"error":{"code":"STALE_SNAPSHOT","next_action":"REFRESH_CONTEXT","missing_fields":[]}}
```

系统提示词由后端维护，核心规则为：先查询授权上下文；道路延误必须有用户明确分钟数；保存事件后再分析；202仅表示排队；同轮不重复轮询；CREATE同用户轮次只登记一次；UPDATE引用当前事件与版本；更换选择不沿用旧事件；工具ok=false表示失败；不得批准、代接收、代完成或宣称未核实成功；最终只输出RunResult JSON、不输出内部思考。完整实际提示词和五工具参数schema随附在 `docs/deepseek-tool-contract.json`，开发入口为 `app/platform.py`，无需向云平台导入。

## 5 地图与真实路网

### 5.1 坐标与数据来源

使用GCJ02，经度在前、纬度在后，严格有限数字。点位含来源、时间与版本；当前种子节点没有自动填入坐标，缺项会明确返回。车辆位置依据为记录的节点，location_basis=NODE，不能在页面标成实时GPS。

地图路线几何是标明GCJ02的GeoJSON-shaped LineString，geometry_standard=GCJ02_GEOJSON_SHAPED，不按WGS84/RFC7946解释。前端选用匹配坐标系的底图或明确转换，避免偏移。当前规划profile为DRIVING_CAR，不代表货车限行/载重道路合规认证。

### 5.2 地图接口

| 接口 | 权限与输入 | 结果与处理 |
| --- | --- | --- |
| GET /map?vehicle_id=V01 | 司机本人车；调度可选车或全车队 | provider、configuration_status、clock、routing来源/版本/就绪、missing_fields、points、vehicles |
| PUT /map/nodes/{node_id} | 调度；写键；expected_node_version、longitude、latitude、coordinate_system=GCJ02，可选name/address | 保存人工确认点位和新version；旧缓存失效，已启用真实路网待刷新 |
| GET /map/geocode?address=...&city=... | 调度；address必填，city可选 | 高德地理编码候选及来源/时刻；人工确认后PUT，不能自动把首候选当真实配送点 |
| GET /map/route?vehicle_id=V01&plan_id=... | 本人授权车或调度；plan_id可选 | 当前路线或候选预览的道路几何、距离、时长、版本和来源；候选预览不等于激活 |
| POST /map/routing/refresh | 调度；写键；expected_routing_version，可选node_ids完整集合 | 202 MAP_ROUTING作业，通过浏览器JOB轮询，成功后发布完整真实有向路网 |

新地图接口使用浏览器登录，不加入模型工具集合。geocode的address最长200、city最长64字符；点位版本>=1，name/address有界且非空。详细请求和响应字段以附录及最新OpenAPI为准。

### 5.3 地图接入顺序

1. 后端私有配置填入AMAP_WEB_SERVICE_KEY。它是高德Web服务类型Key，和前端JS底图Key分开。
2. 调度读取/map查看节点ID、版本和missing_fields；用真实地址地理编码或经确认的GCJ02坐标维护点位，不使用随机坐标。
3. 请求/map/route取得真实道路展示。响应route_kind=CURRENT或CANDIDATE_PREVIEW；显示source和retrieved_at。
4. 若要让确定性求解使用真实道路距离/时长，显式提交/map/routing/refresh，并等MAP_ROUTING成功。只显示真实道路不表示旧模拟计算自动变为真实数据。
5. 成功发布新routing_version后刷新配送快照，重新分析与生成候选；坐标之后再变化时真实图失效，必须重新刷新后计算。

路网刷新覆盖全租户节点，最多21点，60秒总预算；使用真实有向驾车距离和时长，分钟向上取整，不发布半份矩阵。供应商Key、配额或响应失败时保留原图，不把错误当成零距离。种子SIMULATION基准另行保存；明确刷新成功后才切换活动图来源。

默认地图读取超时10秒、缓存最长300秒、总预算60秒，分别可配置AMAP_REQUEST_TIMEOUT_SECONDS、AMAP_CACHE_TTL_SECONDS、AMAP_TOTAL_BUDGET_SECONDS。缓存绑定点位、车辆、路线、候选和路网版本，过期需重取。

未配置Key返回MAP_UNCONFIGURED；无坐标返回DATA_INCOMPLETE与missing_fields；服务失败MAP_UNAVAILABLE、无驾车路线MAP_NO_ROUTE、超时MAP_TIMEOUT、输入变化STALE_MAP或STALE_NODE。禁止用直线或随机坐标作为失败替代。

## 6 前端代理与错误处理

CORS_ALLOW_ORIGINS是显式HTTP(S)源的JSON数组，默认[]；不使用通配符或路径。使用前端开发代理时，浏览器请求同源/api并完整转发到后端，不移除/api前缀。例如Vite proxy的/api指向http://127.0.0.1:8000，changeOrigin=true，前端API基础路径为/api/v1。

| 错误 | 建议处理 |
| --- | --- |
| 401 UNAUTHENTICATED | 重新登录；工具兼容调用另检查独立服务和任务认证 |
| 404 NOT_FOUND | 对象不存在或无权限；不要据此判断他人业务是否存在 |
| 409 IDEMPOTENCY_CONFLICT | 核实原键与参数，不自动换键重做 |
| STALE_SNAPSHOT / STALE_INCIDENT / STALE_PLAN | 刷新对象与快照，再确认新动作 |
| INCIDENT_NOT_READY | 核实窗口、等待调度/司机或转人工 |
| RESOURCE_BUSY | 查询已有作业或预留，不重复提交 |
| MISSING_FIELDS / DATA_INCOMPLETE | 按missing_fields补事实或点位 |
| RATE_LIMITED | 停止本轮工具循环，保留已受理引用 |
| MAP_UNCONFIGURED / MAP_UNAVAILABLE / MAP_TIMEOUT / MAP_NO_ROUTE | 明确显示地图未就绪或错误，不展示假路线 |
| STALE_MAP / STALE_NODE | 获取最新图或节点版本，再提交 |
| DEPENDENCY_UNAVAILABLE / OPERATION_TIMEOUT / INTERNAL_ERROR | 保留请求与作业引用，先核对原结果，再恢复或人工处理 |

一般程序分支使用error.code、next_action、retryable与missing_fields，不按英文message匹配。没有刷新Token公开接口，前端退出清理自己的登录状态。

## 7 初始化 部署与验收

Windows新机器需要Docker Desktop的Linux容器。在仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File backend/scripts/init-env.ps1
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build --wait
docker compose --env-file .env exec -T api python -m app.seed
```

已有环境不重新seed。升级前停止旧业务进程并备份，增量迁移到0004，保留旧业务数据。普通停止 `docker compose --env-file .env down`，保留卷。真实云部署的外部RDS/Redis连接配置见deploy/huaweicloud，不包含真实云账号、域名或证书。

模型Key与地图Key配置后，分别验证真实调用。就绪检查只证明本地依赖与配置就绪，不主动调用收费外部服务，不代替真实验收。

固定仿真基准仍为V01延误20分钟，原ABC的O002迟到15分钟，BAC候选迟到0、改动2单、换车0、逆序1、距离20000米。真实AMAP路网下指标按真实数据重算，不能套用该仿真数值。

联调至少覆盖：正常任务、缺事实补问、道路延误恢复、无可行候选、人工核实、批准待接收、司机拒绝/超时、二次事实变化、地图点位变化、旧计算晚返回、重复请求、越权与断网恢复。

## 8 交付与比赛证据

前端接收本文和最新完整OpenAPI、实际可达地址、单独发放的测试账号。AI负责人接收本文的DeepSeek配置、工具schema与验收口径；不再要求AgentArts工作流、插件ID或云回调工具URL。源码、依赖、迁移、测试及Docker配置另打包交付。

华为云产品接入、实际CodeArts开发记录、云资源与公开部署、正式评分条款映射需独立留证；参见《赛题一技术路线与验收状态》。DeepSeek直连或本地Docker运行不能单独证明已满足赛题所有要求。

协议资料：DeepSeek官方调用说明 https://api-docs.deepseek.com/ ，工具调用 https://api-docs.deepseek.com/guides/tool_calls/ ；高德地理编码 https://developer.amap.com/api/webservice/guide/api/georegeo ，道路规划 https://developer.amap.com/api/webservice/guide/api/newroute 。实际可用模型与配额以账号为准。


## 附录A 浏览器接口目录

以下路径均添加 `/api/v1` 前缀。除health、ready和login外需要登录；业务POST与地图PUT需写入幂等键。表中类型名对应最新OpenAPI组件。

| 方法和路径 | 请求体类型 | 成功data类型 |
| --- | --- | --- |
| GET /health | 无 | 对象（见正文/API.md） |
| GET /ready | 无 | 对象（见正文/API.md） |
| POST /chat | ChatInput | ChatQueuedOutput |
| POST /auth/login | LoginInput | 对象（见正文/API.md） |
| POST /auth/task-token | TaskTokenInput | 对象（见正文/API.md） |
| GET /delivery-context | 无 | ContextOutput |
| GET /incidents | 无 | 对象（见正文/API.md） |
| POST /incidents | IncidentInput | IncidentOutput |
| POST /impact-analyses | ImpactInput | QueuedOutput |
| POST /recovery-plans | PlanGenerationInput | QueuedOutput |
| GET /status | 无 | StatusOutput |
| GET /jobs/{job_id} | 无 | JobOutput |
| GET /conversations | 无 | ConversationListOutput |
| GET /conversations/{conversation_id}/messages | 无 | ConversationMessagesOutput |
| GET /incidents/{incident_id} | 无 | IncidentOutput |
| GET /plans/{plan_id} | 无 | PlanOutput |
| GET /incidents/{incident_id}/workspace | 无 | IncidentWorkspaceOutput |
| GET /notifications | 无 | 对象（见正文/API.md） |
| GET /vehicles | 无 | 对象（见正文/API.md） |
| GET /map | 无 | MapOutput |
| PUT /map/nodes/{node_id} | MapNodeInput | MapNodeOutput |
| GET /map/geocode | 无 | GeocodeOutput |
| GET /map/route | 无 | MapRouteOutput |
| POST /map/routing/refresh | MapRefreshInput | MapQueuedOutput |
| POST /plans/{plan_id}/approve | ApprovalInput | 对象（见正文/API.md） |
| POST /plans/{plan_id}/receipts | ReceiptInput | 对象（见正文/API.md） |
| POST /plans/{plan_id}/reject | RejectionInput | 对象（见正文/API.md） |
| POST /incidents/{incident_id}/verify-window | WindowVerificationInput | 对象（见正文/API.md） |
| POST /incidents/{incident_id}/manual-handoff | HandoffInput | 对象（见正文/API.md） |
| POST /stops/{stop_id}/completion | CompletionInput | 对象（见正文/API.md） |

兼容工具仅保留五项：GET /agent-tools/delivery-context，POST /agent-tools/incidents，POST /agent-tools/impact-analyses，POST /agent-tools/recovery-plans，GET /agent-tools/status。它们使用独立服务与任务认证，浏览器无需调用。

## 附录B 请求字段

表中“是”指结构必填；IncidentInput等还有业务条件约束，见本节末尾。null与省略按各接口语义处理，不能用空字符串代替ID。

### ApprovalInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| expected_plan_version | integer；≥1.0 | 是 |
| expected_incident_version | integer；≥1.0 | 是 |
| snapshot_id | string；ID格式 | 是 |

### ChatInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| message | string；长度≥1,长度≤4000 | 是 |
| conversation_id | string；ID格式 / null | 否 |
| selected_order_no | string；ID格式 / null | 否 |
| incident_id | string；ID格式 / null | 否 |

### CompletionInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| event_type | ARRIVED 、 COMPLETED | 是 |
| expected_route_revision | integer；≥1.0 | 是 |
| occurred_at | string(date-time) | 是 |

### HandoffInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| expected_incident_version | integer；≥1.0 | 是 |
| reason | string；长度≥1,长度≤1000 | 是 |

### ImpactInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| incident_id | string；ID格式 | 是 |
| expected_incident_version | integer；≥1.0 | 是 |
| snapshot_id | string；ID格式 | 是 |

### IncidentInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| mode | CREATE 、 UPDATE | 是 |
| incident_id | string；ID格式 / null | 否 |
| expected_incident_version | integer；≥1.0 / null | 否 |
| event_type | ROAD_DELAY 、 RECEIVING_WINDOW_CHANGE 、 VEHICLE_BREAKDOWN / null | 否 |
| vehicle_id | string；ID格式 / null | 否 |
| order_no | string；ID格式 / null | 否 |
| description | string；长度≥1,长度≤1000 / null | 否 |
| delay_minutes | integer；≥0.0,≤1440.0 / null | 否 |
| reported_window_start | string(date-time) / null | 否 |
| reported_window_end | string(date-time) / null | 否 |
| occurred_at | string(date-time) / null | 否 |

### LoginInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| username | string；长度≥1,长度≤128 | 是 |
| password | string；长度≥1,长度≤256 | 是 |

### MapNodeInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| expected_node_version | integer；≥1.0 | 是 |
| longitude | number；≥-180.0,≤180.0 | 是 |
| latitude | number；≥-90.0,≤90.0 | 是 |
| coordinate_system | GCJ02 | 是 |
| name | string；长度≥1,长度≤128 / null | 否 |
| address | string；长度≥1,长度≤200 / null | 否 |

### MapRefreshInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| expected_routing_version | string；ID格式 | 是 |
| node_ids | array<string；ID格式>；数量≥1,数量≤21 / null | 否 |

### PlanGenerationInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| incident_id | string；ID格式 | 是 |
| expected_incident_version | integer；≥1.0 | 是 |
| snapshot_id | string；ID格式 | 是 |
| analysis_id | string；ID格式 | 是 |
| strategy_profile | MIN_CHANGE_V1 | 否 |

### ReceiptInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| expected_plan_version | integer；≥1.0 | 是 |
| decision | ACCEPTED 、 REJECTED | 是 |
| reason | string；长度≥1,长度≤1000 / null | 否 |

### RejectionInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| expected_plan_version | integer；≥1.0 | 是 |
| reason | string；长度≥1,长度≤1000 | 是 |

### TaskTokenInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| vehicle_id | string；ID格式 / null | 否 |
| order_no | string；ID格式 / null | 否 |

### WindowVerificationInput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| order_no | string；ID格式 | 是 |
| expected_order_version | integer；≥1.0 | 是 |
| expected_incident_version | integer；≥1.0 | 是 |
| window_start | string(date-time) | 是 |
| window_end | string(date-time) | 是 |

IncidentInput：CREATE必须包含event_type、vehicle_id、description，禁止提供incident_id和expected_incident_version；ROAD_DELAY还需明确0至1440整数delay_minutes。窗口变化需order_no及成对reported_window_start/end，开始不晚于结束。UPDATE必须提供incident_id与expected_incident_version，不能更改event_type/vehicle_id/order_no，至少修改一项事实。
WindowVerificationInput只由调度员核实；窗口必须含时区且开始不晚于结束。ReceiptInput拒绝时必须有reason；司机只回执本人被要求确认的计划。CompletionInput按当前路线顺序先ARRIVED后COMPLETED，occurred_at必须含时区，使用最新expected_route_revision。MapRefreshInput省略node_ids表示全租户节点；若提供则必须覆盖完整集合。

## 附录C 主要响应结构

此表覆盖流程与地图的主要结构，嵌套类型、完整枚举和所有条件以同包完整OpenAPI为准。data内object字段不是自动成功标志；JOB还要检查job_status/error。通用Envelope、Meta和错误字段见第2、6节。

### ContextOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| snapshot_id | string；ID格式 | 是 |
| snapshot_version | integer | 是 |
| valid_until | string(date-time) | 是 |
| clock_mode | REALTIME 、 SIMULATION | 是 |
| clock_time | string(date-time) | 是 |
| source | SIMULATION 、 BUSINESS | 是 |
| vehicles | array<VehicleOutput> | 是 |
| orders | array<OrderOutput> | 是 |
| route_stops | array<RouteStopInput> | 是 |
| routing_version | string / null | 是 |
| routing_source | SIMULATION 、 BUSINESS 、 AMAP / null | 否 |
| routing_ready | boolean / null | 否 |
| routing_id | string；ID格式 / null | 否 |
| ruleset_version | string / null | 否 |
| fleet_set_version | string / null | 否 |
| warehouse_node_id | string；ID格式 / null | 否 |
| missing_fields | array<string> | 是 |
| selected_order_no | string；ID格式 / null | 是 |

### IncidentOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| incident_id | string；ID格式 | 是 |
| incident_version | integer | 是 |
| event_type | ROAD_DELAY 、 RECEIVING_WINDOW_CHANGE 、 VEHICLE_BREAKDOWN | 是 |
| vehicle_id | string；ID格式 | 是 |
| order_no | string；ID格式 / null | 是 |
| status | string | 是 |
| description | string | 是 |
| delay_minutes | integer / null | 是 |
| reported_window_start | string(date-time) / null | 是 |
| reported_window_end | string(date-time) / null | 是 |
| reported_at | string(date-time) | 是 |
| occurred_at | string(date-time) / null | 是 |
| can_analyze | boolean | 是 |
| superseded_plan_ids | array<string；ID格式> | 是 |
| next_action | NONE 、 ASK_USER 、 ANALYZE_IMPACT 、 GENERATE_PLANS 、 REFRESH_CONTEXT 、 VERIFY_WINDOW 、 POLL 、 WAIT_DISPATCHER 、 WAIT_DRIVER 、 MANUAL_REVIEW 、 RETRY_SAME_KEY 、 LOGIN | 是 |

### AnalysisOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| analysis_id | string；ID格式 | 是 |
| incident_id | string；ID格式 | 是 |
| incident_version | integer | 是 |
| snapshot_id | string；ID格式 | 是 |
| analysis_status | VALID 、 STALE | 是 |
| risk_order_count | integer | 是 |
| order_impacts | array<OrderImpact> | 是 |
| missing_fields | array<string> | 是 |

### GenerationOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| solution_status | FEASIBLE 、 NO_FEASIBLE_PLAN_IN_SEARCH | 是 |
| search_scope | SearchScope | 是 |
| plans | array<PlanOutput> | 是 |
| infeasible_reasons | array<Violation> | 是 |
| computed_at | string(date-time) | 是 |
| plan_group_id | string；ID格式 | 是 |
| analysis_id | string；ID格式 | 是 |

### PlanOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| plan_id | string；ID格式 | 是 |
| plan_group_id | string；ID格式 | 是 |
| plan_version | integer | 是 |
| incident_id | string；ID格式 | 是 |
| incident_version | integer | 是 |
| snapshot_id | string；ID格式 | 是 |
| analysis_id | string；ID格式 | 是 |
| status | PENDING_APPROVAL 、 APPROVED_PENDING_ACK 、 ACTIVE 、 STALE 、 REJECTED 、 SUPERSEDED | 是 |
| expires_at | string(date-time) | 是 |
| metrics | Metrics | 是 |
| changes | array<Change> | 是 |
| constraints | Constraints | 是 |
| dependencies | array<Dependency> | 是 |
| required_driver_ids | array<string；ID格式> | 是 |
| target_routes | array<RouteStopInput> | 是 |

### JobOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| job_id | string；ID格式 | 是 |
| job_type | CHAT 、 IMPACT_ANALYSIS 、 PLAN_GENERATION 、 MAP_ROUTING | 是 |
| job_status | QUEUED 、 RUNNING 、 SUCCEEDED 、 FAILED 、 UNKNOWN | 是 |
| result_kind | CHAT 、 IMPACT_ANALYSIS 、 PLAN_GENERATION 、 MAP_ROUTING | 是 |
| result | RunResult / AnalysisOutput / GenerationOutput / MapRoutingOutput / null | 是 |
| error | ErrorDetail / null | 是 |
| poll_after_seconds | 2 、 5 | 是 |

### RunResult

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| reply | string；长度≤12000 | 是 |
| business_state | NEEDS_INFO 、 PROCESSING 、 WAITING_APPROVAL 、 WAITING_ACK 、 ACTIVE 、 MANUAL_REVIEW 、 DONE | 是 |
| incident_id | string；ID格式 / null | 是 |
| job_ids | array<string；ID格式> | 是 |
| plan_ids | array<string；ID格式> | 是 |
| next_action | NONE 、 ASK_USER 、 ANALYZE_IMPACT 、 GENERATE_PLANS 、 REFRESH_CONTEXT 、 VERIFY_WINDOW 、 POLL 、 WAIT_DISPATCHER 、 WAIT_DRIVER 、 MANUAL_REVIEW 、 RETRY_SAME_KEY 、 LOGIN | 是 |
| evidence_refs | array<string> | 是 |

### ConversationOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| conversation_id | string；ID格式 | 是 |
| created_at | string(date-time) | 是 |
| active_turn_id | string；ID格式 / null | 是 |
| selected_order_no | string；ID格式 / null | 是 |
| incident_id | string；ID格式 / null | 是 |

### ConversationTurnOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| turn_id | string；ID格式 | 是 |
| job_id | string；ID格式 | 是 |
| status | QUEUED 、 RUNNING 、 SUCCEEDED 、 FAILED 、 UNKNOWN | 是 |
| created_at | string(date-time) | 是 |
| user_message | string | 是 |
| assistant_reply | string / null | 是 |

### MapOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| provider | AMAP | 是 |
| configuration_status | CONFIGURED 、 UNCONFIGURED | 是 |
| coordinate_system | GCJ02 | 是 |
| clock_mode | SIMULATION 、 REALTIME | 是 |
| clock_time | string(date-time) | 是 |
| routing_source | SIMULATION 、 BUSINESS 、 AMAP | 是 |
| routing_version | string | 是 |
| routing_ready | boolean | 是 |
| missing_fields | array<string> | 是 |
| points | array<MapNodeOutput> | 是 |
| vehicles | array<MapVehicleOutput> | 是 |

### MapNodeOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| node_id | string；ID格式 | 是 |
| name | string | 是 |
| node_type | string | 是 |
| longitude | number / null | 是 |
| latitude | number / null | 是 |
| coordinate_system | GCJ02 / null | 是 |
| address | string / null | 是 |
| source | DISPATCHER_CONFIRMED / null | 是 |
| updated_at | string(date-time) / null | 是 |
| version | integer | 是 |

### MapVehicleOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| vehicle_id | string；ID格式 | 是 |
| route_revision | integer | 是 |
| location_node_id | string；ID格式 | 是 |
| location_basis | NODE | 是 |

### GeocodeOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| source | AMAP | 是 |
| coordinate_system | GCJ02 | 是 |
| candidates | array<GeocodeCandidate> | 是 |
| retrieved_at | string(date-time) | 是 |

### GeocodeCandidate

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| longitude | number | 是 |
| latitude | number | 是 |
| formatted_address | string / null | 是 |
| province | string / null | 是 |
| city | string / null | 是 |
| district | string / null | 是 |
| adcode | string / null | 是 |

### MapRouteOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| vehicle_id | string；ID格式 | 是 |
| vehicle_version | integer | 是 |
| route_revision | integer | 是 |
| plan_id | string；ID格式 / null | 是 |
| plan_version | integer / null | 是 |
| routing_version | string | 是 |
| node_versions | object | 是 |
| node_sequence | array<string；ID格式> | 是 |
| distance_m | integer | 是 |
| duration_seconds | integer | 是 |
| geometry | GCJ02LineString / null | 是 |
| source | AMAP | 是 |
| coordinate_system | GCJ02 | 是 |
| geometry_standard | GCJ02_GEOJSON_SHAPED | 是 |
| routing_profile | DRIVING_CAR | 是 |
| route_kind | CURRENT 、 CANDIDATE_PREVIEW | 是 |
| retrieved_at | string(date-time) | 是 |
| cached | boolean | 是 |

### GCJ02LineString

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| type | LineString | 是 |
| coordinates | array<[longitude:number, latitude:number]>；每个点恰好2个数字 | 是 |

### MapQueuedOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| job_id | string；ID格式 | 是 |
| job_type | MAP_ROUTING | 是 |
| job_status | QUEUED | 是 |
| poll_after_seconds | 2 | 是 |

### MapRoutingOutput

| 字段 | 类型和约束 | 必填 |
| --- | --- | --- |
| routing_version | string | 是 |
| source | AMAP | 是 |
| coordinate_system | GCJ02 | 是 |
| routing_profile | DRIVING_CAR | 是 |
| node_versions | object | 是 |
| node_count | integer | 是 |
| published_at | string(date-time) | 是 |


## 附录D 其他响应与角色边界

部分沿用接口的OpenAPI data使用通用对象，具体字段如下。调用方仍应判断对象状态和HTTP错误，不能因data非空就认为计划生效。

| 接口 | data字段 |
| --- | --- |
| auth/login | access_token、token_type、expires_in、user；user包含user_id、username、role、vehicle_id |
| GET vehicles | vehicles数组；每项vehicle_id、vehicle_no、driver_id、status；仅调度可查 |
| GET incidents | incidents数组；元素为IncidentOutput；按当前用户范围过滤 |
| GET notifications | notifications数组；每项notification_id、event_id、payload、created_at、read_at；仅本人 |
| POST plans/{id}/approve | plan_id、plan_version、status、required_driver_ids、reservation_expires_at；只预留 |
| POST plans/{id}/receipts | plan_id、plan_version、status、accepted_driver_ids、pending_driver_ids、active_route_revision；未ACTIVE时revision为null，单车为版本数，多车为车辆到版本映射 |
| POST plans/{id}/reject | plan_id、plan_version、status |
| POST incidents/{id}/verify-window | IncidentOutput |
| POST incidents/{id}/manual-handoff | IncidentOutput |
| POST stops/{id}/completion | stop_id、status、route_revision、order_no、occurred_at |

司机：本人车辆、路线、事件、计划；实际站点回执只由所属司机执行。调度：车队查询、点位维护、地理编码、矩阵刷新、核实窗口、批准和拒绝。聊天、JOB、会话历史与请求操作保持创建者隔离，调度通过事件workspace查看业务成果。两类用户都不能伪造身份字段或越过人工确认。

StatusOutput按object_type为INCIDENT、ANALYSIS、PLAN、JOB、OPERATION、REQUEST解释；REQUEST返回对应OPERATION，再沿引用读取。完整JOB result按result_kind区分IMPACT_ANALYSIS、PLAN_GENERATION、CHAT和MAP_ROUTING；失败时查看JOB error，不能把失败查询的HTTP200当成任务成功。地图刷新没有事件根，因此兼容模型T05不能读取它。
