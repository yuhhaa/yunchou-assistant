<script setup lang="ts">
import { computed, ref, useId, onMounted, onUnmounted } from "vue";
import type { Context, Schema } from "../../api/types";
import type { Stop } from "./types";
const props = defineProps<{
  context: Context | null;
  stops: Stop[];
  baseline?: Stop[];
  vehicleId: string;
  selectedOrder: string;
  riskOrders?: string[];
  changes?: Schema["Change"][];
  candidate?: boolean;
  nextOrder?: string;
  focusOrder?: string;
}>();
const emit = defineEmits<{ select: [string] }>();
const patternId = useId().replaceAll(":", "");
const hovered = ref("");
const svg = ref<SVGSVGElement>();
const narrow = ref(false);
let resizeObserver: ResizeObserver | undefined;
onMounted(() => {
  resizeObserver = new ResizeObserver((entries) => {
    narrow.value = (entries[0]?.contentRect.width || 900) < 700;
  });
  if (svg.value) resizeObserver.observe(svg.value);
});
onUnmounted(() => resizeObserver?.disconnect());
const origin = computed(() => ({
  x: narrow.value ? 48 : 90,
  y: narrow.value ? 190 : 240,
}));
const columns = computed(() =>
  narrow.value
    ? 2
    : Math.min(4, Math.max(2, Math.ceil(sorted.value.length / 2))),
);
const sorted = computed(() =>
  [...props.stops]
    .filter((s) => s.vehicle_id === props.vehicleId)
    .sort((a, b) => a.sequence_no - b.sequence_no),
);
// Stable abstract slots are derived from the baseline sequence, never from invented geographic coordinates.
const slots = computed(() => {
  const stable = [...(props.baseline?.length ? props.baseline : props.stops)]
    .filter((s) => s.vehicle_id === props.vehicleId)
    .sort((a, b) => a.sequence_no - b.sequence_no);
  const ids = [...new Set([...stable, ...sorted.value].map((s) => s.order_no))];
  const count = ids.length;
  const columnCount = columns.value;
  return new Map(
    ids.map((id, i) => {
      const row = Math.floor(i / columnCount);
      const col = i % columnCount;
      return [
        id,
        {
          x:
            (narrow.value ? 150 : 230) +
            (row % 2 ? columnCount - 1 - col : col) *
              ((narrow.value ? 210 : 500) / Math.max(1, columnCount - 1)),
          y: row * (narrow.value ? 140 : 175) + (narrow.value ? 100 : 155),
        },
      ];
    }),
  );
});
const diagramHeight = computed(() =>
  Math.max(
    narrow.value ? 355 : 480,
    Math.ceil(slots.value.size / columns.value) * (narrow.value ? 140 : 175) +
      (narrow.value ? 75 : 165),
  ),
);
const nodes = computed(() =>
  sorted.value.map((s) => ({ ...s, ...slots.value.get(s.order_no)! })),
);
function routePath(stops: Stop[]) {
  const points = stops
    .filter((s) => s.vehicle_id === props.vehicleId)
    .sort((a, b) => a.sequence_no - b.sequence_no)
    .map((s) => slots.value.get(s.order_no))
    .filter(Boolean);
  let d = props.context?.warehouse_node_id
    ? `M ${origin.value.x} ${origin.value.y}`
    : points.length
      ? `M ${points[0]!.x} ${points[0]!.y}`
      : "";
  for (const p of props.context?.warehouse_node_id ? points : points.slice(1)) {
    d += ` L ${p!.x} ${p!.y}`;
  }
  return d;
}
const currentPath = computed(() => routePath([...props.stops]));
const baselinePath = computed(() => routePath([...(props.baseline || [])]));
const vehicle = computed(() =>
  props.context?.vehicles.find((v) => v.vehicle_id === props.vehicleId),
);
const position = computed(
  () =>
    nodes.value.find((n) => n.node_id === vehicle.value?.location_node_id) ||
    (vehicle.value?.location_node_id === props.context?.warehouse_node_id
      ? origin.value
      : null),
);
function name(orderNo: string) {
  const order = props.context?.orders.find((o) => o.order_no === orderNo);
  return String(order?.facts.name || order?.delivery_node_id || orderNo);
}
const focusPoint = computed(() =>
  nodes.value.find((n) => n.order_no === props.focusOrder),
);
const viewbox = computed(() =>
  focusPoint.value
    ? `${Math.max(0, focusPoint.value.x - 270)} ${Math.max(0, focusPoint.value.y - 190)} 540 380`
    : `0 0 ${narrow.value ? 460 : 900} ${diagramHeight.value}`,
);
</script>
<template>
  <svg
    ref="svg"
    class="route-network-svg"
    :class="{ narrow }"
    :viewBox="viewbox"
    role="group"
    aria-label="配送节点网络图，抽象顺序示意，非地理地图"
  >
    <defs>
      <pattern
        :id="'grid' + patternId"
        width="26"
        height="26"
        patternUnits="userSpaceOnUse"
      >
        <circle cx="1" cy="1" r=".8" fill="currentColor" opacity=".12" />
      </pattern>
    </defs>
    <rect width="100%" height="100%" :fill="`url(#grid${patternId})`" />
    <g class="network-landscape" aria-hidden="true">
      <path d="M-20 345 Q160 290 290 400 T600 420 T950 320" />
      <path d="M-20 363 Q160 308 290 418 T600 438 T950 338" />
      <rect x="350" y="65" width="140" height="27" rx="13" />
      <rect x="550" y="230" width="180" height="30" rx="15" />
      <rect x="110" y="370" width="70" height="22" rx="11" />
    </g>
    <path
      v-if="candidate && baseline?.length"
      class="network-baseline"
      :d="baselinePath"
    />
    <path
      v-if="nodes.length"
      :key="currentPath"
      class="network-route"
      :class="{ candidate }"
      :d="currentPath"
    />
    <g class="warehouse-node" :transform="`translate(${origin.x} ${origin.y})`">
      <rect x="-28" y="-28" width="56" height="56" rx="16" />
      <path d="M-13 -3 L0 -13 L13 -3 V13 H-13 Z M-5 13 V3 H5 V13" />
      <text y="53" text-anchor="middle">
        {{ context?.warehouse_node_id ? "配送仓库" : "起点待确认" }}
      </text>
      <text y="70" class="node-id" text-anchor="middle">
        {{ context?.warehouse_node_id || "NO ORIGIN DATA" }}
      </text>
    </g>
    <g
      v-for="node in nodes"
      :key="node.stop_id"
      class="network-node"
      :class="{
        selected: selectedOrder === node.order_no,
        risk: riskOrders?.includes(node.order_no),
        changed: changes?.some((c) => c.order_no === node.order_no),
        next: nextOrder === node.order_no,
        completed: node.status === 'COMPLETED',
      }"
      :transform="`translate(${node.x} ${node.y})`"
      role="button"
      tabindex="0"
      :aria-label="`选择节点 ${node.order_no} ${name(node.order_no)}，停靠顺序 ${node.sequence_no}`"
      :aria-pressed="selectedOrder === node.order_no"
      @click="emit('select', node.order_no)"
      @keydown.enter.prevent="emit('select', node.order_no)"
      @keydown.space.prevent="emit('select', node.order_no)"
      @mouseenter="hovered = node.order_no"
      @mouseleave="hovered = ''"
    >
      <circle class="node-halo" r="33" />
      <circle class="node-core" r="21" />
      <text class="node-sequence" y="4" text-anchor="middle">
        {{ String(node.sequence_no).padStart(2, "0") }}
      </text>
      <text class="node-name" y="54" text-anchor="middle">
        {{
          name(node.order_no).length > 12
            ? name(node.order_no).slice(0, 12) + "…"
            : name(node.order_no)
        }}
      </text>
      <text class="node-id" y="72" text-anchor="middle">
        {{ node.node_id }} · {{ node.order_no }}
      </text>
      <g
        v-if="riskOrders?.includes(node.order_no)"
        class="node-risk"
        transform="translate(20 -20)"
      >
        <circle r="9" />
        <text y="4" text-anchor="middle">!</text>
      </g>
      <text
        v-if="nextOrder === node.order_no"
        class="next-label"
        y="-43"
        text-anchor="middle"
      >
        下一站
      </text>
      <title>
        {{ name(node.order_no) }} · {{ node.order_no }} · 停靠
        {{ node.sequence_no
        }}{{ riskOrders?.includes(node.order_no) ? " · 异常影响节点" : "" }}
      </title>
    </g>
    <g
      v-if="position"
      class="network-vehicle"
      :transform="`translate(${position.x - 36} ${position.y - 45})`"
    >
      <rect x="-18" y="-14" width="36" height="28" rx="9" />
      <path d="M-10 -5 H3 V6 H-10 Z M3 -2 H8 L11 3 V6 H3 M-6 7 H-5 M6 7 H7" />
      <title>
        {{ vehicleId }} 最新确认节点 {{ vehicle?.location_node_id }}，非实时定位
      </title>
    </g>
    <text
      v-if="!nodes.length"
      x="450"
      y="225"
      text-anchor="middle"
      class="network-empty"
    >
      当前没有授权配送节点
    </text>
  </svg>
</template>
