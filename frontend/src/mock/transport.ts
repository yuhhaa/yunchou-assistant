import wuhanFleet from "./data/wuhan-fleet-map.json" with { type: "json" };
import { ApiError } from "../api/client";
import type {
  Context,
  Incident,
  Plan,
  Analysis,
  WorkspaceData,
  Schema,
} from "../api/types";
// Explicit opt-in fixture transport. Never used as a fallback by the API client.
const clock = "2026-10-26T10:00:00+08:00";
const later = new Date(Date.now() + 300000).toISOString();
const vehicles = ["V01", "V02", "V03", "V04", "V05"].map((vehicle_id, i) => ({
  vehicle_id,
  vehicle_no: `YC-${1001 + i}`,
  version: 1,
  status: "ON_TASK" as const,
  location_node_id: `N${i + 1}`,
  location_time: clock,
  capacity_kg: 1500,
  capacity_m3: 12,
  route_revision: 1,
  driver_id: `D0${i + 1}`,
  facts: {},
}));
const names = [
  "滨江生活中心",
  "青禾社区店",
  "云栖商务园",
  "河畔便利店",
  "南岸配送站",
  "新城生鲜店",
];
const orders = names.map((name, i) => ({
  order_no: `O00${i + 1}`,
  version: 1,
  assigned_vehicle_id: i < 4 ? "V01" : "V02",
  cargo_status: "ON_VEHICLE" as const,
  status: "IN_TRANSIT" as const,
  delivery_node_id: `N${i + 1}`,
  earliest_arrival: clock,
  latest_arrival: `2026-10-26T${i < 2 ? "10" : "11"}:45:00+08:00`,
  window_type: "SOFT" as const,
  service_minutes: 5,
  weight_kg: 80,
  volume_m3: 1,
  priority: i === 1 ? ("HIGH" as const) : ("NORMAL" as const),
  facts: { name },
}));
let context: Context = {
  snapshot_id: "demo_snapshot",
  snapshot_version: 1,
  valid_until: later,
  clock_mode: "SIMULATION",
  clock_time: clock,
  source: "SIMULATION",
  vehicles,
  orders,
  route_stops: orders.map((o, i) => ({
    stop_id: `S${i + 1}`,
    vehicle_id: o.assigned_vehicle_id,
    order_no: o.order_no,
    sequence_no: i + 1,
    node_id: o.delivery_node_id,
    stop_type: "DELIVERY",
    status: "PENDING",
  })),
  routing_version: "demo-v1",
  routing_source: "SIMULATION",
  routing_ready: false,
  routing_id: null,
  ruleset_version: null,
  fleet_set_version: null,
  warehouse_node_id: "WH01",
  missing_fields: [],
  selected_order_no: null,
};
let incidents: Incident[] = [
  {
    incident_id: "INC_DEMO_01",
    incident_version: 1,
    event_type: "ROAD_DELAY",
    vehicle_id: "V01",
    order_no: "O001",
    status: "READY_FOR_ANALYSIS",
    description:
      "滨江路施工拥堵，预计延误 20 分钟。优先保障青禾社区店收货时间窗。",
    delay_minutes: 20,
    reported_window_start: null,
    reported_window_end: null,
    reported_at: clock,
    occurred_at: clock,
    can_analyze: true,
    superseded_plan_ids: [],
    next_action: "ANALYZE_IMPACT",
  },
];
let analyses: Analysis[] = [];
let plans: Plan[] = [];
let serial = 0;
const jobs = new Map<string, unknown>();
const conversations = new Map<
  string,
  {
    stage: "draft" | "registered" | "planned";
    orderNo?: string;
    incidentId?: string;
  }
>();
const initialContext = structuredClone(context);
const initialIncidents = structuredClone(incidents);
let fixtureScenario: "legacy" | "wuhan" = "legacy",
  fixtureRole: "DRIVER" | "DISPATCHER" = "DISPATCHER";
