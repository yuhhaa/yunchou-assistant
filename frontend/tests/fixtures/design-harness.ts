// Controlled test states only. Not imported by product routes.
import { createApp, ref, h } from "vue";
import ProcessLedger from "../../src/components/design/ProcessLedger.vue";
import PlanInstrument from "../../src/components/design/PlanInstrument.vue";
import DeliveryProgress from "../../src/components/design/DeliveryProgress.vue";

import { mockRequest, configureDemoScenario } from "../../src/mock/transport";
import type {
  Analysis,
  Context,
  Incident,
  Job,
  Plan,
  Schema,
} from "../../src/api/types";
import "../../src/styles.css";
import "../../src/design-upgrade.css";
configureDemoScenario("wuhan", "DISPATCHER");
const context = await mockRequest<Context>("/delivery-context");
const incident = (await mockRequest<{ incidents: Incident[] }>("/incidents"))
  .incidents[0]!;
const aJob = await mockRequest<Schema["QueuedOutput"]>("/impact-analyses", {
  incident_id: incident.incident_id,
});
const a = (await mockRequest<Job>("/jobs/" + aJob.job_id)).result as Analysis;
const pJob = await mockRequest<Schema["QueuedOutput"]>("/recovery-plans", {
  incident_id: incident.incident_id,
});
const plan = (
  (await mockRequest<Job>("/jobs/" + pJob.job_id))
    .result as Schema["GenerationOutput"]
).plans[0]!;
createApp({
  setup() {
    const analysis = ref<Analysis | null>(null),
      plans = ref<Plan[]>([]),
      job = ref<Job | null>(null),
      chartAnalysis = ref<Analysis | null>(a),
      stops = ref(context.route_stops.filter((s) => s.vehicle_id === "V01"));
    const button = (name: string, click: () => void) =>
      h("button", { onClick: click, style: "padding:12px;margin:4px" }, name);
    return () =>
      h("main", { style: "max-width:1100px;padding:25px" }, [
        h("p", "TEST FIXTURES — not business runtime"),
        button("分析已返回", () => (analysis.value = a)),
        button("候选已返回", () => (plans.value = [structuredClone(plan)])),
        button(
          "调度已批准",
          () =>
            (plans.value = [
              { ...structuredClone(plan), status: "APPROVED_PENDING_ACK" },
            ]),
        ),
        button(
          "后端ACTIVE",
          () =>
            (plans.value = [{ ...structuredClone(plan), status: "ACTIVE" }]),
        ),
        button("当前作业失败", () => {
          analysis.value = null;
          plans.value = [];
          job.value = {
            job_id: "DESIGN_JOB",
            job_type: "IMPACT_ANALYSIS",
            result_kind: "IMPACT_ANALYSIS",
            job_status: "FAILED",
            result: null,
            error: null,
            poll_after_seconds: 2,
          };
        }),
        button("未知作业状态", () => {
          if (job.value) job.value = { ...job.value, job_status: "UNKNOWN" };
        }),
        button("移除图表分析", () => (chartAnalysis.value = null)),
        button(
          "分析版本不一致",
          () => (chartAnalysis.value = { ...a, incident_version: 999 }),
        ),
        button("到达第一站", () => (stops.value[0]!.status = "ARRIVED")),
        button("完成第一站", () => (stops.value[0]!.status = "COMPLETED")),
        h("div", { style: "max-width:360px" }, [
          h(ProcessLedger, {
            incident,
            analysis: analysis.value,
            plans: plans.value,
            job: job.value,
            jobAssociated: true,
          }),
          h(PlanInstrument, {
            plan,
            analysis: chartAnalysis.value,
            vehicleId: "V01",
          }),
          h(DeliveryProgress, { stops: stops.value }),
        ]),
      ]);
  },
}).mount("#app");
