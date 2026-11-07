<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch, inject } from "vue";
import { MapPin, RefreshCw } from "lucide-vue-next";
import {
  loadAmap,
  demoCity,
  amapLoaderKey,
} from "../../services/maps/amapLoader";
import type {
  AmapSDK,
  AmapInstance,
  AmapOverlay,
  AmapInfoWindow,
} from "../../services/maps/amapSdk";
import type {
  MapModel,
  DeliveryMarker,
} from "../../services/maps/mapDataAdapter";
import MapToolbar from "./MapToolbar.vue";
import { markerContent, infoContent } from "./mapMarkers";
import { drawRouteLayers } from "./routeLayers";
const props = defineProps<{
  model: MapModel;
  selectedOrder: string;
  focusOrder: string;
  vehicleId: string;
  planId: string | null;
  planVersion?: number | null;
  candidate: boolean;
  riskOrders?: string[];
  changedOrders: string[];
  nextOrder?: string;
  retry: number;
}>();
const emit = defineEmits<{
  select: [string];
  "select-node": [{ nodeId: string; vehicleId?: string }];
  ready: [];
  failure: [string];
  retry: [];
}>();
const host = ref<HTMLElement>(),
  ready = ref(false),
  error = ref("");
const sdkLoader = inject(amapLoaderKey, loadAmap);
let sdk: AmapSDK | undefined,
  map: AmapInstance | undefined,
  info: AmapInfoWindow | undefined;
let overlays: AmapOverlay[] = [],
  observer: ResizeObserver | undefined,
  intersection: IntersectionObserver | undefined;
let alive = true,
  generation = 0,
  fitted = "",
  completeTimer: ReturnType<typeof setTimeout> | undefined;