const historyTurns = new Map<string, Schema["ConversationTurnOutput"][]>();
export function resetFixtures() {
  fixtureScenario = "legacy";
  historyTurns.clear();
  context = structuredClone(initialContext);
  incidents = structuredClone(initialIncidents);
  analyses = [];
  plans = [];
  serial = 0;
  jobs.clear();
  conversations.clear();
}
export function configureDemoScenario(
  scenario: "legacy" | "wuhan",
  role: "DRIVER" | "DISPATCHER",
) {
  if (fixtureScenario !== scenario) {
    resetFixtures();
    if (scenario === "wuhan") {
      fixtureScenario = "wuhan";
      context.warehouse_node_id = "W";
      context.vehicles.forEach((v) => {
        v.location_node_id = "";
      });
      context.orders = [];
      context.route_stops = [];
      context.routing_source = "SIMULATION";
      context.routing_ready = false;
      for (const route of wuhanFleet.routes)
        for (const [i, node] of route.node_sequence.slice(1).entries()) {
          const n = context.orders.length + 1,
            order = {
              ...structuredClone(initialContext.orders[0]!),
              order_no: "O" + String(n).padStart(3, "0"),
              assigned_vehicle_id: route.vehicle_id,
              delivery_node_id: node,
              facts: {
                name: wuhanFleet.points.find((p) => p.node_id === node)!.name,
              },
            };
          context.orders.push(order);
          context.route_stops.push({
            stop_id: "S" + n,
            vehicle_id: route.vehicle_id,
            order_no: order.order_no,
            sequence_no: i + 1,
            node_id: node,
            stop_type: "DELIVERY",
            status: "PENDING",
          });
        }
      incidents[0]!.description = "武汉本地模拟：道路拥堵，预计延误20分钟。";
    }
  }
  fixtureRole = role;
}
function localDemoMap(path: string): Schema["DemoMapOutput"] {
  if (fixtureScenario !== "wuhan")
    throw new ApiError(
      "旧业务演示未接入地图样例，请选择武汉联调本地样例",
      404,
      "LOCAL_MAP_NOT_ENABLED",
    );
  const params = new URLSearchParams(path.split("?")[1] || ""),
    requested = params.get("vehicle_id") || "",
    planId = params.get("plan_id") || "";
  if (planId && !requested)
    throw new ApiError("候选必须指定车辆", 422, "VALIDATION_ERROR");
  const vehicleId = fixtureRole === "DRIVER" ? "V01" : requested;
  if (fixtureRole === "DRIVER" && requested && requested !== "V01")
    throw new ApiError("无权查看该车辆", 404, "NOT_FOUND");
  const plan = planId ? plans.find((p) => p.plan_id === planId) : undefined;
  if (
    planId &&
    (!plan ||
      !["PENDING_APPROVAL", "APPROVED_PENDING_ACK"].includes(plan.status) ||
      (plan.status === "PENDING_APPROVAL" &&
        Date.parse(plan.expires_at) <= Date.now()))
  )
    throw new ApiError("候选已失效", 409, "PLAN_STALE");
  const target = plan?.target_routes || context.route_stops;
  const result = structuredClone(wuhanFleet) as Schema["DemoMapOutput"];
  result.routes = result.routes
    .filter((r) => !vehicleId || r.vehicle_id === vehicleId)
    .map((r) => {
      const sequence = [
        "W",
        ...target
          .filter(
            (s) =>
              s.vehicle_id === r.vehicle_id &&
              s.status !== "COMPLETED" &&
              s.status !== "CANCELLED",
          )
          .slice()
          .sort((a, b) => a.sequence_no - b.sequence_no)
          .map((s) => s.node_id),
      ];
      return {
        ...r,
        route_revision: context.vehicles.find(
          (v) => v.vehicle_id === r.vehicle_id,
        )!.route_revision,
        route_kind: plan ? "CANDIDATE_PREVIEW" : "CURRENT",
        plan_id: plan?.plan_id || null,
        plan_version: plan?.plan_version ?? null,
        node_sequence: sequence,
        geometry:
          sequence.length > 1
            ? {
                type: "LineString",
                coordinates: sequence.map((n) => {
                  const point = result.points.find((p) => p.node_id === n);
                  if (!point)
                    throw new ApiError("节点缺失", 409, "DATA_INCOMPLETE");
                  return [point.longitude, point.latitude] as [number, number];
                }),
              }
            : null,
        distance_m: null,
        duration_seconds: null,
      };
    });
  const ids = new Set(result.routes.flatMap((r) => r.node_sequence));
  result.points = result.points.filter((p) => ids.has(p.node_id));
  result.retrieved_at = new Date().toISOString();
  result.notice += " 本地响应样例，不是实时API结果。";
  return result;
}

