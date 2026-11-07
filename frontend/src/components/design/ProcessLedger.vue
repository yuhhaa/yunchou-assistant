<script setup lang="ts">
import { computed, ref } from "vue";
import {
  Check,
  ChevronDown,
  LoaderCircle,
  TriangleAlert,
} from "lucide-vue-next";
import type { Incident, Analysis, Plan, Job, Schema } from "../../api/types";
import { label, time } from "../../lib/format";
const props = defineProps<{
  incident: Incident;
  analysis?: Analysis | null;
  plans: Plan[];
  job?: Job | null;
  jobAssociated?: boolean;
  now?: number;
  generation?: Schema["GenerationOutput"] | null;
}>();
const open = ref<number | null>(null);
const validPlans = computed(() =>
  props.plans.filter(
    (p) =>
      p.incident_id === props.incident.incident_id &&
      p.incident_version === props.incident.incident_version &&
      (p.status !== "PENDING_APPROVAL" ||
        Date.parse(p.expires_at) > (props.now ?? Date.now())) &&
      !["STALE", "EXPIRED", "REJECTED", "ACTIVATION_FAILED"].includes(p.status),
  ),
);
const active = computed(() =>
  validPlans.value.find((p) => p.status === "ACTIVE"),
);
const approved = computed(
  () =>
    active.value ||
    validPlans.value.find((p) => p.status === "APPROVED_PENDING_ACK"),
);
const relevantJob = computed(() =>
  props.jobAssociated ||
  (props.job?.result &&
    "incident_id" in props.job.result &&
    props.job.result.incident_id === props.incident.incident_id)
    ? props.job
    : null,
);
const steps = computed(() => {
  const a =
      props.analysis?.incident_id === props.incident.incident_id &&
      props.analysis.incident_version === props.incident.incident_version &&
      props.analysis.analysis_status === "VALID"
        ? props.analysis
        : null,
    plans = validPlans.value;
  const data = [
    {
      name: "异常登记",
      done: true,
      detail: `${props.incident.incident_id} · V${props.incident.incident_version} · ${time(props.incident.reported_at)}`,
    },
    {
      name: "影响分析",
      done: !!a,
      detail: a
        ? `${a.analysis_id} · ${a.risk_order_count} 单存在风险`
        : "等待有效的影响分析；没有结果时不预估风险。",
    },
    {
      name: "方案生成",
      done: plans.length > 0,
      detail: plans.length
        ? `${plans.length} 个当前版本候选；生成不代表已经批准。`
        : props.generation?.solution_status === "NO_FEASIBLE_PLAN_IN_SEARCH" &&
            props.generation.analysis_id === a?.analysis_id
          ? "本次搜索范围内无可行候选。"
          : "等待候选计算结果。",
    },
    {
      name: "调度审批",
      done: !!approved.value,
      detail: approved.value
        ? `${approved.value.plan_id} · V${approved.value.plan_version} · ${label(approved.value.status)}`
        : "需要调度员明确确认，AI回复不能代替审批。",
    },
    {
      name: "司机确认",
      done: !!active.value,
      detail: active.value
        ? "后端已确认新计划生效。"
        : approved.value
          ? "等待必要司机回执及服务端复核；不推测已接收人数。"
          : "调度批准后进入司机确认阶段。",
    },
    {
      name: "计划生效",
      done: !!active.value,
      detail: active.value
        ? `${active.value.plan_id} · ACTIVE；计划生效不等于配送完成。`
        : "仅后端返回ACTIVE，才显示生效。",
    },
  ];
  const current = data.findIndex((s) => !s.done);
  return data.map((s, i) => {
    const job = relevantJob.value;
    const matches =
      job &&
      ((i === 1 && job.job_type === "IMPACT_ANALYSIS") ||
        (i === 2 && job.job_type === "PLAN_GENERATION"));
    const state = s.done
      ? "done"
      : matches && job.job_status === "FAILED"
        ? "failed"
        : matches && job.job_status === "UNKNOWN"
          ? "unknown"
          : matches && ["RUNNING", "QUEUED"].includes(job.job_status)
            ? "running"
            : i === current
              ? "current"
              : "waiting";
    return {
      ...s,
      state,
      status: s.done
        ? "已确认"
        : state === "failed"
          ? "失败"
          : state === "unknown"
            ? "待核实"
            : state === "running"
              ? "处理中"
              : i === current
                ? "当前步骤"
                : "等待",
    };
  });
});
</script>
<template>
  <section class="process-ledger" aria-label="异常处置六步流程">
    <header>
      <span class="eyebrow">处置进度</span><span>查看步骤详情</span>
    </header>
    <div
      v-for="(step, i) in steps"
      :key="step.name"
      class="ledger-step"
      :class="step.state"
    >
      <button
        :aria-expanded="open === i"
        :aria-controls="`step-${incident.incident_id}-${i}`"
        :aria-current="step.state === 'current' ? 'step' : undefined"
        @click="open = open === i ? null : i"
      >
        <span class="ledger-icon"
          ><Check v-if="step.state === 'done'" :size="12" /><TriangleAlert
            v-else-if="['failed', 'unknown'].includes(step.state)"
            :size="12"
          /><LoaderCircle
            v-else-if="step.state === 'running'"
            :size="12"
            class="spinning"
          /><span v-else class="mono">{{
            String(i + 1).padStart(2, "0")
          }}</span></span
        >
        <b>{{ step.name }}</b
        ><small>{{ step.status }}</small
        ><ChevronDown :size="12" class="ledger-chevron" />
      </button>
      <p
        v-if="open === i"
        :id="`step-${incident.incident_id}-${i}`"
        class="ledger-evidence"
      >
        {{ step.detail }}
      </p>
    </div>
  </section>
</template>
