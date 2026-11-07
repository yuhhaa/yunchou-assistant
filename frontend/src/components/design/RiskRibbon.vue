<script setup lang="ts">
import { computed } from "vue";
import type { Analysis } from "../../api/types";
const props = defineProps<{ analysis: Analysis; selectedOrder?: string }>();
defineEmits<{ select: [string] }>();
const max = computed(() =>
  Math.max(1, ...props.analysis.order_impacts.map((i) => i.delay_minutes)),
);
</script>
<template>
  <section class="risk-ribbon" aria-label="订单时间窗风险分布">
    <div class="risk-ribbon-title">
      <span class="eyebrow">订单延误</span><b>订单时间窗风险</b
      ><small>柱长对应分析返回的延误分钟</small>
    </div>
    <div class="risk-bars">
      <button
        v-for="item in analysis.order_impacts"
        :key="item.order_no"
        :class="{
          selected: selectedOrder === item.order_no,
          risk: item.risk_type !== 'NONE',
        }"
        :aria-pressed="selectedOrder === item.order_no"
        :aria-label="`${item.order_no}，延误${item.delay_minutes}分钟，${item.risk_type === 'NONE' ? '无风险' : '存在风险'}`"
        @click="$emit('select', item.order_no)"
      >
        <span class="mono">{{ item.order_no }}</span
        ><span class="risk-bar-track"
          ><i
            :style="{
              width: (Math.max(0, item.delay_minutes) / max) * 100 + '%',
            }" /></span
        ><b class="mono">{{ item.delay_minutes }}<small> min</small></b
        ><span class="risk-caption">{{
          item.risk_type === "NONE" ? "无风险" : "需关注"
        }}</span>
      </button>
    </div>
  </section>
</template>
