<script setup lang="ts">
import {
  computed,
  defineAsyncComponent,
  ref,
  watch,
  onUnmounted,
  onErrorCaptured,
  nextTick,
} from "vue";
import {
  Expand,
  Minimize2,
  Focus,
  MapPin,
  X,
  Package,
  Clock3,
  RefreshCw,
} from "lucide-vue-next";
import type { Context, Schema } from "../../api/types";
import { label, time } from "../../lib/format";
import { adaptMapData } from "../../services/maps/mapDataAdapter";
import { adaptWuhanMap } from "../../services/maps/wuhanAdapter";

import {
  type GeographicRouteData,
  type GeographicMapConfig,
  type Stop,
} from "./types";
const AmapMap = defineAsyncComponent({
  loader: () => import("./AmapMap.vue"),
  onError(_e, _retry, fail) {
    fail();
  },
});
const props = withDefaults(
  defineProps<{
    context: Context | null;
    stops: Stop[];
    baseline?: Stop[];
    vehicleId: string;
    selectedOrder?: string;
    riskOrders?: string[];
    changes?: Schema["Change"][];
    candidate?: boolean;
    planId?: string | null;
    planVersion?: number | null;
    planStatus?: string;
    nextOrder?: string;
    geography?: GeographicRouteData;
    demoMap?: Schema["DemoMapOutput"];
    referenceMap?: Schema["DemoMapOutput"];
    dataLoading?: boolean;
    dataError?: string;
    localSample?: boolean;
    mapConfig?: GeographicMapConfig;
    compact?: boolean;
    title?: string;
    dataMode?: string;
    inspect?: boolean;
  }>(),
  {
    selectedOrder: "",
    candidate: false,
    planId: null,
    inspect: true,
    title: "城市配送地图",
  },
);
const emit = defineEmits<{
  "update:selectedOrder": [string];
  select: [string];
  "select-node": [{ nodeId: string; vehicleId?: string }];
  refresh: [];
}>();
const root = ref<HTMLElement>();
const amap = ref<{ fit: () => void }>();
let previousFocus: HTMLElement | null = null;
let previousOverflow = "";
const expanded = ref(false),
  focusOrder = ref(""),
  message = ref(""),
  retry = ref(0),
  mapReady = ref(false);
const mapModel = computed(() =>
  props.demoMap
    ? adaptWuhanMap({
        data: props.demoMap,
        reference: props.referenceMap,
        context: props.context,
        stops: props.stops,
        baseline: props.baseline,
        vehicleId: props.vehicleId,
        planId: props.planId,
        planVersion: props.planVersion,
        local: !!props.localSample,
      })
    : adaptMapData({
        data: props.geography,
        context: props.context,
        stops: props.stops,
        baseline: props.baseline,
        vehicleId: props.vehicleId,
        planId: props.planId,
        planVersion: props.planVersion,
        demoAllowed: props.dataMode === "demo",
      }),
);
const mappedData = computed(() => mapModel.value.markers.length > 0);
const routeKind = computed(() =>
  mapModel.value.layers.some((l) => l.geometryKind === "SCHEMATIC")
    ? "schematic"
    : mapModel.value.layers.length
      ? "road"
      : "empty",
);
function ready() {
  mapReady.value = true;
  message.value = "";
}

