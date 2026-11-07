<script setup lang="ts">
import { computed } from "vue";
import type { Analysis, Plan } from "../../api/types";
const props = defineProps<{
  plan: Plan;
  analysis?: Analysis | null;
  vehicleId: string;
}>();
const comparable = computed(
  () =>
    props.analysis?.analysis_id === props.plan.analysis_id &&
    props.analysis?.incident_id === props.plan.incident_id &&
    props.analysis?.analysis_status === "VALID" &&
    props.analysis?.incident_version === props.plan.incident_version,
);
const originalDelay = computed(
  () =>
    props.analysis?.order_impacts.reduce((n, o) => n + o.delay_minutes, 0) ?? 0,
);
const domain = computed(() =>
  Math.max(1, originalDelay.value, props.plan.metrics.total_delay_minutes),
);
const stops = computed(() =>
  props.plan.target_routes.filter((s) => s.vehicle_id === props.vehicleId),
);
const changed = computed(
  () => new Set(props.plan.changes.map((c) => c.order_no)),
);
const preserved = computed(
  () => stops.value.filter((s) => !changed.value.has(s.order_no)).length,
);
</script>
<template>
  <section class="plan-instrument" aria-label="候选方案数据对照">
    <header>
      <span class="eyebrow">方案对照</span
      ><span class="mono">V{{ plan.plan_version }}</span>
    </header>
    <h3>延误与任务调整</h3>
    <div v-if="comparable" class="delay-comparison">
      <div>
        <span>异常后原安排<small>分析逐单延误合计</small></span
        ><b class="mono">{{ originalDelay }} <small>min</small></b
        ><i class="comparison-track"
          ><span :style="{ width: (originalDelay / domain) * 100 + '%' }"
        /></i>
      </div>
      <div class="candidate-measure">
        <span>所选恢复候选<small>服务端总延误指标</small></span
        ><b class="mono"
          >{{ plan.metrics.total_delay_minutes }} <small>min</small></b
        ><i class="comparison-track"
          ><span
            :style="{
              width: (plan.metrics.total_delay_minutes / domain) * 100 + '%',
            }"
        /></i>
      </div>
    </div>
    <p v-else class="instrument-missing">
      当前缺少同一分析版本，暂不绘制延误对照。
    </p>
    <div class="preservation-measure">
      <b class="mono"
        >{{ preserved }}<small> / {{ stops.length }}</small></b
      ><span>所选车辆任务保留原安排</span>
    </div>
    <div
      class="preservation-blocks"
      role="img"
      :aria-label="`${stops.length}个任务中${preserved}个保留原安排`"
    >
      <span
        v-for="s in stops"
        :key="s.stop_id"
        :class="{ changed: changed.has(s.order_no) }"
        :title="s.order_no + (changed.has(s.order_no) ? ' 已调整' : ' 保留')"
      />
    </div>
    <footer>
      <span><i />保留</span><span><i class="changed" />调整</span
      ><small>仅呈现服务端数据，不估算候选ETA</small>
    </footer>
  </section>
</template>
