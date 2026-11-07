<script setup lang="ts">
import { computed } from "vue";
import type { Schema } from "../../api/types";
const props = defineProps<{ stops: Schema["RouteStopInput"][] }>();
const completed = computed(
  () => props.stops.filter((s) => s.status === "COMPLETED").length,
);
const arrived = computed(
  () => props.stops.filter((s) => s.status === "ARRIVED").length,
);
</script>
<template>
  <section class="delivery-progress" aria-label="配送执行进度">
    <header>
      <span>执行进度 <i class="progress-live-dot" /></span
      ><b class="mono"
        >{{ completed }}<small> / {{ stops.length }} 站完成</small></b
      >
    </header>
    <div
      class="delivery-progress-track"
      role="img"
      :aria-label="`${completed}站完成，${arrived}站已到达，共${stops.length}站`"
    >
      <span
        v-for="s in stops"
        :key="s.stop_id"
        :class="s.status.toLowerCase()"
        :title="s.order_no + ' · ' + s.status"
      />
    </div>
    <p>
      {{ arrived ? `${arrived}站已到达，等待完成反馈` : "进度以配送反馈为准"
      }}<span>到达 ≠ 配送完成</span>
    </p>
  </section>
</template>
