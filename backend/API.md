# Business API integration

All routes use `/api/v1` and the contract 1.0 envelope:
`code, message, data, error, request_id, operation_id, meta`.
`/openapi.json` documents JSON inputs, exact tool operation IDs, security schemes,
bounded tracing headers, and the required `Idempotency-Key` header on business writes.

## Browser authentication and reads

`POST /auth/login` accepts `{username,password}` and returns
`data.access_token`, `token_type`, `expires_in`, and `user` with
`user_id,username,role,vehicle_id`. Browser requests use `Authorization: Bearer <access_token>`.
Identity, tenant and role always come from the stored user.

| Read | `data` |
| --- | --- |
| `GET /delivery-context?order_no=O001` | Persisted selected-vehicle snapshot with complete route orders, `selected_order_no`, `clock_time`, `source`, and contract context fields. Routing matrix is excluded. |
| `GET /delivery-context?vehicle_id=V01` | Same context; dispatchers explicitly choose an authorized vehicle or order. Drivers default to their current vehicle. |
| `GET /vehicles` | Dispatcher-only `{vehicles:[{vehicle_id,vehicle_no,driver_id,status}]}`. |
| `GET /incidents` | `{incidents:[Incident...]}` within the actor's permissions. |
| `GET /incidents/{incident_id}` | Current Incident structure, including version, state, `can_analyze` and `next_action`. |
| `GET /plans/{plan_id}` | Current Plan, complete saved `target_routes`, required drivers, dependencies and metrics. |
| `GET /notifications` | `{notifications:[{notification_id,event_id,payload,created_at,read_at}]}` for the logged-in user. |
| `GET /jobs/{job_id}` | Own authorized job: `job_id,job_type,job_status,result_kind,result,error,poll_after_seconds`; browser route also supports CHAT. |
| `GET /status?object_type=...&object_id=...` | Shared six-object contract status structure. |

The browser business writes `/incidents`, `/impact-analyses`, and `/recovery-plans`
reuse the same services as the corresponding tool routes. Human writes are the
contract routes `/plans/{id}/approve`, `/plans/{id}/receipts`, `/plans/{id}/reject`,
`/incidents/{id}/verify-window`, `/incidents/{id}/manual-handoff`, and
`/stops/{id}/completion`. Every business write requires a stable idempotency key.
Keys contain 1–256 letters, digits, underscores or hyphens and remain queryable
as T05 REQUEST identifiers. Other public business IDs remain at most 64 characters.

## Tool credentials

Tools use `Authorization: Bearer <tool_service_key>` **and** `X-Task-Token`.
Their operation IDs/scopes are exactly `get_delivery_context`, `upsert_incident`,
`analyze_impact`, `generate_recovery_plans`, and `get_operation_status`.
Only the five contract tool routes are exposed under `/agent-tools`.

Authenticated `POST /auth/task-token` accepts only optional `vehicle_id` and
`order_no` for local testing. It derives the user/tenant, permitted vehicle root
and scopes on the server and returns `task_id,task_token,expires_in,scopes,object_ids`.
A driver without a current vehicle receives an own-user self root, allowing T01
to return an empty context without granting fleet access. Future incident,
analysis, job and plan IDs are authorized through their stored incident/vehicle
roots; they need not exist when the token is issued. Tool T05 never exposes CHAT.
The internal chat gateway can use `app.security.create_task_token` with the
same trusted task ID when renewing its short-lived context.

## Worker integration

`create_app(settings=None, session_factory=None)` exposes:

```python
app.state.settings
app.state.session_factory
app.state.business
app.state.business.run_job(job_id, worker_id=None)
app.state.business.expire_reservations()
```

The equivalent module functions are:

```python
app.services.jobs.run_job(
    job_id, *, session_factory, settings=None, worker_id=None
)  # -> {job_id, job_status}

app.services.lifecycle.expire_reservations(
    *, session_factory
)  # -> list of expired plan IDs
```

