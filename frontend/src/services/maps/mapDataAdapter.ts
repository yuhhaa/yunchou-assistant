import type { Context } from "../../api/types";
import type { GeographicRouteData, Stop } from "../../components/maps/types";
import type { LngLat } from "./amapSdk";
export type DeliveryMarker = {
  id: string;
  kind: "warehouse" | "vehicle" | "stop";
  coordinates: LngLat;
  label: string;
  orderNo?: string;
  sequence?: number;
  status?: string;
  observedAt?: string;
  nodeId?: string;
  orderNos?: string[];
  vehicleId?: string;
};
export type DeliveryLayer = {
  role: "current" | "original" | "candidate";
  coordinates: LngLat[];
  geometryKind?: "ROAD" | "SCHEMATIC";
  vehicleId?: string;
  distanceM?: number | null;
  durationSeconds?: number | null;
  muted?: boolean;
};
export type MapModel = {
  markers: DeliveryMarker[];
  layers: DeliveryLayer[];
  issues: string[];
  source: string;
  version: string;
  observedAt: string;
  demo: boolean;
  city?: string;
  center?: LngLat;
  local?: boolean;
  notice?: string;
  fitKey?: string;
  schematic?: boolean;
};
export const validCoordinate = (c: unknown): c is LngLat =>
  Array.isArray(c) &&
  c.length === 2 &&
  c.every(Number.isFinite) &&
  Math.abs(c[0]) <= 180 &&
  Math.abs(c[1]) <= 90;
export function adaptMapData(input: {
  data?: GeographicRouteData;
  context: Context | null;
  stops: Stop[];
  baseline?: Stop[];
  vehicleId: string;
  planId: string | null;
  planVersion?: number | null;
  demoAllowed: boolean;
}): MapModel {
  const model: MapModel = {
    markers: [],
    layers: [],
    issues: [],
    source: "",
    version: "",
    observedAt: "",
    demo: false,
  };
  const { data, context } = input;
  if (!data) {
    model.issues.push("配送地理数据待后端提供");
    return model;
  }
  const reject = (message: string) => {
    model.issues.push(message);
    return model;
  };
  if (
    !context ||
    data.snapshotId !== context.snapshot_id ||
    data.snapshotVersion !== context.snapshot_version
  )
    return reject("地理数据与当前快照版本不一致");
  if (data.coordinateSystem !== "GCJ02")
    return reject("高德需要 GCJ-02 坐标，请由约定的数据适配服务统一转换");
  if (
    !data.source ||
    !data.version ||
    !Number.isFinite(Date.parse(data.observedAt))
  )
    return reject("地理来源、版本或更新时间缺失");
  if (data.dataKind === "DEMO" && !input.demoAllowed)
    return reject("演示坐标不能绑定正式业务上下文");
  if (new Set(data.nodes.map((n) => n.nodeId)).size !== data.nodes.length)
    return reject("存在重复节点坐标，请后端核对");
  const nodes = new Map(
    data.nodes
      .filter((n) => validCoordinate(n.coordinates))
      .map((n) => [n.nodeId, n.coordinates]),
  );
  model.source = data.source;
  model.version = data.version;
  model.observedAt = data.observedAt;
  model.demo = data.dataKind === "DEMO";
  const stops = input.stops
    .filter(
      (s) =>
        s.vehicle_id === input.vehicleId &&
        context.orders.some((o) => o.order_no === s.order_no),
    )
    .slice()
    .sort((a, b) => a.sequence_no - b.sequence_no);
  const warehouse = nodes.get(context.warehouse_node_id || "");
  if (warehouse)
    model.markers.push({
      id: "warehouse",
      kind: "warehouse",
      label: "配送仓库",
      coordinates: warehouse,
    });
  for (const s of stops) {
    const coordinates = nodes.get(s.node_id);
    if (!coordinates) {
      model.issues.push("部分任务坐标缺失或无效");
      continue;
    }
    const order = context.orders.find((o) => o.order_no === s.order_no);
    model.markers.push({
      id: s.stop_id,
      kind: "stop",
      coordinates,
      orderNo: s.order_no,
      sequence: s.sequence_no,
      status: s.status,
      label:
        typeof order?.facts.name === "string" ? order.facts.name : s.node_id,
    });
  }
  const vehicle = context.vehicles.find(
    (v) => v.vehicle_id === input.vehicleId,
  );
  const position = nodes.get(vehicle?.location_node_id || "");
  if (
    vehicle &&
    position &&
    vehicle.location_time &&
    Number.isFinite(Date.parse(vehicle.location_time))
  )
    model.markers.push({
      id: vehicle.vehicle_id,
      kind: "vehicle",
      label: "车辆 " + vehicle.vehicle_no + " · 最新确认位置",
      coordinates: position,
      observedAt: vehicle.location_time,
    });
  const route = data.routes.find(
    (r) =>
      r.vehicleId === input.vehicleId &&
      r.planId === input.planId &&
      (input.planId
        ? r.planVersion === input.planVersion
        : r.routeRevision === vehicle?.route_revision),
  );
  function addRoute(
    r: GeographicRouteData["routes"][number] | undefined,
    expected: Stop[],
    role: DeliveryLayer["role"],
  ) {
    if (!r) {
      model.issues.push(
        role === "candidate" ? "候选道路几何尚未提供" : "当前道路几何尚未提供",
      );
      return;
    }
    const ids = expected
      .filter((s) => s.vehicle_id === input.vehicleId)
      .slice()
      .sort((a, b) => a.sequence_no - b.sequence_no)
      .map((s) => s.stop_id);
    if (
      r.geometryKind !== "ROAD" ||
      r.coordinates.length < 2 ||
      !r.coordinates.every(validCoordinate)
    ) {
      model.issues.push("缺少经过确认的道路几何，不绘制节点直线");
      return;
    }
    if (!r.stopIds || JSON.stringify(r.stopIds) !== JSON.stringify(ids)) {
      model.issues.push("道路几何的停靠序列与业务计划不一致");
      return;
    }
    model.layers.push({ role, coordinates: r.coordinates });
  }
  addRoute(route, stops, input.planId ? "candidate" : "current");
  // A gray reference must be explicitly supplied as ORIGINAL, never inferred from a newer current route.
  if (input.planId && input.baseline?.length) {
    const original = data.routes.find(
      (r) =>
        r.vehicleId === input.vehicleId &&
        r.role === "ORIGINAL" &&
        r.planId === null &&
        r.routeRevision === vehicle?.route_revision,
    );
    if (original) addRoute(original, input.baseline, "original");
    else model.issues.push("原计划道路几何尚未提供");
  }
  model.issues = [...new Set(model.issues)];
  return model;
}
