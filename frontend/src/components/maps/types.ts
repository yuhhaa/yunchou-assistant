import type { Context, Schema } from "../../api/types";
export type Stop = Schema["RouteStopInput"];
export type MapSelection = { orderNo: string; stopId: string; nodeId: string };
// Frontend adapter contract only. No new backend endpoint or inferred facts fields.
export interface GeographicRouteData {
  dataKind?: "BUSINESS" | "DEMO";
  coordinateSystem: "WGS84" | "GCJ02" | "BD09";
  source: string;
  version: string;
  observedAt: string;
  snapshotId: string;
  snapshotVersion: number;
  nodes: { nodeId: string; coordinates: [number, number] }[];
  routes: {
    vehicleId: string;
    planId: string | null;
    planVersion: number | null;
    routeRevision: number | null;
    geometryKind?: "ROAD";
    stopIds?: string[];
    role?: "CURRENT" | "ORIGINAL" | "CANDIDATE";
    coordinates: [number, number][];
  }[];
}
export interface GeographicMapConfig {
  styleUrl: string;
  attribution: string;
  sourceName: string;
}
export function geographyIssue(
  data: GeographicRouteData | undefined,
  config: GeographicMapConfig | undefined,
  context: Context | null,
  stops: Stop[],
  vehicleId: string,
  planId: string | null,
  planVersion: number | null = null,
): string {
  if (!data || !config)
    return "当前接口未提供地理坐标、道路几何及地图服务配置，继续使用配送节点网络。";
  if (data.coordinateSystem !== "WGS84")
    return "地理数据坐标系尚未统一为 WGS84，不能直接叠加。";
  if (
    !data.source ||
    !data.version ||
    !Number.isFinite(Date.parse(data.observedAt)) ||
    !config.attribution ||
    !config.sourceName
  )
    return "地图数据来源、版本、更新时间或授权信息不完整。";
  if (!context || data.snapshotId !== context.snapshot_id)
    return "地图数据与当前配送快照不一致，请更新数据。";
  if (data.snapshotVersion !== context.snapshot_version)
    return "地图数据与当前配送快照版本不一致。";
  if (
    !/^https:\/\//.test(config.styleUrl) &&
    !/^\/(?!\/)/.test(config.styleUrl)
  )
    return "地图样式必须来自配置的 HTTPS 或同源服务。";
  const valid = (c: number[]) =>
    c.length === 2 &&
    c.every(Number.isFinite) &&
    Math.abs(c[0]!) <= 180 &&
    Math.abs(c[1]!) <= 90;
  const route = data.routes.find(
    (r) => r.vehicleId === vehicleId && r.planId === planId,
  );
  if (!route || route.coordinates.length < 2 || !route.coordinates.every(valid))
    return "所选路线缺少经过验证的地理几何。";
  if (planId && (!planVersion || route.planVersion !== planVersion))
    return "地理候选路线与所选方案版本不一致。";
  const vehicle = context.vehicles.find((v) => v.vehicle_id === vehicleId);
  if (!planId && route.routeRevision !== vehicle?.route_revision)
    return "地理路线版本与当前生效路线版本不一致。";
  const ids = new Set(
    data.nodes.filter((n) => valid(n.coordinates)).map((n) => n.nodeId),
  );
  if (ids.size !== data.nodes.length || stops.some((s) => !ids.has(s.node_id)))
    return "配送节点坐标不完整或存在重复节点。";
  if (context.warehouse_node_id && !ids.has(context.warehouse_node_id))
    return "配送仓库缺少有效坐标。";
  if (vehicle?.location_node_id && !ids.has(vehicle.location_node_id))
    return "车辆最近确认节点缺少有效坐标。";
  return "";
}