Accepted calculation submissions atomically create `Operation`, `Job` and
`OutboxEvent(event_type="JOB_SUBMITTED", payload={job_id,job_type})`. A worker
dispatches IMPACT_ANALYSIS/PLAN_GENERATION to `run_job`; CHAT execution belongs
to the dedicated chat worker; MAP_ROUTING uses the compute queue. Terminal or already-running calculation jobs are
safe no-ops. Claim commits RUNNING, a unique lease owner and incremented attempts;
computation runs with no open session; publication reacquires resource locks,
checks ownership/attempts and all current input versions, then commits the result.
Expired-running-job recovery is deliberately left to the worker integration:
requeue under the job row lock, clear ownership, and advance the attempts fence
before redispatching so an old computation cannot publish.

Schedule `expire_reservations()` periodically. Reads and lifecycle actions also
check expiration. Activation stores in-app notifications atomically and emits
`NOTIFICATION_CREATED` outbox events containing `type,plan_id,status,user_id,event_id`.
The notification already exists; an outbox consumer must not blindly insert it again.

## Lifecycle choices

Approving only reserves affected vehicles; it never changes the current route.
All related drivers must accept before atomic activation. Unchanged routes in a
snapshot acquire no reservation, acknowledgement or route revision. Stored target
routes are revalidated directly; activation does not regenerate another candidate.
Lease expiry/stale checks persist STALE and release reservations even when an
action returns 409. Rejection persists REJECTED and releases the same resources.
Successful approval and rejection of the selected/current workflow advance the incident's independent
coordination epoch; refusal, fact invalidation or expiry of an approved reservation also advances
it. Predecision T03/T04 jobs cannot publish after the temporary reservation has
been released, even if a dispatcher has since reopened the incident. These
transitions never increment the incident's calculation-fact version. Expiration
of an unapproved old candidate on read does not advance the coordination epoch.

Incident.current_calculation_job_id identifies the newest accepted calculation;
only that job can publish aggregate status or current_plan_group_id. Older jobs
may retain successful historical results but cannot replace the current family,
and older failures cannot affect the current owner. Failure of the current job
returns MANUAL_REVIEW even if older jobs remain pending. A new snapshot clears
the prior candidate family; same-snapshot recalculation retains its alternatives
until the owning generation publishes. Rejecting that current family still
fences in-flight calculation. Human approval explicitly selects its family.

Expiry of an unapproved candidate changes incident status only for the current
family while AWAITING_APPROVAL. Rejecting a historical candidate, or an unselected
alternative while another plan is APPROVED_PENDING_ACK, discards only that plan:
the selected approval, AWAITING_ACK state, reservations and valid ACK remain intact.
Fact invalidation releases an affected selected approval and returns its incident
to MANUAL_REVIEW; it does not change the incident calculation-fact version.

Breakdown marks the vehicle UNAVAILABLE and requires manual rescue; T03 rejects
it with INCIDENT_NOT_READY. New ARRIVED writes are blocked on an unavailable
vehicle. An already-arrived delivery may still be recorded COMPLETED without
moving the vehicle. Dispatcher reanalysis may explicitly reopen a road/window
incident in MANUAL_REVIEW and records an audit event; a reported window must
still be verified. Incident status changes and description-only clarifications
do not increment its calculation version.

Business simulation time stays separate from real UTC authentication, candidate,
snapshot and reservation deadlines. All seed output is explicitly SIMULATION.
No database schema creation or demo seeding occurs at startup. SQLite is useful
for local functional tests; PostgreSQL provides the row-lock concurrency semantics.

## Durable chat and direct DeepSeek

`POST /api/v1/chat` accepts strict `message` (trimmed 1..4000), optional
`conversation_id,selected_order_no,incident_id`; browser auth and Idempotency-Key
are required. Its 202 data is `conversation_id,turn_id,job_id,job_type=CHAT,
job_status=QUEUED,poll_after_seconds=2`. One pending/running turn per owned
conversation; new messages return409 RESOURCE_BUSY, original key replays202.
`GET /jobs/{id}` returns typed CHAT RunResult; tools cannot query CHAT directly
or through its REQUEST/OPERATION. CHAT reads recheck current DB state/authority.

