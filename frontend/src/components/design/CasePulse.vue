<script setup lang="ts">
import { computed } from "vue";
import { ArrowUpRight } from "lucide-vue-next";
import type { Incident } from "../../api/types";
const props = defineProps<{ incidents: Incident[] }>();
const emit = defineEmits<{ select: [string] }>();
const groups = computed(() => [
  {
    name: "待补充 / 待分析",
    tone: "attention",
    items: props.incidents.filter((i) =>
      ["NEEDS_INFO", "READY_FOR_ANALYSIS", "REPORTED"].includes(i.status),
    ),
  },
  {
    name: "等待人工确认",
    tone: "waiting",
    items: props.incidents.filter((i) =>
      ["WAITING_APPROVAL", "WAITING_ACK", "MANUAL_REVIEW"].includes(i.status),
    ),
  },
  {
    name: "执行与其他状态",
    tone: "neutral",
    items: props.incidents.filter(
      (i) =>
        ![
          "NEEDS_INFO",
          "READY_FOR_ANALYSIS",
          "REPORTED",
          "WAITING_APPROVAL",
          "WAITING_ACK",
          "MANUAL_REVIEW",
        ].includes(i.status),
    ),
  },
]);
</script>
<template>
  <section class="case-pulse" aria-label="授权事件状态概览">
    <div class="pulse-label">
      <span class="eyebrow">事件状态</span><b>授权事件</b>
    </div>
    <button
      v-for="g in groups"
      :key="g.name"
      :class="g.tone"
      :disabled="!g.items.length"
      @click="emit('select', g.items[0]!.incident_id)"
    >
      <span class="pulse-dot" /><span>{{ g.name }}</span
      ><b class="mono">{{ String(g.items.length).padStart(2, "0") }}</b
      ><ArrowUpRight :size="14" />
    </button>
  </section>
</template>
