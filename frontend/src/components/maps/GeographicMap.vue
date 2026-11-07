<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from "vue";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FeatureCollection, LineString } from "geojson";
import type { Context, Schema } from "../../api/types";
import type { GeographicRouteData, GeographicMapConfig, Stop } from "./types";
const props = defineProps<{
  data?: GeographicRouteData;
  config?: GeographicMapConfig;
  context: Context | null;
  stops: Stop[];
  baseline?: Stop[];
  vehicleId: string;
  planId: string | null;
  planVersion?: number | null;
  selectedOrder: string;
  candidate: boolean;
  riskOrders?: string[];
  changes?: Schema["Change"][];
  focusOrder: string;
  nextOrder?: string;
  demo: boolean;
  retry: number;
}>();
const emit = defineEmits<{
  select: [string];
  failure: [string];
  ready: [{ kind: "road" | "connections" | "empty"; points: number }];
}>();
const host = ref<HTMLElement>();
let map: maplibregl.Map | undefined,
  resize: ResizeObserver | undefined,
  timer: ReturnType<typeof setTimeout> | undefined;
let markers: maplibregl.Marker[] = [];
let fitKey = "";
let reportedError = false;
const valid = (c: unknown): c is [number, number] =>
  Array.isArray(c) &&
  c.length === 2 &&
  c.every(Number.isFinite) &&
  Math.abs(c[0]) <= 180 &&
  Math.abs(c[1]) <= 90;
