import type { Schema, Context } from "../../api/types";
import type { Stop } from "../../components/maps/types";
import {
  validCoordinate,
  type MapModel,
  type DeliveryLayer,
} from "./mapDataAdapter";
export function adaptWuhanMap(input: {
  data: Schema["DemoMapOutput"];
  reference?: Schema["DemoMapOutput"];
  context: Context | null;
  stops: Stop[];
  baseline?: Stop[];
  vehicleId: string;
  planId: string | null;
  planVersion?: number | null;
  local: boolean;
}): MapModel {
  const { data, context } = input;
  const model: MapModel = {
    markers: [],
    layers: [],
    issues: [],
    source: data.source,
    version: data.dataset_version,
    observedAt: data.retrieved_at,
    demo: true,
    city: data.city,
    center: validCoordinate(data.center) ? data.center : undefined,
    local: input.local,
    notice: data.notice,
    fitKey: "",
    schematic: true,
  };
  const reject = (reason: string) => {
    model.issues.push(reason);
    return model;
  };
  if (
    data.coordinate_system !== "GCJ02" ||
    data.source !== "SIMULATION" ||
    data.simulation !== true
  )
    return reject("武汉模拟数据来源或坐标系不符合契约");
  if (!data.dataset_version || !Number.isFinite(Date.parse(data.retrieved_at)))
    return reject("地图数据版本或取得时间缺失");
  if (context && context.source !== "SIMULATION")
    return reject("模拟点位不得绑定正式业务上下文");
  const points = new Map(
    data.points
      .filter(
        (p) =>
          validCoordinate([p.longitude, p.latitude]) &&
          p.source === "SIMULATION",
      )
      .map((p) => [p.node_id, p]),
  );
  if (points.size !== data.points.length)
    return reject("模拟点位重复、无效或来源不一致");
  const routes = data.routes.filter(
    (r) => !input.vehicleId || r.vehicle_id === input.vehicleId,
  );
  const allowedNodes = new Set(routes.flatMap((r) => r.node_sequence));
  const stops = input.stops
    .filter(
      (s) =>
        s.status !== "COMPLETED" &&
        s.status !== "CANCELLED" &&
        (!input.vehicleId || s.vehicle_id === input.vehicleId),
    )
    .slice()
    .sort((a, b) => a.sequence_no - b.sequence_no);
  const warehouse = context?.warehouse_node_id;
  for (const [nodeId, p] of points) {
    if (!allowedNodes.has(nodeId)) continue;
    const linked = stops.filter(
      (s) =>
        s.node_id === nodeId &&
        context?.orders.some((o) => o.order_no === s.order_no),
    );
    const owner = routes.find((r) =>
      r.node_sequence.includes(nodeId),
    )?.vehicle_id;
    model.markers.push({
      id: nodeId,
      nodeId,
      kind: nodeId === warehouse ? "warehouse" : "stop",
      coordinates: [p.longitude, p.latitude],
      label: p.name,
      orderNo: linked[0]?.order_no,
      orderNos: linked.map((s) => s.order_no),
      sequence:
        linked[0]?.sequence_no ??
        Math.max(
          1,
          routes
            .find((r) => r.vehicle_id === owner)!
            .node_sequence.indexOf(nodeId),
        ),
      status: linked[0]?.status,
      vehicleId: owner,
    });
  }
  function layer(r: Schema["DemoMapRoute"], role: DeliveryLayer["role"]) {
    const vehicle = context?.vehicles.find(
      (v) => v.vehicle_id === r.vehicle_id,
    );
    if (vehicle && vehicle.route_revision !== r.route_revision) {
      model.issues.push("路线版本已变化，请刷新地图");
      return;
    }
    if (
      r.geometry_kind !== "SCHEMATIC" ||
      r.distance_m !== null ||
      r.duration_seconds !== null
    ) {
      model.issues.push("模拟几何或里程/时长边界不符合契约");
      return;
    }
    if (r.node_sequence.some((n) => !points.has(n))) {
      model.issues.push("路线包含未授权或缺失节点");
      return;
    }
    const relevant = stops
      .filter((s) => s.vehicle_id === r.vehicle_id)
      .map((s) => s.node_id);
    const ordered = r.node_sequence.slice(1);
    if (
      relevant.length &&
      JSON.stringify(relevant) !== JSON.stringify(ordered)
    ) {
      model.issues.push("模拟路线与当前未完成站点顺序不一致");
      return;
    }
    if (!r.geometry) return;
    const geometryNodes = r.node_sequence.filter((n, i, sequence) => {
      if (!i) return true;
      const current = points.get(n)!;
      const previous = points.get(sequence[i - 1]!)!;
      return (
        current.longitude !== previous.longitude ||
        current.latitude !== previous.latitude
      );
    });
    if (
      r.geometry.type !== "LineString" ||
      r.geometry.coordinates.length !== geometryNodes.length ||
      !r.geometry.coordinates.every(validCoordinate)
    ) {
      model.issues.push("模拟连线几何无效");
      return;
    }
    if (
      r.geometry.coordinates.some((c, i) => {
        const p = points.get(geometryNodes[i]!)!;
        return c[0] !== p.longitude || c[1] !== p.latitude;
      })
    ) {
      model.issues.push("模拟连线与节点坐标不一致");
      return;
    }
    model.layers.push({
      role,
      geometryKind: "SCHEMATIC",
      vehicleId: r.vehicle_id,
      coordinates: r.geometry.coordinates as [number, number][],
      distanceM: r.distance_m,
      durationSeconds: r.duration_seconds,
      muted: !!input.vehicleId && input.vehicleId !== r.vehicle_id,
    });
  }
  for (const r of routes) {
    if (input.planId) {
      if (
        r.route_kind !== "CANDIDATE_PREVIEW" ||
        r.plan_id !== input.planId ||
        r.plan_version !== input.planVersion
      ) {
        model.issues.push("候选地图与所选方案或版本不一致");
        continue;
      }
      layer(r, "candidate");
    } else {
      if (r.route_kind !== "CURRENT" || r.plan_id !== null) {
        model.issues.push("候选预览不能作为当前路线");
        continue;
      }
      layer(r, "current");
    }
  }
  if (
    input.planId &&
    input.reference &&
    input.reference.dataset_version === data.dataset_version
  ) {
    const reference = adaptWuhanMap({
      ...input,
      data: input.reference,
      reference: undefined,
      stops: input.baseline || context?.route_stops || [],
      planId: null,
      planVersion: null,
    });
    model.layers.unshift(
      ...reference.layers.map((l) => ({ ...l, role: "original" as const })),
    );
  }

  model.fitKey = routes
    .map((r) =>
      [
        r.vehicle_id,
        r.route_revision,
        r.plan_id,
        r.plan_version,
        r.node_sequence.join(","),
      ].join(":"),
    )
    .join("|");
  model.issues = [...new Set(model.issues)];
  return model;
}
