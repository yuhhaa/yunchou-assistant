import type {
  AmapSDK,
  AmapInstance,
  AmapOverlay,
} from "../../services/maps/amapSdk";
import type { DeliveryLayer } from "../../services/maps/mapDataAdapter";
export function drawRouteLayers(
  sdk: AmapSDK,
  map: AmapInstance,
  layers: DeliveryLayer[],
): AmapOverlay[] {
  return layers.map((layer) => {
    const line = new sdk.Polyline({
      map,
      path: layer.coordinates,
      strokeColor:
        layer.role === "candidate"
          ? "#d86a21"
          : layer.role === "original"
            ? "#80929f"
            : layer.vehicleId
              ? ["#17675d", "#506c9b", "#93693c", "#627d69", "#8b6c88"][
                  Math.max(0, Number(layer.vehicleId.slice(-2)) - 1) % 5
                ]
              : "#17675d",
      strokeWeight: layer.role === "original" ? 5 : 7,
      strokeOpacity: layer.role === "original" ? 0.7 : 0.98,
      isOutline: true,
      outlineColor: "#ffffff",
      borderWeight: 2,
      lineJoin: "round",
      lineCap: "round",
      strokeStyle: layer.geometryKind === "SCHEMATIC" ? "dashed" : "solid",
      strokeDasharray: layer.geometryKind === "SCHEMATIC" ? [12, 8] : undefined,
      zIndex: layer.role === "original" ? 50 : 60,
      showDir: layer.role !== "original",
    });
    return line;
  });
}