const selected = computed(() =>
  props.stops.find((s) => s.order_no === props.selectedOrder),
);
const order = computed(() =>
  props.context?.orders.find((o) => o.order_no === props.selectedOrder),
);
const vehicle = computed(() =>
  props.context?.vehicles.find((v) => v.vehicle_id === props.vehicleId),
);
function select(orderNo: string) {
  emit("update:selectedOrder", orderNo);
  emit("select", orderNo);
}
function failure(reason: string) {
  mapReady.value = false;
  message.value = reason;
}
function reload() {
  message.value = "";
  mapReady.value = false;
  retry.value++;
}
onUnmounted(() => {
  window.removeEventListener("keydown", escape);
  if (expanded.value)
    document.documentElement.style.overflow = previousOverflow;
});
onErrorCaptured(() => {
  failure("地图组件暂时无法加载，请重试。业务列表仍可使用。");
  return false;
});
watch(
  () => props.selectedOrder,
  (order) => {
    if (focusOrder.value) focusOrder.value = order;
  },
);
function escape(e: KeyboardEvent) {
  if (e.key === "Escape") expanded.value = false;
  if (e.key === "Tab" && expanded.value) {
    const all = [
      ...root.value!.querySelectorAll<HTMLElement>(
        'button:not(:disabled),[tabindex="0"]',
      ),
    ];
    const first = all[0],
      last = all.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  }
}
watch(expanded, async (value) => {
  if (value) {
    previousFocus = document.activeElement as HTMLElement;
    previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    window.addEventListener("keydown", escape);
    await nextTick();
    root.value
      ?.querySelector<HTMLElement>('[aria-label="收起配送地图"]')
      ?.focus();
  } else {
    document.documentElement.style.overflow = previousOverflow;
    window.removeEventListener("keydown", escape);
    await nextTick();
    previousFocus?.focus();
  }
});
watch(
  () => props.vehicleId,
  () => {
    focusOrder.value = "";
    message.value = "";
    emit("update:selectedOrder", "");
  },
);
watch(
  () => props.planId,
  () => {
    focusOrder.value = "";
  },
);
</script>
<template>
  <Teleport to="body" :disabled="!expanded">
    <section
      ref="root"
      :role="expanded ? 'dialog' : 'region'"
      :aria-modal="expanded ? true : undefined"
      class="map-view amap-map-view"
      :class="{ expanded, compact, candidate }"
      :aria-label="title"
    >
      <header class="map-header">
        <div>
          <span class="eyebrow"
            >配送地图 <span v-if="dataMode === 'demo'">/ 本地演示</span></span
          >
          <h3>{{ title }}</h3>
        </div>
        <div class="map-controls">
          <button
            class="icon-button"
            aria-label="刷新配送地图数据"
            :disabled="dataLoading"
            @click="emit('refresh')"
          >
            <RefreshCw :size="17" />
          </button>
          <button
            class="icon-button"
            :aria-label="
              selectedOrder && mappedData ? '聚焦选中配送节点' : '回到当前路线'
            "
            :disabled="!mapReady"
            @click="
              selectedOrder && mappedData
                ? (focusOrder = focusOrder ? '' : selectedOrder)
                : amap?.fit()
            "
          >
            <Focus :size="17" /></button
          ><button
            class="icon-button"
            :aria-label="expanded ? '收起配送地图' : '展开配送地图'"
            @click="expanded = !expanded"
          >
            <Minimize2 v-if="expanded" :size="17" /><Expand v-else :size="17" />
          </button>
        </div>
      </header>
      <div class="map-mode-bar real-map-bar">
        <span class="map-place-label"
          ><MapPin :size="13" />{{
            mapReady ? "高德地图" : "高德地图 · 等待加载"
          }}</span
        ><span class="map-plan-badge" :class="{ preview: candidate }">{{
          candidate
            ? "方案预览 · " + label(planStatus)
            : "当前有效路线 · REV " + (vehicle?.route_revision ?? "—")
        }}</span>
      </div>
      <div
        class="map-canvas"
        :class="{ focused: !!focusOrder }"
        :data-map-ready="mapReady"
        :data-route-kind="routeKind"
        :data-plan="planId || 'current'"
      >
        <AmapMap
          ref="amap"
          :model="mapModel"
          :selected-order="selectedOrder"
          :focus-order="focusOrder"
          :vehicle-id="vehicleId"
          :plan-id="planId"
          :plan-version="planVersion"
          :candidate="candidate"
          :risk-orders="riskOrders"
          :changed-orders="changes?.map((c) => c.order_no) || []"
          :next-order="nextOrder"
          :retry="retry"
          @select="select"
          @select-node="emit('select-node', $event)"
          @ready="ready"
          @failure="failure"
          @retry="reload"
        />
        <div v-if="message && !amap" class="real-map-error" role="status">
          <span>{{ message }}</span
          ><button @click="reload">重新加载</button>
        </div>
      </div>
      <div
        v-if="dataLoading || dataError"
        class="map-data-feedback"
        :class="{ error: !!dataError }"
        role="status"
      >
        {{ dataError || "配送地图数据加载中，任务列表仍可使用…" }}
      </div>
      <div v-if="selected && inspect" class="map-node-inspector">
        <div class="inspector-heading">
          <span><Package :size="14" /> 关联配送任务</span
          ><button
            class="icon-button"
            aria-label="取消节点选择"
            @click="select('')"
          >
            <X :size="14" />
          </button>
        </div>
        <b>{{ order?.facts.name || selected.node_id }}</b>
        <div class="inspector-ids mono">
          {{ selected.order_no }} / {{ selected.node_id }}
        </div>
        <div class="inspector-facts">
          <span
            >停靠顺序 <b>{{ selected.sequence_no }}</b></span
          ><span>{{ candidate ? "候选安排" : label(selected.status) }}</span>
        </div>
        <small
          ><Clock3 :size="11" /> 收货截止
          {{ time(order?.latest_arrival) }}</small
        >
        <p
          v-if="riskOrders?.includes(selected.order_no)"
          class="inspector-risk"
        >
          异常影响节点 · 请核对时间窗风险
        </p>
      </div>
      <p
        v-if="mapModel.issues.length && (geography || demoMap)"
        class="amap-adapter-warning"
      >
        {{ mapModel.issues.join("；") }}
      </p>
      <footer class="map-footer">
        <div class="map-legend">
          <span><i class="legend-current" />所选路线</span
          ><span v-if="candidate"><i class="legend-baseline" />原计划</span
          ><span><i class="legend-risk" />异常任务</span
          ><span><i class="legend-next" />下一站 / 变更</span>
        </div>
        <small>{{
          mapModel.demo
            ? "武汉模拟配送 · 模拟连线，不代表实际行驶道路"
            : mappedData
              ? "GCJ-02 · " + geography?.version
              : "真实地理数据尚未接入 · 任务列表仍可使用"
        }}</small>
      </footer>
      <div v-if="expanded" class="expanded-safety">
        请在安全停车后进行详细操作。地图道路预览不替代后端业务 ETA。
      </div>
    </section>
  </Teleport>
</template>
