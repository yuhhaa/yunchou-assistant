<script setup lang="ts">
import { computed } from "vue";
import { MapPin, Check, ArrowRight } from "lucide-vue-next";
import type { Schema } from "../api/types";
import { useOps } from "../stores/ops";
import { label } from "../lib/format";
const props = defineProps<{
  stops: Schema["RouteStopInput"][];
  highlight?: boolean;
  changes?: Schema["Change"][];
  interactive?: boolean;
  selectedOrder?: string;
}>();
const emit = defineEmits<{ select: [string] }>();
const ops = useOps();
const sorted = computed(() =>
  [...props.stops].sort(
    (a, b) =>
      a.vehicle_id.localeCompare(b.vehicle_id) || a.sequence_no - b.sequence_no,
  ),
);
function name(orderNo: string) {
  const o = ops.context?.orders.find((o) => o.order_no === orderNo);
  return (o?.facts?.name as string) || o?.delivery_node_id || orderNo;
}
</script>
<template>
  <div class="route-timeline" :class="{ highlight }">
    <div class="timeline-origin">
      <span class="origin-icon"><MapPin :size="15" /></span>
      <div><b>配送出发节点</b><small>业务顺序示意 · 非地理导航</small></div>
    </div>
    <button
      v-for="stop in sorted"
      :key="stop.stop_id"
      class="route-stop"
      :class="{
        changed: changes?.some((c) => c.order_no === stop.order_no),
        completed: stop.status === 'COMPLETED',
        'timeline-selected': selectedOrder === stop.order_no,
      }"
      :disabled="!interactive"
      @click="emit('select', stop.stop_id)"
    >
      <span class="stop-dot"
        ><Check v-if="stop.status === 'COMPLETED'" :size="14" /><template
          v-else
          >{{ stop.sequence_no.toString().padStart(2, "0") }}</template
        ></span
      ><span class="stop-body"
        ><b>{{ name(stop.order_no) }}</b
        ><small
          >{{ stop.order_no }} <span> / {{ stop.vehicle_id }}</span></small
        ><span
          v-if="changes?.find((c) => c.order_no === stop.order_no)"
          class="change-caption"
          >顺序
          {{
            changes?.find((c) => c.order_no === stop.order_no)?.old_sequence_no
          }}
          <ArrowRight :size="11" /> {{ stop.sequence_no }} · 停靠顺序调整</span
        ></span
      ><span class="stop-state">{{
        changes?.some((c) => c.order_no === stop.order_no)
          ? "已调整"
          : highlight
            ? "保留原安排"
            : label(stop.status)
      }}</span>
    </button>
    <div v-if="!stops.length" class="empty-inline">暂无授权路线数据</div>
  </div>
</template>