`app.services.chat.run_chat(job_id, *, session_factory, settings,
worker_id=None, platform=None)` uses the same durable claim/fencing pattern.
The injected platform is a test/adapter boundary (`invoke(run_input,gateway)`).
The actual adapter protects credentials in `RunInput.trusted_task_context`
(SecretStr fields), never model inputs; direct-provider configuration is in README.
ToolGateway commits logical receipts with business effects and their Operation,
limits calls to8 (including replays/business conflicts), and restores accepted
incident/job references before another model invocation. CREATE has one
semantic identity per accepted user turn; changed wording/facts conflict rather
than create another report. A new explicit user turn permits a new action.
Local mode is explicitly SIMULATION and enqueues real computation jobs.

`Actor` gains optional trailing fields `execution_job_id,execution_attempt,
execution_owner`; existing Task2 callers remain compatible. `create_task_token`
gains optional `execution={job_id,attempt,owner}`. This claim is required for
tokens whose task_id names a persisted ChatTurn. The DB job claim is locked and
checked inside each Business read/write transaction, so an old attempt cannot
write after reclaim even while a replacement is RUNNING. New attempts renew
their token but retain task_id/turn_id/trace_id and saved logical tool keys.
Independently issued narrow local-test tokens still use client idempotency keys.
Protected callbacks preserve the exact Idempotency-Key: same action/body replays,
different action/body conflicts409. Equivalent turn writes under another key
atomically add an OperationAlias referring to the original Operation. REQUEST
resolves either accepted key; OPERATION always names the canonical operation.
Ordinary APIs also check aliases in the same tenant/user namespace. Canonical
operation retention controls aliases, and expired keys never silently reexecute.
`Business.write` adds optional internal `turn_id`; the gateway supplies it, and
the transaction validates it against the signed actor task/execution claim.
`ToolGateway.call` adds optional `idempotency_key` for protected HTTP callbacks.

DeepSeek calls the five functions through the internal ToolGateway. The model sees only authorized business context, current business time, bounded same-selection history and user/tool messages; no trusted credentials or execution identities. Native ordinary Chat Completions uses non-streaming JSON output and thinking disabled. Total model budget is at most45s. The server revalidates all model references against database facts and recovers accepted durable receipts on malformed/provider output. AgentArts invocation/configuration is removed. The protected HTTP callback machinery remains only for legacy five-tool compatibility, not as a required DeepSeek network path.

Owner-only `GET /conversations` and `GET /conversations/{id}/messages` accept limit1..100 (default20), offset0..10000 (default0); next_offset=null is the final page. The former is newest-first, the latter oldest-first. History returned to the model is at most6 preceding turns/12 messages/12000 characters, from the same contiguous business-selection segment. Browser history contains no tool-state credentials or provider reasoning.

`GET /api/v1/incidents/{incident_id}/workspace` is browser-only and returns typed
`incident,analyses,plans`. An authorized dispatcher discovers driver-created
candidates from the incident list without guessing job/plan IDs; drivers are
restricted to their authorized vehicle. Results recheck versions/expiry under
the fleet lock. No jobs or CHAT data are returned; job reads remain owner-only.
The restricted export still contains exactly five tools.

`app.maintenance.maintenance_once(session_factory,publish)` runs reservation
expiry, expired-job reclaim and outbox delivery. `publish(job_type,job_id)` is
the transport boundary. `recover_jobs` never modifies calculation Job.input or
backfills a coordination generation. Every claim increments attempts, and every
reclaim increments it again before redelivery. `app.worker.celery` routes
fleet.chat to chat and fleet.compute to compute; no Celery result backend.

`/api/v1/health` and `/api/v1/ready` are unauthenticated standard envelopes.
Readiness checks migration0004, DB, Redis, secrets and configured adapter;
it makes no outbound model/map call or certification claim. Frozen0004 adds nullable geography, graph provenance/version metadata and expiring version-bound map geometry cache; old coordinates stay null.
Frozen0002 adds only OperationAlias; frozen0003 adds two nullable incident ownership
columns. Existing0001/0002/0003 are unchanged. Upgrade preserves seed, plan/job/operation
records and incident fact versions. Legacy ownership is backfilled only when
unambiguous: a selected approval takes priority, a sole pending family remains
current, and a sole version/coordination-matching pending/running calculation owns
ANALYZING. Ambiguous unapproved families/jobs move to MANUAL_REVIEW with an advanced
coordination fence; a dispatcher must explicitly reopen or approve a valid plan.
No timestamp heuristic or job-input rewrite is used. Multiple selected approvals
on one legacy incident abort migration for manual repair. Stop old API/workers
during upgrade; do not reset or reseed existing data.
Turn receipts use the existing ChatTurn.tool_state JSON, including recovery of
old pending keys whose Operation committed before the tool-state result.
`python -m app.export_openapi` exports the current frontend schema plus
`agent-tools.openapi.json`, restricted to the five exact tool operationIds.
# 地图增量接口（GCJ02）