function coordinate(nodeId: string) {
  const point = props.data?.nodes.find((n) => n.nodeId === nodeId)?.coordinates;
  return valid(point) ? point : null;
}
function lines(stops: Stop[]): FeatureCollection<LineString> {
  const points: [number, number][] = [],
    features: FeatureCollection<LineString>["features"] = [];
  const origin = coordinate(props.context?.warehouse_node_id || "");
  if (origin) points.push(origin);
  for (const stop of [...stops]
    .filter((s) => s.vehicle_id === props.vehicleId)
    .sort((a, b) => a.sequence_no - b.sequence_no)) {
    const c = coordinate(stop.node_id);
    if (c) points.push(c);
    else if (points.length > 1) {
      features.push({
        type: "Feature",
        properties: {},
        geometry: { type: "LineString", coordinates: [...points] },
      });
      points.length = 0;
    }
  }
  if (points.length > 1)
    features.push({
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: points },
    });
  return { type: "FeatureCollection", features };
}
function setSource(name: string, data: FeatureCollection<LineString>) {
  const source = map?.getSource(name) as maplibregl.GeoJSONSource | undefined;
  if (source) source.setData(data);
  else map?.addSource(name, { type: "geojson", data });
}
function icon(kind: string) {
  return kind === "warehouse"
    ? '<svg viewBox="0 0 24 24"><path d="m3 10 9-7 9 7v11H3Z M9 21V12h6v9"/></svg>'
    : '<svg viewBox="0 0 24 24"><path d="M3 5h11v12H3z M14 9h4l3 4v4h-7 M7 17v3 M18 17v3"/></svg>';
}
function marker(
  coordinates: [number, number],
  kind: string,
  label: string,
  sequence?: number,
  orderNo?: string,
  extra = "",
) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "delivery-map-marker " + kind + " " + extra;
  el.setAttribute(
    "aria-label",
    kind === "stop" ? "选择配送点 " + orderNo + " " + label : label,
  );
  if (orderNo) el.dataset.order = orderNo;
  const badge = document.createElement("span");
  badge.className = "delivery-marker-badge";
  if (kind === "stop") badge.textContent = String(sequence).padStart(2, "0");
  else badge.innerHTML = icon(kind);
  el.append(badge);
  if (kind !== "vehicle") {
    const caption = document.createElement("span");
    caption.className = "delivery-marker-name";
    caption.textContent = label;
    el.append(caption);
  }
  if (kind === "vehicle") {
    el.title = "最近确认位置，非实时定位";
  }
  if (orderNo) {
    el.setAttribute("aria-pressed", String(orderNo === props.selectedOrder));
    el.onclick = () => emit("select", orderNo);
  }
  markers.push(
    new maplibregl.Marker({
      element: el,
      anchor: "center",
      offset: kind === "vehicle" ? [28, -29] : [0, 0],
    })
      .setLngLat(coordinates)
      .addTo(map!),
  );
}
function update() {
  if (!map?.isStyleLoaded()) return;
  const sorted = [...props.stops]
    .filter((s) => s.vehicle_id === props.vehicleId)
    .sort((a, b) => a.sequence_no - b.sequence_no);
  const road = props.data?.routes.find(
    (r) =>
      r.vehicleId === props.vehicleId &&
      r.planId === props.planId &&
      (!props.planId || r.planVersion === props.planVersion) &&
      r.coordinates?.length > 1 &&
      r.coordinates.every(valid),
  );
  const current = road
    ? {
        type: "FeatureCollection" as const,
        features: [
          {
            type: "Feature" as const,
            properties: {},
            geometry: {
              type: "LineString" as const,
              coordinates: road.coordinates,
            },
          },
        ],
      }
    : lines(sorted);
  setSource("delivery-current", current);
  setSource(
    "delivery-baseline",
    props.candidate
      ? lines(props.baseline || [])
      : { type: "FeatureCollection", features: [] },
  );
  if (!map.getLayer("route-baseline")) {
    map.addLayer({
      id: "route-baseline",
      type: "line",
      source: "delivery-baseline",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#5d7666",
        "line-width": 3,
        "line-opacity": 0.45,
        "line-dasharray": [2, 2],
      },
    });
    map.addLayer({
      id: "route-shadow",
      type: "line",
      source: "delivery-current",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#344f36", "line-width": 9, "line-opacity": 0.18 },
    });
    map.addLayer({
      id: "route-selected",
      type: "line",
      source: "delivery-current",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#dbe96b", "line-width": 5, "line-opacity": 0.98 },
    });
  }
  markers.forEach((m) => m.remove());
  markers = [];
  const origin = coordinate(props.context?.warehouse_node_id || "");
  if (origin) marker(origin, "warehouse", "配送仓库");
  sorted.forEach((s) => {
    const c = coordinate(s.node_id);
    if (!c) return;
    const order = props.context?.orders.find((o) => o.order_no === s.order_no);
    const name =
      typeof order?.facts.name === "string" ? order.facts.name : s.node_id;
    marker(
      c,
      "stop",
      name,
      s.sequence_no,
      s.order_no,
      [
        props.selectedOrder === s.order_no ? "selected" : "",
        props.nextOrder === s.order_no ? "next" : "",
        props.riskOrders?.includes(s.order_no) ? "risk" : "",
        props.changes?.some((c) => c.order_no === s.order_no) ? "changed" : "",
        s.status === "COMPLETED" ? "completed" : "",
      ].join(" "),
    );
  });
  const vehicle = props.context?.vehicles.find(
    (v) => v.vehicle_id === props.vehicleId,
  );
  const position = coordinate(vehicle?.location_node_id || "");
  if (position)
    marker(position, "vehicle", "车辆 " + props.vehicleId + " · 最新确认位置");
  const key =
    props.vehicleId +
    props.planId +
    props.planVersion +
    props.context?.snapshot_id +
    props.data?.version;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (fitKey !== key) {
    const points = [origin, ...sorted.map((s) => coordinate(s.node_id))].filter(
      (c): c is [number, number] => !!c,
    );
    if (points.length) {
      const bounds = new maplibregl.LngLatBounds();
      points.forEach((c) => bounds.extend(c));
      map.fitBounds(bounds, {
        padding: { top: 90, bottom: 90, left: 85, right: 85 },
        maxZoom: 15.7,
        duration: reduce ? 0 : 420,
      });
    }
    fitKey = key;
  }
  if (props.focusOrder) {
    const c = coordinate(
      sorted.find((s) => s.order_no === props.focusOrder)?.node_id || "",
    );
    if (c) map.easeTo({ center: c, zoom: 16, duration: reduce ? 0 : 350 });
  }
  emit("ready", {
    kind: road ? "road" : current.features.length ? "connections" : "empty",
    points: sorted.filter((s) => coordinate(s.node_id)).length,
  });
}
function mount() {
  if (!host.value || !props.config?.styleUrl) return;
  reportedError = false;
  fitKey = "";
  try {
    map = new maplibregl.Map({
      container: host.value,
      style: props.config.styleUrl,
      center: props.data?.nodes.find((n) => valid(n.coordinates))
        ?.coordinates || [104.2, 35.8],
      zoom: 3.5,
      minZoom: 2,
      maxZoom: 18,
      attributionControl: false,
      interactive: true,
      renderWorldCopies: false,
      pitchWithRotate: false,
      dragRotate: false,
    });
    map.addControl(
      new maplibregl.AttributionControl({ compact: true }),
      "bottom-left",
    );
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "bottom-right",
    );
    timer = setTimeout(
      () => emit("failure", "底图加载较慢，可重试；任务列表仍可使用。"),
      18000,
    );
    map.on("load", () => {
      clearTimeout(timer);
      update();
    });
    map.on("error", () => {
      if (!reportedError) {
        reportedError = true;
        emit(
          "failure",
          "部分地图内容未加载，请重试或检查网络；列表操作不受影响。",
        );
      }
    });
    resize = new ResizeObserver(() => map?.resize());
    resize.observe(host.value);
  } catch {
    emit(
      "failure",
      "当前设备无法启用 WebGL 地图，请启用浏览器硬件加速后重试；列表仍可使用。",
    );
  }
}
onMounted(mount);
watch(
  () => [
    props.data,
    props.stops,
    props.selectedOrder,
    props.planId,
    props.riskOrders,
    props.changes,
    props.focusOrder,
  ],
  update,
  { deep: true },
);
watch(
  () => props.retry,
  () => {
    markers.forEach((m) => m.remove());
    markers = [];
    resize?.disconnect();
    map?.remove();
    mount();
  },
);
onUnmounted(() => {
  clearTimeout(timer);
  resize?.disconnect();
  map?.remove();
});
</script>
<template>
  <div
    ref="host"
    class="geographic-map real-city-map"
    aria-label="城市配送真实地图"
  />
</template>