function analysis(incident: Incident): Analysis {
  return {
    analysis_id: "ANA_DEMO_" + ++serial,
    incident_id: incident.incident_id,
    incident_version: incident.incident_version,
    snapshot_id: context.snapshot_id,
    analysis_status: "VALID",
    risk_order_count: 2,
    order_impacts: context.orders
      .filter((o) => o.assigned_vehicle_id === incident.vehicle_id)
      .map((o, i) => ({
        order_no: o.order_no,
        original_eta: `2026-10-26T${i < 2 ? "10" : "11"}:${i < 2 ? (i === 0 ? "35" : "40") : "10"}:00+08:00`,
        updated_eta: `2026-10-26T${i === 0 ? "10" : "11"}:${i === 0 ? "55" : i === 1 ? "00" : "10"}:00+08:00`,
        latest_arrival: o.latest_arrival,
        delay_minutes: i === 0 ? 10 : i === 1 ? 15 : 0,
        risk_type: i < 2 ? "SOFT_WINDOW_LATE" : "NONE",
        evidence_refs: ["demo_fixture"],
      })),
    missing_fields: [],
  };
}
function candidates(inc: Incident, a: Analysis): Schema["GenerationOutput"] {
  plans = [0, 1].map((n) => {
    const target_routes = context.route_stops.map((s) => ({
      ...s,
      sequence_no:
        s.vehicle_id !== "V01"
          ? s.sequence_no
          : n
            ? s.sequence_no === 1
              ? 2
              : s.sequence_no === 2
                ? 3
                : s.sequence_no === 3
                  ? 1
                  : s.sequence_no
            : s.sequence_no === 1
              ? 2
              : s.sequence_no === 2
                ? 1
                : s.sequence_no,
    }));
    const changes = target_routes
      .filter(
        (s) =>
          context.route_stops.find((o) => o.stop_id === s.stop_id)!
            .sequence_no !== s.sequence_no,
      )
      .map((s) => ({
        change_type: "RESEQUENCE" as const,
        order_no: s.order_no,
        from_vehicle_id: s.vehicle_id,
        to_vehicle_id: s.vehicle_id,
        old_sequence_no: context.route_stops.find(
          (o) => o.stop_id === s.stop_id,
        )!.sequence_no,
        new_sequence_no: s.sequence_no,
      }));
    return {
      plan_id: `PLAN_DEMO_${++serial}`,
      plan_group_id: "GROUP_DEMO",
      plan_version: 1,
      incident_id: inc.incident_id,
      incident_version: inc.incident_version,
      snapshot_id: context.snapshot_id,
      analysis_id: a.analysis_id,
      status: "PENDING_APPROVAL",
      expires_at: new Date(Date.now() + 300000).toISOString(),
      metrics: {
        unresolved_order_count: 0,
        late_order_count: n ? 1 : 0,
        high_priority_delay_minutes: 0,
        total_delay_minutes: n ? 8 : 0,
        changed_order_count: changes.length,
        reassigned_order_count: 0,
        sequence_inversion_count: n ? 2 : 1,
        total_distance_m: n ? 18400 : 17200,
        extra_distance_m: n ? 1400 : 200,
        computation_ms: 0,
      },
      changes,
      constraints: { passed: true, violations: [], checked_at: clock },
      dependencies: [],
      required_driver_ids: ["D01"],
      target_routes,
    };
  });
  return {
    solution_status: "FEASIBLE",
    plans,
    search_scope: {
      vehicle_ids: ["V01"],
      order_nos: context.orders
        .filter((o) => o.assigned_vehicle_id === inc.vehicle_id)
        .map((o) => o.order_no),
      budget_ms: 0,
      method: "BOUNDED_SAME_VEHICLE_RESEQUENCE",
      max_permutations: 0,
    },
    infeasible_reasons: [],
    computed_at: clock,
    plan_group_id: "GROUP_DEMO",
    analysis_id: a.analysis_id,
  };
}
export async function mockRequest<T>(path: string, body?: unknown): Promise<T> {
  const b = body as Record<string, any> | undefined;
  let result: unknown;
  const inc =
    incidents.find(
      (i) => i.incident_id === (b?.incident_id || path.split("/")[2]),
    ) || incidents[0]!;
  if (path.startsWith("/map/demo"))
    return structuredClone(localDemoMap(path)) as T;
  if (path === "/map")
    return {
      provider: "AMAP",
      configuration_status: "UNCONFIGURED",
      coordinate_system: "GCJ02",
      clock_mode: "SIMULATION",
      clock_time: clock,
      routing_source: "SIMULATION",
      routing_version: context.routing_version,
      routing_ready: false,
      missing_fields: ["AMAP_WEB_SERVICE_KEY"],
      points: [],
      vehicles: [],
    } as T;
  if (path.startsWith("/conversations")) {
    const [pathname, query] = path.split("?"),
      params = new URLSearchParams(query || ""),
      offset = Number(params.get("offset") || 0),
      limit = Number(params.get("limit") || 20);
    if (pathname === "/conversations") {
      const items = [...conversations.entries()].map(
        ([conversation_id, c]) => ({
          conversation_id,
          created_at:
            historyTurns.get(conversation_id)?.[0]?.created_at || clock,
          active_turn_id:
            historyTurns.get(conversation_id)?.at(-1)?.turn_id || null,
          selected_order_no: c.orderNo || null,
          incident_id: c.incidentId || null,
        }),
      );
      return {
        items: items.slice(offset, offset + limit),
        next_offset: offset + limit < items.length ? offset + limit : null,
      } as T;
    }
    const cid = decodeURIComponent(pathname!.split("/")[2]!),
      items = historyTurns.get(cid);
    if (!items) throw new ApiError("会话不存在", 404, "NOT_FOUND");
    return {
      conversation_id: cid,
      items: items.slice(offset, offset + limit),
      next_offset: offset + limit < items.length ? offset + limit : null,
    } as T;
  }
  if (path === "/ready")
    return { status: "READY", agent_mode: "local", source: "SIMULATION" } as T;
  if (path.startsWith("/delivery-context")) {
    context.valid_until = new Date(Date.now() + 300000).toISOString();
    const scoped = structuredClone(context);
    const selected = new URLSearchParams(path.split("?")[1] || "").get(
      "vehicle_id",
    );
    if (selected) {
      scoped.vehicles = scoped.vehicles.filter(
        (v) => v.vehicle_id === selected,
      );
      scoped.orders = scoped.orders.filter(
        (o) => o.assigned_vehicle_id === selected,
      );
      scoped.route_stops = scoped.route_stops.filter(
        (s) => s.vehicle_id === selected,
      );
    }
    result = scoped;
  } else if (path === "/incidents" && !body)
    result = { incidents: structuredClone(incidents) };
  else if (path === "/vehicles") result = { vehicles };
  else if (path.endsWith("/workspace"))
    result = {
      incident: structuredClone(inc),
      analyses: structuredClone(
        analyses.filter((a) => a.incident_id === inc.incident_id),
      ),
      plans: structuredClone(
        plans.filter((p) => p.incident_id === inc.incident_id),
      ),
    } satisfies WorkspaceData;
  else if (path.startsWith("/jobs/")) result = jobs.get(path.split("/").pop()!);
  else if (path.startsWith("/status"))
    result = { status: "SUCCEEDED", source: "DEMO", message: "演示操作已记录" };
  else if (path === "/incidents" && b) {
    const created = {
      ...inc,
      ...b,
      incident_id: "INC_DEMO_" + ++serial,
      incident_version: 1,
      status: "READY_FOR_ANALYSIS",
      can_analyze: true,
      next_action: "ANALYZE_IMPACT",
      reported_at: clock,
    } as Incident;
    incidents.unshift(created);
    result = created;
  } else if (
    path === "/impact-analyses" ||
    path === "/recovery-plans" ||
    path === "/chat"
  ) {
    const job_id = "JOB_DEMO_" + ++serial;
    let data: unknown;
    let job_type = "IMPACT_ANALYSIS";
    if (path === "/impact-analyses") {
      const a = analysis(inc);
      analyses.push(a);
      data = a;
    } else if (path === "/recovery-plans") {
      data = candidates(inc, analyses[analyses.length - 1]!);
      job_type = "PLAN_GENERATION";
    } else {
      const conversation = b?.conversation_id || "CONV_DEMO_" + ++serial;
      const draft: {
        stage: "draft" | "registered" | "planned";
        orderNo?: string;
        incidentId?: string;
      } = conversations.get(conversation) || {
        stage: "draft" as const,
        orderNo: b?.selected_order_no,
      };
      const createJob = (kind: string, value: unknown) => {
        const child = "JOB_DEMO_" + ++serial;
        jobs.set(child, {
          job_id: child,
          job_type: kind,
          job_status: "SUCCEEDED",
          result_kind: kind,
          result: value,
          error: null,
          poll_after_seconds: 2,
        });
        return child;
      };
      if (draft.stage === "draft") {
        const minutes = String(b?.message || "").match(/(\d+)\s*分钟/);
        if (!minutes)
          data = {
            reply:
              "独立演示助手：请问预计延误多少分钟？这是本地规则补问示例，不是真实模型调用。",
            business_state: "NEEDS_INFO",
            incident_id: null,
            job_ids: [],
            plan_ids: [],
            next_action: "ASK_USER",
            evidence_refs: [],
          };
        else {
          const saved = {
            ...inc,
            incident_id: "INC_DEMO_" + ++serial,
            incident_version: 1,
            order_no: draft.orderNo || null,
            delay_minutes: Number(minutes[1]),
            description: "独立演示：道路拥堵，预计延误" + minutes[1] + "分钟。",
            status: "READY_FOR_ANALYSIS",
            next_action: "ANALYZE_IMPACT" as const,
          };
          incidents.unshift(saved);
          draft.incidentId = saved.incident_id;
          draft.stage = "registered";
          const computed = analysis(saved);
          analyses.push(computed);
          data = {
            reply:
              "演示业务已登记异常，关联影响分析任务已受理；请查看计算结果后主动继续。",
            business_state: "PROCESSING",
            incident_id: saved.incident_id,
            job_ids: [createJob("IMPACT_ANALYSIS", computed)],
            plan_ids: [],
            next_action: "POLL",
            evidence_refs: [saved.incident_id],
          };
        }
      } else if (draft.stage === "registered") {
        const saved = incidents.find(
          (i) => i.incident_id === draft.incidentId,
        )!;
        const a = analyses.find((a) => a.incident_id === saved.incident_id)!;
        const generated = candidates(saved, a);
        draft.stage = "planned";
        data = {
          reply: "演示候选计算已受理。完成后请查看方案，由调度员决定是否批准。",
          business_state: "PROCESSING",
          incident_id: saved.incident_id,
          job_ids: [createJob("PLAN_GENERATION", generated)],
          plan_ids: generated.plans.map((p) => p.plan_id),
          next_action: "POLL",
          evidence_refs: [a.analysis_id],
        };
      } else {
        data = {
          reply: "演示恢复方案等待调度员批准；本轮聊天完成不代表方案已经生效。",
          business_state: "WAITING_APPROVAL",
          incident_id: draft.incidentId,
          job_ids: [],
          plan_ids: plans
            .filter((p) => p.incident_id === draft.incidentId)
            .map((p) => p.plan_id),
          next_action: "WAIT_DISPATCHER",
          evidence_refs: [],
        };
      }
      conversations.set(conversation, draft);
      job_type = "CHAT";
      result = { conversation_id: conversation };
    }
    if (job_type === "CHAT") {
      const cid = (result as { conversation_id: string }).conversation_id;
      const turns = historyTurns.get(cid) || [];
      turns.push({
        turn_id: "TURN_" + job_id,
        job_id,
        status: "SUCCEEDED",
        created_at: new Date().toISOString(),
        user_message: String(b?.message || ""),
        assistant_reply: (data as Schema["RunResult"]).reply,
      });
      historyTurns.set(cid, turns);
    }
    jobs.set(job_id, {
      job_id,
      job_type,
      job_status: "SUCCEEDED",
      result_kind: job_type,
      result: data,
      error: null,
      poll_after_seconds: 2,
    });
    result = {
      job_id,
      job_type,
      job_status: "QUEUED",
      poll_after_seconds: 2,
      conversation_id:
        (result as { conversation_id?: string })?.conversation_id ||
        "CONV_DEMO",
      turn_id: "TURN_" + job_id,
    };
  } else if (path.startsWith("/plans/")) {
    const p = plans.find((p) => p.plan_id === path.split("/")[2]);
    if (!p) throw Error("演示方案不存在，请先生成候选。");
    const planIncident = incidents.find(
      (i) => i.incident_id === p.incident_id,
    )!;
    if (b && b.expected_plan_version !== p.plan_version)
      throw Error("方案版本变化，请刷新。");
    if (path.endsWith("/approve")) {
      if (
        plans.some(
          (other) =>
            other.plan_id !== p.plan_id &&
            other.incident_id === p.incident_id &&
            ["APPROVED_PENDING_ACK", "ACTIVE"].includes(other.status),
        )
      )
        throw Error("同一事件已有批准或生效的方案。");
      p.status = "APPROVED_PENDING_ACK";
      planIncident.status = "WAITING_ACK";
      planIncident.next_action = "WAIT_DRIVER";
      p.plan_version++;
    }
    if (path.endsWith("/reject")) {
      p.status = "REJECTED";
      p.plan_version++;
    }
    if (path.endsWith("/receipts")) {
      p.status = b?.decision === "ACCEPTED" ? "ACTIVE" : "REJECTED";
      p.plan_version++;
      if (p.status === "ACTIVE") {
        context.route_stops = structuredClone(p.target_routes);
        context.vehicles[0]!.route_revision++;
        planIncident.status = "ACTIVE";
        planIncident.next_action = "NONE";
      }
    }
    result = {
      ...structuredClone(p),
      ...(path.endsWith("/approve")
        ? { reservation_expires_at: new Date(Date.now() + 60000).toISOString() }
        : {}),
    };
  } else if (path.includes("/completion")) {
    const stop = context.route_stops.find(
      (s) => s.stop_id === path.split("/")[2],
    )!;
    stop.status = b?.event_type === "ARRIVED" ? "ARRIVED" : "COMPLETED";
    if (stop.status === "COMPLETED") {
      const order = context.orders.find((o) => o.order_no === stop.order_no)!;
      order.status = "COMPLETED";
      order.cargo_status = "DELIVERED";
      order.version++;
    }
    result = {
      ...stop,
      route_revision: context.vehicles.find(
        (v) => v.vehicle_id === stop.vehicle_id,
      )!.route_revision,
    };
  } else if (path.endsWith("/manual-handoff")) {
    inc.status = "MANUAL_REVIEW";
    inc.incident_version++;
    result = inc;
  } else if (path.endsWith("/verify-window")) {
    const order = context.orders.find((o) => o.order_no === b?.order_no);
    if (order) {
      order.earliest_arrival = b!.window_start;
      order.latest_arrival = b!.window_end;
      order.version++;
    }
    inc.reported_window_start = b?.window_start;
    inc.reported_window_end = b?.window_end;
    inc.status = "READY_FOR_ANALYSIS";
    inc.incident_version++;
    inc.can_analyze = true;
    inc.next_action = "ANALYZE_IMPACT";
    plans
      .filter((p) => p.incident_id === inc.incident_id)
      .forEach((p) => (p.status = "STALE"));
    result = inc;
  } else throw Error("此操作不在演示数据范围内。");
  return structuredClone(result) as T;
}