2026-11-04：新增浏览器登录 `GET /api/v1/map/demo?vehicle_id=...&plan_id=...`。
武汉用户授权模拟点集WUHAN_DEMO_V1，source=SIMULATION、simulation=true、
road_service_used=false。省略vehicle_id时司机看本人、调度看车队；候选预览必须同时提供vehicle_id与plan_id，沿用当前方案权限及过期检查。
示意几何geometry_kind=SCHEMATIC，distance_m/duration_seconds=null；不写入真实坐标或发布计算图。
详细字段及前端高德适配见 `docs/前端联调逐项回复与武汉地图说明_20261104.md` 和最新完整OpenAPI。

新增浏览器登录接口，统一使用契约1.0信封。坐标经度在前，严格有限数字；几何为明确标注GCJ02的GeoJSON-shaped结构，不是RFC7946/WGS84。无真实点位/Key时明确缺项，不生成随机点或直线道路。

- `GET /api/v1/map?vehicle_id=V01`：司机默认本人，调度可选车辆或全车队；返回配置状态、时钟、当前计算路网来源/版本/就绪状态、授权节点与车辆，车辆位置依据为`NODE`，不是GPS。
- `PUT /api/v1/map/nodes/{node_id}`：调度专用，要求`Idempotency-Key`。JSON `{expected_node_version:1,longitude:116.4,latitude:39.9,coordinate_system:"GCJ02",name?:string,address?:string}`；版本>=1，name/address非空且分别最多128/200字符。以上坐标仅为格式例子，非项目真实点位。返回点位、版本、`DISPATCHER_CONFIRMED`来源；更新清理缓存，已启用AMAP矩阵失效，原模拟基准保持存档。
- `GET /api/v1/map/geocode?address=...&city=...`：调度专用，address 1..200/city 1..64字符，返回候选，需人工选定后调用PUT，不自动落库。
- `GET /api/v1/map/route?vehicle_id=V01&plan_id=...`：plan_id可选；按当前位置及未完成站点顺序查询道路，候选只供预览。响应包含AMAP来源、GCJ02、`DRIVING_CAR`（非货车合规承诺）、距离米/时长秒、LineString或无路段时null、取得时间、节点/车辆/路线/方案/图版本和是否缓存。缺坐标422、未配置/服务不可用503、版本变化409。
- `POST /api/v1/map/routing/refresh`：调度、`Idempotency-Key`，JSON `{expected_routing_version:"R1",node_ids?:["W",...]}`。可选集合必须完整覆盖租户节点；最多21点，60秒总预算。返回202与`MAP_ROUTING` job_id；通过浏览器`/jobs/{id}`或`/status?object_type=JOB&object_id=...`轮询。完整有向矩阵成功后一次发布并使旧候选失效，失败不改变现有图。分钟为道路秒数向上取整。既有SIMULATION基准独立保存；显式刷新后计算使用AMAP版本。T05不提供无事件根的地图作业，仍只保留五个模型工具。

后端配置：`AMAP_WEB_SERVICE_KEY`为私有高德Web服务Key；`AMAP_REQUEST_TIMEOUT_SECONDS`默认10（1..30），`AMAP_CACHE_TTL_SECONDS`默认300（1..3600），`AMAP_TOTAL_BUDGET_SECONDS`默认60（1..60）。固定HTTPS高德主机，关闭重定向/环境代理。地图缓存绑定权限和业务版本、到期失效。实际高德调用验收需真实Key及确认点位。
