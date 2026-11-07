import type { Schema } from "../api/types";
export function isMapRoutingResult(
  value: unknown,
): value is Schema["MapRoutingOutput"] {
  if (!value || typeof value !== "object") return false;
  const r = value as Record<string, unknown>;
  return (
    r.source === "AMAP" &&
    r.coordinate_system === "GCJ02" &&
    r.routing_profile === "DRIVING_CAR" &&
    typeof r.routing_version === "string" &&
    !!r.routing_version &&
    Number.isInteger(r.node_count) &&
    Number(r.node_count) >= 0 &&
    typeof r.published_at === "string" &&
    Number.isFinite(Date.parse(r.published_at)) &&
    !!r.node_versions &&
    typeof r.node_versions === "object" &&
    !Array.isArray(r.node_versions) &&
    Object.values(r.node_versions).every(
      (v) => Number.isInteger(v) && Number(v) >= 0,
    )
  );
}
