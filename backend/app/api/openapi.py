from fastapi.openapi.utils import get_openapi
from pydantic import JsonValue

from app.outputs import (
    ChatQueuedOutput,
    ContextOutput,
    ConversationListOutput,
    ConversationMessagesOutput,
    DemoMapOutput,
    Envelope,
    GeocodeOutput,
    IncidentOutput,
    IncidentWorkspaceOutput,
    JobOutput,
    MapNodeOutput,
    MapOutput,
    MapQueuedOutput,
    MapRouteOutput,
    PlanOutput,
    QueuedOutput,
    StatusOutput,
    ToolStatusOutput,
)

TOOL_DESCRIPTIONS = {
    "get_delivery_context": (
        "查询授权配送任务",
        "用户查单或报告异常前使用。司机默认本人车辆；调度员须选择订单或车辆。返回不可变快照、完整相关路线与数据来源，不返回路网矩阵。",
    ),
    "upsert_incident": (
        "登记或补充配送异常",
        "明确异常类型、车辆及必要事实后使用；道路延误必须有明确整数分钟数。CREATE 返回201，UPDATE 返回200；保存不等于解决，窗口须人工核实。写入必须复用逻辑幂等键。",
    ),
    "analyze_impact": (
        "提交异常影响分析",
        "须有当前事件版本和未过期快照。202仅代表已接收，保存job_id并通过T05查询；QUEUED/RUNNING时返回处理中，不宣称分析成功。",
    ),
    "generate_recovery_plans": (
        "生成待审批恢复候选",
        "须有匹配当前事件、快照的有效analysis_id。202后保存job_id查询；无可行候选为正常有限搜索结果。只生成候选，调度批准和司机接收走人工接口。",
    ),
    "get_operation_status": (
        "核对事件、计算任务与原请求状态",
        "只查询授权的事件、分析、候选、计算JOB、OPERATION或幂等REQUEST。工具禁止查询CHAT；202后先2秒再5秒轮询，每轮最多8次工具调用（含本工具）。超时先核对原键，不新建异常。",
    ),
}


def install_openapi(app):
    def schema():
        if app.openapi_schema:
            return app.openapi_schema
        document = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = document.setdefault("components", {}).setdefault("schemas", {})

        def response(model, description):
            generated = Envelope[model].model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            components.update(generated.pop("$defs", {}))
            name = Envelope[model].__name__
            components[name] = generated
            return {
                "description": description,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + name}}
                },
            }

        document.setdefault("components", {})["securitySchemes"] = {
            "BrowserLogin": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            "ToolServiceKey": {
                "type": "http",
                "scheme": "bearer",
                "description": "Protected platform service key; independent of browser login.",
            },
            "TaskContext": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Task-Token",
                "description": "Signed 300-second user/task/scope/root context injected by a trusted caller.",
            },
        }
        for path, operations in document["paths"].items():
            for method, operation in operations.items():
                suffix = path.removeprefix("/api/v1/agent-tools").removeprefix("/api/v1")
                output = dict[str, JsonValue]
                if suffix == "/delivery-context":
                    output = ContextOutput
                elif suffix in {
                    "/map",
                    "/map/demo",
                    "/map/nodes/{node_id}",
                    "/map/route",
                    "/map/geocode",
                    "/map/routing/refresh",
                }:
                    output = {
                        "/map": MapOutput,
                        "/map/demo": DemoMapOutput,
                        "/map/nodes/{node_id}": MapNodeOutput,
                        "/map/route": MapRouteOutput,
                        "/map/geocode": GeocodeOutput,
                        "/map/routing/refresh": MapQueuedOutput,
                    }[suffix]
                elif suffix == "/incidents" and method == "post":
                    output = IncidentOutput
                elif suffix in {"/impact-analyses", "/recovery-plans"}:
                    output = QueuedOutput
                elif suffix == "/status":
                    output = (
                        ToolStatusOutput
                        if path.startswith("/api/v1/agent-tools/")
                        else StatusOutput
                    )
                elif suffix == "/chat":
                    output = ChatQueuedOutput
                elif suffix == "/conversations":
                    output = ConversationListOutput
                elif suffix == "/conversations/{conversation_id}/messages":
                    output = ConversationMessagesOutput
                elif suffix == "/jobs/{job_id}":
                    output = JobOutput
                elif suffix == "/plans/{plan_id}" and method == "get":
                    output = PlanOutput
                elif suffix == "/incidents/{incident_id}" and method == "get":
                    output = IncidentOutput
                elif suffix == "/incidents/{incident_id}/workspace" and method == "get":
                    output = IncidentWorkspaceOutput
                    operation["summary"] = "查看事件的分析与恢复候选"
                    operation["description"] = (
                        "浏览器登录专用；调度员从事件列表进入，司机限本人车辆。返回当前授权事件、分析与候选状态，重新核验过期和依赖版本，不包含他人的作业或CHAT。"
                    )
                accepted = suffix in {
                    "/impact-analyses",
                    "/recovery-plans",
                    "/chat",
                    "/map/routing/refresh",
                }
                operation["responses"] = {
                    str(202 if accepted else 200): response(
                        output,
                        "持久化受理；请查询任务状态。"
                        if accepted
                        else "成功，data包含结构化业务结果。",
                    )
                }
                if suffix == "/incidents" and method == "post":
                    operation["responses"]["201"] = response(output, "异常已保存，尚未代表解决。")
                for code in (401, 403, 404, 409, 422, 426, 429, 500, 503, 504):
                    operation["responses"][str(code)] = response(output, "标准错误，data为null。")
                if path in {"/api/v1/auth/login", "/api/v1/health", "/api/v1/ready"}:
                    continue
                tool = path.startswith("/api/v1/agent-tools/")
                if tool:
                    operation["summary"], operation["description"] = TOOL_DESCRIPTIONS[
                        operation["operationId"]
                    ]
                operation["security"] = (
                    [{"ToolServiceKey": [], "TaskContext": []}] if tool else [{"BrowserLogin": []}]
                )
                headers = operation.setdefault("parameters", [])
                for name in ("X-Request-ID", "X-Trace-ID"):
                    headers.append(
                        {
                            "in": "header",
                            "name": name,
                            "required": False,
                            "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                        }
                    )
                headers.append(
                    {
                        "in": "header",
                        "name": "X-Contract-Version",
                        "required": False,
                        "schema": {"type": "string", "enum": ["1.0"], "default": "1.0"},
                    }
                )
                if method in {"post", "put"} and path != "/api/v1/auth/task-token":
                    headers.append(
                        {
                            "in": "header",
                            "name": "Idempotency-Key",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,256}$"},
                        }
                    )
        app.openapi_schema = document
        return document

    app.openapi = schema
