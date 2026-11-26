import type {
  AmapSDK,
  AmapInstance,
  AmapOverlay,
  AmapPolylineLike,
} from "../../services/maps/amapSdk";
import type { DeliveryLayer } from "../../services/maps/mapDataAdapter";
const palette = ["#3ddcb0", "#6fa8ff", "#f2b562", "#a9d977", "#d79bff"];
export function routeColor(layer: DeliveryLayer) {
  if (layer.role === "candidate") return "#f5c451";
  if (layer.role === "original") return "#8fa39b";
  if (!layer.vehicleId) return palette[0]!;
  return palette[Math.max(0, Number(layer.vehicleId.slice(-2)) - 1) % 5]!;
}
export type DrawnRoute = { layer: DeliveryLayer; lines: AmapPolylineLike[] };
export function drawRouteLayers(
  sdk: AmapSDK,
  map: AmapInstance,
  layers: DeliveryLayer[],
  drawn: DrawnRoute[] = [],
): AmapOverlay[] {
  const out: AmapOverlay[] = [];
  for (const layer of layers) {
    const color = routeColor(layer);
    const original = layer.role === "original";
    const strokeStyle =
      original || layer.geometryKind === "SCHEMATIC" ? "dashed" : "solid";
    const strokeDasharray = original
      ? [6, 8]
      : layer.geometryKind === "SCHEMATIC"
        ? [12, 8]
        : undefined;
    const lines: AmapPolylineLike[] = [];
    if (!original) {
      // Soft glow underneath the main stroke.
      lines.push(
        new sdk.Polyline({
          map,
          path: layer.coordinates,
          strokeColor: color,
          strokeWeight: 16,
          strokeOpacity: 0.18,
          strokeStyle,
          strokeDasharray,
          lineJoin: "round",
          lineCap: "round",
          zIndex: 55,
          bubble: true,
        }) as AmapPolylineLike,
      );
    }
    lines.push(
      new sdk.Polyline({
        map,
        path: layer.coordinates,
        strokeColor: color,
        strokeWeight: original ? 4 : 6,
        strokeOpacity: original ? 0.6 : 0.95,
        isOutline: !original,
        outlineColor: "#0b1a16",
        borderWeight: 1,
        lineJoin: "round",
        lineCap: "round",
        strokeStyle,
        strokeDasharray,
        zIndex: original ? 50 : 60,
        showDir: !original,
        dirColor: "#ffffff",
      }) as AmapPolylineLike,
    );
    drawn.push({ layer, lines });
    out.push(...lines);
  }
  return out;
}
