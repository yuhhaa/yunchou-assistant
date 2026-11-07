import type { Context } from "../../api/types";
import type { GeographicRouteData } from "./types";
// Explicit DEMO-only geocoded public landmarks. Never attach these to API-mode IDs.
// Provenance: reference/geocoding-pois.json, Nominatim / OpenStreetMap, 2026-10-28.
export const demoLocations: Record<
  string,
  { name: string; coordinates: [number, number]; osm: string }
> = {
  WH01: {
    name: "演示起点 · 杭州国际博览中心",
    coordinates: [120.2346648, 30.2318275],
    osm: "way/582242842",
  },
  N1: {
    name: "城市阳台",
    coordinates: [120.2124962, 30.2441989],
    osm: "relation/8718551",
  },
  N2: {
    name: "杭州大剧院",
    coordinates: [120.2109931, 30.2466154],
    osm: "way/626838819",
  },
  N3: {
    name: "杭州万象城",
    coordinates: [120.2109698, 30.2541583],
    osm: "way/340388734",
  },
  N4: {
    name: "杭州国际会议中心",
    coordinates: [120.2091853, 30.2449295],
    osm: "way/626838544",
  },
  N5: {
    name: "奥体中心站",
    coordinates: [120.2282194, 30.2260329],
    osm: "node/8940624010",
  },
  N6: {
    name: "杭州国际会议中心",
    coordinates: [120.2091853, 30.2449295],
    osm: "way/626838544",
  },
};
export function demoGeography(context: Context): GeographicRouteData {
  return {
    coordinateSystem: "WGS84",
    source: "OSM 地理编码 / DEMO 示例任务",
    version: "hangzhou-poi-v1",
    observedAt: "2026-10-28T15:00:00+08:00",
    snapshotId: context.snapshot_id,
    snapshotVersion: context.snapshot_version,
    nodes: Object.entries(demoLocations).map(([nodeId, n]) => ({
      nodeId,
      coordinates: n.coordinates,
    })),
    routes: [],
  };
}
