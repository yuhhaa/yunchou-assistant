# 运筹助手后端（契约 1.0）

FastAPI + PostgreSQL + Redis/Celery，直接接入 DeepSeek 与高德 Web 服务。默认聊天为 local 规则演示，种子路网为 SIMULATION；真实调用需要私有 Key 与实际点位。数据库保存业务事实，候选必须经调度批准及所有相关司机接受才激活。本版不再依赖 AgentArts。

## Windows / Docker 启动

在仓库根目录运行，需 Docker Desktop（Linux 容器）。初始化脚本支持 Windows PowerShell 5.1 和 PowerShell 7，生成随机凭据且拒绝覆盖已有文件，不输出凭据值。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File backend/scripts/init-env.ps1
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build --wait
docker compose --env-file .env exec -T api python -m app.seed
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
```

首次启动会由独立 `migrate` 容器执行 `alembic upgrade head`，然后启动 API、独立的 compute/chat worker 和维护器。**种子仅由上述显式命令创建**；启动 API 不建表、不导入种子、不重置已有记录。重复种子命令会拒绝覆盖。`/api/v1/health` 只检查进程；`/api/v1/ready` 检查迁移版本、数据库、Redis、密钥及当前模型模式配置完整性，不主动调用收费服务，不代替真实模型和地图联调。

入口 [Swagger](http://127.0.0.1:8000/docs)。API 默认仅发布到本机，可通过 `.env` 的 `API_HOST`、`API_PORT` 修改。PostgreSQL 和 Redis 无宿主机端口。容器无固定名称，可用 `docker compose -p other-demo ...` 启动独立项目；每个项目使用自己的持久化卷。应用 UID/GID 为 `10001`。

登录用户名为 `dispatcher`、`driver01` 至 `driver05`。密码分别保存在根目录私有 `.env` 的 `DEMO_DISPATCHER_PASSWORD` 和 `DEMO_DRIVER_PASSWORD`；不要把文件提交或粘贴到日志。签名密钥、工具服务密钥和模型及地图访问凭据相互独立。`.env.example` 是字段模板，不含可用默认密码。

停止保留数据：`docker compose --env-file .env down`。再次启动使用相同 `.env` 和项目名。需要**明确删除整个演示数据库与队列**时才运行 `docker compose --env-file .env down --volumes`，再启动并显式导入种子；普通演示不要执行此命令。初始迁移面向空数据库，已有手工建表库须先备份并核对迁移，不直接 `stamp` 掩盖差异。

## 登录、工具凭据与固定基准

以下 PowerShell 从根目录执行，凭据保留在变量中，不打印访问令牌：

```powershell
$demoEnv = @{}
Get-Content .env | ForEach-Object {
  if ($_ -match '^([A-Z_]+)=(.*)$') { $demoEnv[$matches[1]] = $matches[2] }
}
$base = 'http://127.0.0.1:8000/api/v1'
$loginBody = @{username='driver01'; password=$demoEnv['DEMO_DRIVER_PASSWORD']} | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType 'application/json' -Body $loginBody
$browserHeaders = @{Authorization="Bearer $($login.data.access_token)"}
$task = Invoke-RestMethod -Method Post -Uri "$base/auth/task-token" -Headers $browserHeaders -ContentType 'application/json' -Body '{"order_no":"O001"}'
$toolHeaders = @{
  Authorization="Bearer $($demoEnv['TOOL_SERVICE_KEY'])"
  'X-Task-Token'=$task.data.task_token
  'X-Contract-Version'='1.0'
  'X-Trace-ID'=[guid]::NewGuid().ToString('N')
}
$context = Invoke-RestMethod "$base/agent-tools/delivery-context?order_no=O001" -Headers $toolHeaders
$context.data | Select-Object snapshot_id, selected_order_no, source
```

工具只包含 T01 查询、T02 登记/补充异常、T03 分析、T04 候选、T05 状态。服务认证与 `X-Task-Token` 必须同时存在，任务身份从已登录用户派生；模型不能提供身份、租户或角色。浏览器不应获得工具密钥。T02–T04、聊天及人工写入都要求 `Idempotency-Key`，同键同内容重放，改变内容返回 409；写入失败或 HTTP 超时后先查原 REQUEST/JOB，不创建新报告。

完整固定基准脚本通过**实际 HTTP、后台队列和数据库**验证：O002 原路线迟到 15 分钟，BAC 迟到 0，变动 2 单、换车 0、反转 1、总距离 20000 米，最后调度批准并司机接收达到 ACTIVE。脚本读取私有环境文件但只输出测试结论。须对新种子运行一次；已执行过的数据库请使用独立 Compose 项目或明确重置演示数据。

```powershell
cd backend
uv run python scripts/demo.py --env-file ../.env --chat
cd ..
```

`--chat` 还使用 driver02 演示三轮：`预计延误20分钟` → 分析任务；分析结束后 `继续` → 候选任务；计算结束后 `查看结果` → WAITING_APPROVAL。本地模拟器只处理明确的整数延误分钟数、当前订单和已有事件，不声称具备真实模型理解能力。消息缺少事实会补问；其他异常仍可通过业务 API 登记并走人工核实。聊天 `current_time` 与演示业务时钟一致；鉴权、租约、快照过期使用真实 UTC 时钟。

`POST /api/v1/chat` 返回 202 的 `conversation_id,turn_id,job_id`；202 只表示受理。前端对 `/api/v1/jobs/{id}` 首次 2 秒、后续 5 秒查询。CHAT 的 `result_kind=CHAT`，结果严格采用 RunResult。一个会话只允许一个 QUEUED/RUNNING 轮次；其他用户的会话返回 404，新输入遇忙返回 409。T05 不能读取 CHAT，包括经 OPERATION/REQUEST 间接查询。

## 可靠执行与超时

接受业务写入时，Operation、Job、outbox 在同一事务提交。维护器每 2 秒扫描，投递失败指数退避至 60 秒；已投递却仍排队的任务 15 秒后重新投递。RUNNING 租约过期后推进尝试号并重新排队；旧 worker 的结果不能覆盖新尝试。Redis 开启 AOF、`noeviction` 和持久卷，但业务恢复依赖 PostgreSQL。计算和聊天分队列、分 worker，模型等待不会占用计算 worker。

每轮工具最多调用 8 次（含状态查询、重放和业务冲突），计数保存在数据库。每个已接受用户轮次只有一个 CREATE 报告身份，独立于描述文字、HTTP 请求键和 worker 尝试号；同内容重放原结果，同轮次改写 CREATE 返回 409，需要核实或新的明确用户轮次。其他工具按动作和规范化参数保存独立回执。业务结果、Operation、轮次回执及请求键别名在同一事务提交；恢复时先找回已接受的事件与 job_ids，再进行模型调用。兼容旧 0001 的仅保存键、尚未补写工具结果的恢复状态。

HTTP `Idempotency-Key` 原值始终保留：同键同动作同内容重放，`meta.replayed=true`；同键改变内容或动作返回 409。同一轮次的相同写入使用另一个键时，仅登记指向原 Operation 的别名，不执行第二次写入；T05 REQUEST 可用任一已接受的原键查到同一个 OPERATION。别名与普通 API 共用企业+用户的键空间，过期记录不自动重做。turn_id、trace_id、task_id 在重试中不变；每次执行重新签发含 job_id/attempt/owner 的凭据。写事务内锁定并核对执行尝试及工具预算，旧回调不能越过恢复后的执行权。

固定预算：连接 3 秒、兼容工具读取 10 秒、模型总等待预算 45 秒、计算 60 秒、任务凭据 300 秒、快照/候选最多 300 秒、预留 60 秒。Celery 计算软/硬限时 65/75 秒，聊天 50/60 秒，数据库执行租约稍长以便回收。预留到期由维护器自动释放；无人工等待事务。通知在激活事务内已保存，重复通知事件只确认投递，不再插入通知。

审批、明确拒绝、司机拒收以及已批准预留的过期释放会推进独立协调版本，阻止先前提交的 T03/T04 在人工决定之后覆盖状态；事件事实版本保持不变。调度员随后明确重新提交的计算使用新协调版本，仍可完成。读取未批准旧候选并标记过期，不推进该协调版本。

模型返回的所有业务 ID 都经授权，状态再从数据库核对。前端读取 CHAT 时再次核对当前状态。后端用事实模板构造回复，避免自由文本暗示未核实的 ACTIVE/DONE。模型超时但已有业务写入时，返回已找到的原事件/任务状态；没有可靠输出则失败并保留原 JOB 供查询。

## DeepSeek、地图与新对齐文档

11月4日前端交接与武汉模拟地图增量见 [最新联调补充](../docs/部署与配置.md)。新增 `/api/v1/map/demo`，提供明确标记的武汉模拟点和示意路线，无需地图Web服务Key；真实道路接口保持原有缺项校验。

完整接入请使用 [赛题一前端、AI 与地图对齐文档](../docs/赛题一前端与AI地图对齐文档.md) 和 [完整 OpenAPI](openapi.json)，不再执行 AgentArts 插件导入、工作流发布或云回调配置。

| 私有配置 | 要求 |
| --- | --- |
| `AGENT_MODE` | `local` 为明确规则模拟，`deepseek` 为真实模型；不接受 `agentarts` |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com`，不要追加 `/chat/completions` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash`，以账号实际开通模型名为准 |
| `DEEPSEEK_API_KEY` | 后端私有 API Key |
| `AMAP_WEB_SERVICE_KEY` | 后端私有高德 Web 服务类型 Key，和前端 JS 底图 Key 分开 |
| `AMAP_REQUEST_TIMEOUT_SECONDS` | 默认 10 秒，单请求读取超时 |
| `AMAP_TOTAL_BUDGET_SECONDS` | 默认 60 秒，一次路线或矩阵的总预算 |
| `AMAP_CACHE_TTL_SECONDS` | 默认 300 秒，版本绑定的几何缓存 |
| `CORS_ALLOW_ORIGINS` | 显式源 JSON 数组，如 `["http://localhost:5173"]`，默认 `[]` |

在根目录私有 `.env` 填写，重新创建业务容器后生效。不要把 Key 交给浏览器、写进 URL 或提交 Git。后端使用普通非流式 Chat Completions、原生函数工具、JSON 输出和关闭 thinking；第三方托管 DeepSeek 需另核对参数支持。每轮工具上限 8 次、总模型等待最多 45 秒，受控网关执行工具，模型不能审批或代司机接收。外部网络请求不持有业务事务和车队锁。

新增本人会话列表与消息历史；换订单、事件或车辆不继承旧业务片段。CHAT 完成可能仍为 PROCESSING，子任务结束不自动触发新 AI 轮次，前端通过原 JOB 查询。

地图采用 GCJ02，经度在前，车辆位置来自业务节点，不能标成实时 GPS。调度读取 `/map`，通过 `/map/geocode` 取得候选，人工确认后 `PUT /map/nodes/{node_id}`；`/map/route` 返回真实道路几何。候选预览不能标为当前已激活路线。当前规划为 DRIVING_CAR，不声称货车限行合规。

显式提交 `/map/routing/refresh` 并等待 MAP_ROUTING 成功后，计算才使用真实道路距离/时长矩阵。最多 21 点、完整有向图、60 秒预算；失败不发布部分数据。点位更新使已依赖它的真实图失效，需重新刷新。缺 Key、缺坐标、服务失败或无路可走返回明确状态，不用随机坐标和直线补齐。

华为云外部 RDS PostgreSQL / DCS Redis 部署见 [部署说明](../deploy/huaweicloud/README.md)。实际云资源、HTTPS、CodeArts 使用和比赛证据仍需真实部署留证，参见 [技术路线与验收状态](../docs/赛题一技术路线与验收状态.md)。

调度界面可先 `GET /api/v1/incidents`，再以返回的 incident_id 请求 `GET /api/v1/incidents/{incident_id}/workspace`，获得 `incident,analyses,plans`。该浏览器登录接口让调度员查看司机生成的分析与候选，比较后使用已有批准接口；司机限本人车辆，未授权对象返回 404。读取重新检查版本、快照/候选过期和预留租约，不泄漏创建者的 JOB 或 CHAT；`/jobs/{id}` 仍只允许作业创建者查询。该接口不导入模型工具。

已有数据库执行 `alembic upgrade head` 升级到 0004。0001 至 0003 保持冻结，0004 新增可空地理坐标、版本化路网来源及路线缓存，不自动补坐标；0002 新增请求键别名表，0003 新增事件的当前计算与当前候选组指针，保留种子、事件事实版本、候选、作业和操作记录；不要重置或重新 seed。升级时停止旧 API/worker；readiness 要求 0004，Compose 迁移服务在新 API/worker 启动前执行升级。旧记录能唯一确定归属时回填指针，已批准方案及其预留保持有效；多个未批准候选组或多个有效计算无法确定归属时，事件转入人工处理并隔离旧计算，调度员需重新开启计算或明确批准仍有效的方案。同一旧事件存在多个已批准方案时，迁移会停止，需先人工修复。

读取历史候选过期不会覆盖新计算、新候选组或已批准方案的事件状态，也不会建立新的人工决定。已有方案等待司机确认时，拒绝另一候选只丢弃该候选，保留选中方案、车辆预留和有效确认。拒绝当前流程、司机拒绝、已批准方案租约到期或路线事实导致其失效，仍会转入人工处理并阻止更早提交的计算覆盖决定。

[agent-tools.openapi.json](agent-tools.openapi.json) 仅为历史外部五工具兼容契约，仍有独立服务与任务认证，不用于 DeepSeek 直连导入。完整 OpenAPI 为 3.1。原 AgentArts 环境文档及交付包保留为历史资料，不作为本版接入步骤。

## 开发验证

本机推荐 Python 3.12 和 uv。在 backend 目录：

```powershell
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run python -m app.export_openapi
```

迁移命令 `uv run alembic upgrade head` 从私有环境中的 `DATABASE_URL` 读取数据库地址；显式种子使用 `DEMO_DISPATCHER_PASSWORD`、`DEMO_DRIVER_PASSWORD`，先迁移再 `uv run python -m app.seed`。本机独立运行时还需 `REDIS_URL` 和三个后端密钥；不要复用容器内部 `postgres`/`redis` 主机名作为 Windows 地址。

可选 PostgreSQL 测试读取 `TEST_DATABASE_URL`，每个 fixture 只创建/销毁 UUID 临时 schema；设置隔离的 `TEST_REDIS_URL` 后还运行实际 Celery 双 worker 三轮聊天测试。Redis 必须为专用测试实例或逻辑库，不连接生产队列。SQLite 缺少 PostgreSQL 行锁语义，只作功能测试。

```powershell
# 将私有测试配置读入进程，不打印值；文件行形如 TEST_DATABASE_URL=... 和 TEST_REDIS_URL=...
Get-Content ../.superpowers/sdd/test-runtime.env | ForEach-Object {
  if ($_ -match '^(TEST_DATABASE_URL|TEST_REDIS_URL)=(.+)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
  }
}
uv run pytest tests/test_postgres.py tests/test_lifecycle.py tests/test_idempotency.py -q
```

已观测的验收结果记录在 [后端验收说明](../docs/backend-acceptance.md)。维护器、worker、HTTP 日志只记录追踪号、任务/操作号及状态，不记录认证头、客户消息或完整请求体。

本版验证结果见 [赛题一迁移验证记录](../docs/赛题一迁移验证记录.md)，旧版结果保留在 docs/backend-verification.md。