const immediate = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
function fit() {
  if (!map) return;
  if (overlays.length)
    map.setFitView(
      overlays,
      immediate(),
      (host.value?.offsetHeight || 400) < 250
        ? [35, 35, 35, 35]
        : [65, 55, 65, 55],
      16,
    );
  else
    map.setZoomAndCenter(
      demoCity.zoom,
      props.model.center || demoCity.center,
      immediate(),
    );
}
function show(point: DeliveryMarker, focus: boolean) {
  if (!map || !sdk) return;
  info?.close();
  info = new sdk.InfoWindow({
    content: infoContent(point, props.candidate),
    offset: new sdk.Pixel(0, -28),
  });
  info.open(map, point.coordinates);
  if (focus) map.setZoomAndCenter(15, point.coordinates, immediate());
}
function update() {
  if (!map || !sdk) return;
  info?.close();
  overlays.forEach((o) => o.setMap(null));
  overlays = drawRouteLayers(sdk, map, props.model.layers);
  for (const point of props.model.markers) {
    const marker = new sdk.Marker({
      map,
      position: point.coordinates,
      anchor: "bottom-center",
      content: markerContent(
        point,
        (point.orderNos || [point.orderNo]).includes(props.selectedOrder),
        (point.orderNos || [point.orderNo]).includes(props.nextOrder),
        (point.orderNos || [point.orderNo]).some(
          (n) => !!n && props.riskOrders?.includes(n),
        ),
        (point.orderNos || [point.orderNo]).some(
          (n) => !!n && props.changedOrders.includes(n),
        ),
      ),
      zIndex:
        point.orderNo === props.selectedOrder
          ? 150
          : point.kind === "vehicle"
            ? 140
            : 100,
    });
    marker.on("click", () => {
      if (point.orderNo) emit("select", point.orderNo);
      else if (point.nodeId)
        emit("select-node", {
          nodeId: point.nodeId,
          vehicleId: point.vehicleId,
        });
      show(point, false);
    });
    overlays.push(marker);
  }
  const key = [
    props.vehicleId,
    props.planId,
    props.planVersion,
    props.model.source,
    props.model.version,
    props.model.fitKey,
    props.model.markers.length > 0,
  ].join(":");
  if (key !== fitted) {
    fit();
    fitted = key;
  }
  const selected = props.model.markers.find((m) =>
    (m.orderNos || [m.orderNo]).includes(props.selectedOrder),
  );
  if (selected) show(selected, false);
}
async function init() {
  const run = ++generation;
  error.value = "";
  ready.value = false;
  try {
    const loaded = await sdkLoader();
    if (!alive || run !== generation || !host.value) return;
    sdk = loaded;
    map = new sdk.Map(host.value, {
      viewMode: "2D",
      resizeEnable: true,
      zoom: demoCity.zoom,
      center: props.model.center || demoCity.center,
      mapStyle: "amap://styles/normal",
      pitch: 0,
      dragEnable: true,
      zoomEnable: true,
      rotateEnable: false,
    });
    map.addControl(
      new sdk.Scale({ position: "LB", offset: new sdk.Pixel(12, 48) }),
    );
    map.on("complete", () => {
      if (!alive || run !== generation) return;
      clearTimeout(completeTimer);
      ready.value = true;
      error.value = "";
      emit("ready");
    });
    map.on("error", () => {
      ready.value = false;
      error.value = "地图瓦片或服务加载失败，请检查网络和地图鉴权。";
      emit("failure", error.value);
    });
    completeTimer = setTimeout(() => {
      if (!ready.value && alive) {
        error.value = "地图内容加载超时，任务列表仍可使用。";
        emit("failure", error.value);
      }
    }, 18000);
    update();
    observer = new ResizeObserver(() => {
      if (host.value?.offsetWidth && host.value?.offsetHeight) map?.resize?.();
    });
    observer.observe(host.value);
    intersection = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) map?.resize?.();
    });
    intersection.observe(host.value);
  } catch (e) {
    if (alive && run === generation) {
      error.value = e instanceof Error ? e.message : "地图加载失败";
      emit("failure", error.value);
    }
  }
}
function cleanup() {
  clearTimeout(completeTimer);
  observer?.disconnect();
  intersection?.disconnect();
  info?.close();
  overlays.forEach((o) => o.setMap(null));
  overlays = [];
  map?.destroy();
  map = undefined;
  fitted = "";
}
watch(
  () => [
    props.model,
    props.selectedOrder,
    props.riskOrders,
    props.changedOrders,
    props.nextOrder,
  ],
  update,
  { deep: true },
);
watch(
  () => [props.selectedOrder, props.focusOrder],
  () => {
    const point = props.model.markers.find((m) =>
      (m.orderNos || [m.orderNo]).includes(
        props.focusOrder || props.selectedOrder,
      ),
    );
    if (point) show(point, true);
  },
);
watch(
  () => props.retry,
  () => {
    cleanup();
    void init();
  },
);
onMounted(init);
onUnmounted(() => {
  alive = false;
  generation++;
  cleanup();
});
defineExpose({ fit });
</script>
<template>
  <div
    class="amap-workspace"
    :data-sdk-ready="ready"
    :data-business-points="model.markers.length"
    :data-route-layers="model.layers.length"
  >
    <div ref="host" class="amap-host" aria-label="高德标准浅色城市地图" />
    <div v-if="!ready && !error" class="amap-loading" role="status">
      <RefreshCw :size="18" /><b>正在加载高德地图</b
      ><span>任务与异常处理仍可使用</span>
    </div>
    <div v-if="error" class="amap-load-error" role="status">
      <MapPin :size="24" /><b>{{
        error.includes("鉴权未配置") ? "地图鉴权未配置" : "地图暂时无法加载"
      }}</b>
      <p>{{ error }}</p>
      <button class="secondary" @click="emit('retry')">重新加载地图</button>
    </div>
    <div
      v-if="ready && !model.markers.length"
      class="amap-data-notice"
      role="status"
    >
      <b>真实地图已接入，配送地理数据待后端提供。</b
      ><span>{{ demoCity.name }} · 演示城市视野，不代表车队所在地</span
      ><span>真实地理数据尚未接入 · 不绘制虚构车辆、配送点或路线</span>
    </div>
    <div v-if="ready && model.demo" class="amap-data-notice">
      <b
        >武汉模拟配送 ·
        {{ model.local ? "本地响应样例" : "后端 SIMULATION" }}</b
      ><span>模拟连线，不代表实际行驶道路 · 非车辆实时GPS</span
      ><span>{{ model.version }}</span>
    </div>
    <MapToolbar
      :ready="ready"
      @zoom-in="map?.zoomIn()"
      @zoom-out="map?.zoomOut()"
      @fit="fit"
    />
  </div>
</template>
