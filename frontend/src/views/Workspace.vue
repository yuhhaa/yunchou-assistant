<script setup lang="ts">
import { computed, ref, watch, onUnmounted, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowRight,
  ArrowUpRight,
  ArrowDown,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  GitCompareArrows,
  Layers2,
  MapPin,
  MoveUpRight,
  Package,
  Plus,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  MessageSquareText,
  Truck,
  TriangleAlert,
  X,
  Route,
  Send,
  RefreshCw,
} from "lucide-vue-next";
import { useOps } from "../stores/ops";
import { label, time, number } from "../lib/format";
import RouteTimeline from "../components/RouteTimeline.vue";
import MapView from "../components/maps/MapView.vue";
import CasePulse from "../components/design/CasePulse.vue";
import ProcessLedger from "../components/design/ProcessLedger.vue";
import RiskRibbon from "../components/design/RiskRibbon.vue";
import PlanInstrument from "../components/design/PlanInstrument.vue";
import DeliveryProgress from "../components/design/DeliveryProgress.vue";
import { useDeliveryMaps } from "../composables/useDeliveryMaps";
import type { Schema } from "../api/types";
const ops = useOps(),
  route = useRoute(),
  router = useRouter();
const filter = ref("ALL"),
  showReport = ref(false),
  showNetwork = ref(false),
  dialog = ref<
    "approve" | "reject" | "accept" | "decline" | "handoff" | "verify" | null
  >(null),
  reason = ref("");
const eventType = ref<
    "ROAD_DELAY" | "RECEIVING_WINDOW_CHANGE" | "VEHICLE_BREAKDOWN"
  >("ROAD_DELAY"),
  description = ref(""),
  delay = ref<number | null>(null),
  vehicleId = ref(""),
  orderNo = ref(""),
  windowStart = ref(""),
  windowEnd = ref("");
const selectedOrder = ref("");
const lifecycleJob = ref<{ incidentId: string; jobId: string }>();
async function runLifecycle(action: "analyze" | "generate") {
  const incidentId = ops.workspace?.incident.incident_id;
  if (!incidentId || ops.busy) return;
  const previous = ops.job?.job_id;
  const stop = watch(
    () => ops.job,
    (job) => {
      if (
        job &&
        job.job_id !== previous &&
        job.job_type ===
          (action === "analyze" ? "IMPACT_ANALYSIS" : "PLAN_GENERATION")
      )
        lifecycleJob.value = { incidentId, jobId: job.job_id };
    },
    { flush: "sync" },
  );
  try {
    await ops[action]();
  } finally {
    stop();
  }
}
const planMapMode = ref<"baseline" | "candidate">("candidate");
const driverTab = ref("today");
const selectedStop = ref("");
const p = computed(() => ops.selectedPlan),
  inc = computed(() => ops.workspace?.incident);
const activeOrders = computed(
  () =>
    ops.context?.orders.filter(
      (o) => o.status !== "COMPLETED" && o.status !== "CANCELLED",
    ) || [],
);
const ownStops = computed(
  () =>
    ops.context?.route_stops.filter(
      (s) => s.vehicle_id === ops.user?.vehicle_id,
    ) || [],
);
const baseStops = computed(
  () =>
    ops.context?.route_stops.filter(
      (s) => !inc.value || s.vehicle_id === inc.value.vehicle_id,
    ) || [],
);
const targetStops = computed(
  () =>
    p.value?.target_routes.filter(
      (s) => !inc.value || s.vehicle_id === inc.value.vehicle_id,
    ) || [],
);
const nextEta = computed(() =>
  ops.workspace?.plans.some((p) => p.status === "ACTIVE")
    ? null
    : ops.analysis?.order_impacts.find(
        (o) => o.order_no === nextStop.value?.order_no,
      )?.updated_eta,
);
const nextStop = computed(() =>
  [...ownStops.value]
    .sort((a, b) => a.sequence_no - b.sequence_no)
    .find((s) => s.status !== "COMPLETED" && s.status !== "CANCELLED"),
);
const filteredIncidents = computed(() =>
  ops.incidents.filter(
    (i) =>
      (filter.value === "ALL" ||
        (filter.value === "MANUAL"
          ? i.status === "MANUAL_REVIEW"
          : i.event_type === filter.value)) &&
      (!ops.search ||
        `${i.incident_id} ${i.vehicle_id} ${i.description}`
          .toLowerCase()
          .includes(ops.search.toLowerCase())),
  ),
);
const metricRows = computed(() =>
  p.value
    ? [
        ["预计总延误", p.value.metrics.total_delay_minutes, "min"],
        ["变更订单", p.value.metrics.changed_order_count, "单"],
        ["改派车辆订单", p.value.metrics.reassigned_order_count, "单"],
        ["停靠顺序逆序数", p.value.metrics.sequence_inversion_count, "次"],
        ["额外里程", p.value.metrics.extra_distance_m / 1000, "km"],
        ["仍有风险的订单", p.value.metrics.late_order_count, "单"],
      ]
    : [],
);
const preserved = computed(
  () =>
    targetStops.value.filter(
      (s) => !p.value?.changes.some((c) => c.order_no === s.order_no),
    ).length,
);
const currentVehicle = computed(() =>
  ops.context?.vehicles.find((v) => v.vehicle_id === ops.user?.vehicle_id),
);
const countdown = computed(() =>
  p.value
    ? Math.max(
        0,
        Math.ceil((Date.parse(p.value.expires_at) - ops.realTime) / 1000),
      )
    : 0,
);
const riskOrders = computed(
  () =>
    ops.analysis?.order_impacts
      .filter((o) => o.risk_type !== "NONE")
      .map((o) => o.order_no) || [],
);
const mapVehicle = computed(
  () => ops.selectedVehicleId || ops.user?.vehicle_id || "",
);
const mapStops = computed(() =>
  validMapPlan.value && p.value ? targetStops.value : baseStops.value,
);
const mapScope = ref(""),
  workbenchPlanId = ref("");
const workbenchPlan = computed(() =>
  ops.workspace?.plans.find((plan) => plan.plan_id === workbenchPlanId.value),
);
const mapPlan = computed(() =>
  route.path === "/plans" && planMapMode.value === "candidate"
    ? p.value
    : ["/incidents", "/driver"].includes(route.path)
      ? workbenchPlan.value
      : null,
);
const validMapPlan = computed(() => {
  const plan = mapPlan.value;
  if (
    !plan ||
    ops.expired(plan) ||
    !["PENDING_APPROVAL", "APPROVED_PENDING_ACK"].includes(plan.status)
  )
    return null;
  const reservation = ops.reservationExpiry[plan.plan_id];
  if (reservation && Date.parse(reservation) <= ops.realTime) return null;
  return {
    id: plan.plan_id,
    version: plan.plan_version,
    vehicleId: ops.workspace?.incident.vehicle_id || mapVehicle.value,
  };
});
const mapScopeForRequest = computed(() =>
  ops.isDriver
    ? ops.user?.vehicle_id || ""
    : route.path === "/plans"
      ? ops.workspace?.incident.vehicle_id || mapVehicle.value
      : mapScope.value,
);
const {
  current: currentMap,
  preview: previewMap,
  loading: mapLoading,
  previewLoading: previewMapLoading,
  error: mapError,
  previewError: previewMapError,
  refresh: refreshMaps,
} = useDeliveryMaps({
  enabled: () => route.path !== "/overview" || showNetwork.value,
  scope: mapScopeForRequest,
  preview: () => validMapPlan.value,
});
const visibleMap = computed(() =>
  validMapPlan.value ? previewMap.value : currentMap.value,
);
const visibleMapError = computed(
  () =>
    previewMapError.value ||
    mapError.value ||
    (mapPlan.value && mapPlan.value.status !== "ACTIVE" && !validMapPlan.value
      ? "候选已失效或不可预览，请重新分析；当前仅显示有效路线。"
      : ""),
);
async function changeMapVehicle(value: string) {
  workbenchPlanId.value = "";
  mapScope.value = value;
  selectedOrder.value = "";
  if (value) await ops.chooseVehicle(value);
}
async function chooseWorkbenchPlan(value: string) {
  if (value && inc.value && !ops.isDriver) {
    mapScope.value = inc.value.vehicle_id;
    if (ops.selectedVehicleId !== inc.value.vehicle_id)
      await ops.chooseVehicle(inc.value.vehicle_id);
  }
  workbenchPlanId.value = value;
}
async function selectMapNode(point: { nodeId: string; vehicleId?: string }) {
  if (
    !ops.isDriver &&
    point.vehicleId &&
    point.vehicleId !== ops.selectedVehicleId
  ) {
    mapScope.value = point.vehicleId;
    await ops.chooseVehicle(point.vehicleId);
  }
  const stop = ops.context?.route_stops.find(
    (s) =>
      s.node_id === point.nodeId &&
      s.status !== "COMPLETED" &&
      s.status !== "CANCELLED",
  );
  if (stop) selectOrder(stop.order_no);
}
watch(
  () => ops.workspace?.incident.incident_id,
  () => {
    workbenchPlanId.value = "";
  },
);
const confirmedAdjustments = computed(() =>
  ops.workspace?.plans.find(
    (plan) =>
      plan.status === "ACTIVE" &&
      plan.target_routes.some((s) => s.vehicle_id === ops.user?.vehicle_id),
  ),
);
const selectedImpact = computed(() =>
  ops.analysis?.order_impacts.find((o) => o.order_no === selectedOrder.value),
);
function selectOrder(orderNo: string) {
  selectedOrder.value = orderNo;
  selectedStop.value =
    ownStops.value.find((s) => s.order_no === orderNo)?.stop_id || "";
}
function selectStop(stopId: string) {
  selectedStop.value = stopId;
  selectedOrder.value =
    ops.context?.route_stops.find((s) => s.stop_id === stopId)?.order_no ||
    p.value?.target_routes.find((s) => s.stop_id === stopId)?.order_no ||
    "";
}
function scrollDriver(tab: string) {
  driverTab.value = tab;
  document
    .getElementById(
      tab === "today"
        ? "driver-today"
        : tab === "route"
          ? "driver-map"
          : "driver-feedback",
    )
    ?.scrollIntoView({
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      block: "start",
    });
}
watch(
  () => ops.selectedPlanId,
  () => {
    planMapMode.value = "candidate";
  },
);
watch(
  () => ops.selectedVehicleId,
  () => {
    selectedOrder.value = "";
    selectedStop.value = "";
  },
);
function nodeName(order: string | undefined) {
  const o = ops.context?.orders.find((o) => o.order_no === order);
  return String(o?.facts?.name || o?.delivery_node_id || "等待任务");
}
async function selectIncident(id: string) {
  await ops.guard(() => ops.openIncident(id));
}
function beginReport() {
  vehicleId.value =
    ops.user?.vehicle_id || ops.context?.vehicles[0]?.vehicle_id || "";
  showReport.value = true;
}
async function submitReport() {
  const body: Schema["IncidentInput"] = {
    mode: "CREATE",
    event_type: eventType.value,
    vehicle_id: vehicleId.value,
    description: description.value.trim(),
    ...(orderNo.value ? { order_no: orderNo.value } : {}),
    ...(eventType.value === "ROAD_DELAY" && delay.value !== null
      ? { delay_minutes: delay.value }
      : {}),
    ...(eventType.value === "RECEIVING_WINDOW_CHANGE"
      ? {
          reported_window_start: new Date(windowStart.value).toISOString(),
          reported_window_end: new Date(windowEnd.value).toISOString(),
        }
      : {}),
    occurred_at:
      ops.context?.clock_mode === "SIMULATION"
        ? ops.context.clock_time
        : new Date().toISOString(),
  };
  await ops.report(body);
  if (!ops.error) {
    showReport.value = false;
    description.value = "";
    delay.value = null;
  }
}
async function confirm() {
  if (dialog.value === "approve") await ops.planAction("approve");
  if (dialog.value === "reject") await ops.planAction("reject", reason.value);
  if (dialog.value === "accept")
    await ops.planAction("receipts", "", "ACCEPTED");
  if (dialog.value === "decline")
    await ops.planAction("receipts", reason.value, "REJECTED");
  if (dialog.value === "handoff") await ops.handoff(reason.value);
  if (dialog.value === "verify")
    await ops.verifyWindow(
      inc.value!.order_no!,
      new Date(windowStart.value).toISOString(),
      new Date(windowEnd.value).toISOString(),
    );
  if (!ops.error) {
    dialog.value = null;
    reason.value = "";
  }
}
watch(
  () => route.path,
  () => {
    ops.search = "";
    filter.value = "ALL";
  },
);
// Drivers must discover newly approved adjustments even when none is visible yet.
const refreshTimer = setInterval(() => {
  if (
    document.visibilityState === "visible" &&
    !ops.busy &&
    !showReport.value &&
    !dialog.value &&
    (ops.isDriver || ops.workspace?.plans.some((p) => p.status === "APPROVED_PENDING_ACK"))
  )
    ops.guard(ops.refresh);
}, 10000);
let previousFocus: HTMLElement | null = null;
watch(
  () => showReport.value || !!dialog.value,
  async (open) => {
    if (open) {
      previousFocus = document.activeElement as HTMLElement;
      await nextTick();
      document
        .querySelector<HTMLElement>(".modal button,.modal input")
        ?.focus();
    } else previousFocus?.focus();
  },
);
function modalKeys(event: KeyboardEvent) {
  if (!showReport.value && !dialog.value) return;
  if (event.key === "Escape") {
    showReport.value = false;
    dialog.value = null;
  }
  if (event.key === "Tab") {
    const nodes = [
      ...document.querySelectorAll<HTMLElement>(
        ".modal button:not(:disabled),.modal input:not(:disabled),.modal select:not(:disabled),.modal textarea:not(:disabled)",
      ),
    ];
    const first = nodes[0],
      last = nodes.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
}
document.addEventListener("keydown", modalKeys);
onUnmounted(() => {
  clearInterval(refreshTimer);
  document.removeEventListener("keydown", modalKeys);
});
</script>
<template>
  <main v-if="route.path === '/overview'" class="overview-main">
    <section class="city-stage">
      <div class="city-photo" />
      <div class="city-scrim" />
      <div class="stage-topline">
        <span><span class="status-dot" /> 城市配送总览</span
        ><span
          >{{ time(ops.context?.clock_time) }}
          <span class="thin">业务时间</span></span
        >
      </div>
      <div class="stage-intro">
        <h1>当前配送</h1>
        <p>选择车辆查看任务，或进入工作台处理异常。</p>
      </div>
      <aside class="fleet-glass">
        <div class="fleet-head">
          <span>配送任务 / {{ ops.selectedVehicleId }}</span
          ><span class="live-tag">{{
            ops.mode === "demo" ? "DEMO" : "SIMULATION"
          }}</span>
        </div>
        <div class="fleet-major">
          <b>{{ String(activeOrders.length).padStart(2, "0") }}</b
          ><span>当前车辆未完成订单</span>
        </div>
        <div class="fleet-divider" />
        <div class="fleet-pair">
          <div>
            <b>{{
              String(
                ops.fleet.filter((v) => v.status === "ON_TASK").length ?? 0,
              ).padStart(2, "0")
            }}</b
            ><small>车队在途车辆</small>
          </div>
          <div>
            <b class="lemon-text">{{
              String(ops.incidents.length).padStart(2, "0")
            }}</b
            ><small>当前授权异常</small>
          </div>
        </div>
        <button @click="router.push('/incidents')">
          进入异常工作台 <ArrowUpRight :size="17" />
        </button>
      </aside>

      <label v-if="ops.fleet.length" class="vehicle-scope"
        >车辆范围<select
          :value="ops.selectedVehicleId"
          aria-label="选择配送车辆"
          @change="
            ops.chooseVehicle(($event.target as HTMLSelectElement).value)
          "
        >
          <option
            v-for="v in ops.fleet"
            :key="v.vehicle_id"
            :value="v.vehicle_id"
          >
            {{ v.vehicle_no }} / {{ v.vehicle_id }}
          </option>
        </select></label
      >

      <div class="city-bottom">
        <span
          ><i /> {{ ops.mode === "demo" ? "独立演示数据" : "后端业务仿真" }} ·
          所有路线以配送快照为准</span
        ><button @click="showNetwork = !showNetwork">
          配送任务与地图 <ArrowUpRight :size="17" />
        </button>
      </div>
      <aside v-if="showNetwork" class="network-panel network-upgraded">
        <header>
          <h3>配送任务与地图</h3>
          <button
            class="icon-button"
            aria-label="关闭节点网络"
            @click="showNetwork = false"
          >
            <X :size="18" />
          </button>
        </header>
        <MapView
          :demo-map="visibleMap"
          :reference-map="validMapPlan ? currentMap : undefined"
          :data-loading="mapLoading || previewMapLoading"
          :data-error="visibleMapError"
          :local-sample="ops.mode === 'demo' && ops.demoScenario === 'wuhan'"
          @refresh="refreshMaps"
          @select-node="selectMapNode"
          :context="ops.context"
          :stops="baseStops"
          :vehicle-id="mapScope"
          v-model:selected-order="selectedOrder"
          :risk-orders="riskOrders"
          :data-mode="ops.mode"
          :compact="true"
          title="所选车辆配送网络"
        />
        <h4 class="sequence-list-heading">授权停靠顺序</h4>
        <RouteTimeline
          :stops="baseStops"
          :interactive="true"
          :selected-order="selectedOrder"
          @select="selectStop"
        />
      </aside>
    </section>
    <section class="overview-strip">
      <div class="strip-title">
        <h2>待处理异常</h2>
      </div>
      <div class="strip-event" v-if="inc">
        <span class="event-icon"><TriangleAlert :size="23" /></span>
        <div>
          <div class="inline-labels">
            <b>{{ label(inc.event_type) }}</b
            ><span class="badge warning">{{ label(inc.status) }}</span>
          </div>
          <p>{{ inc.vehicle_id }} <span>·</span> {{ inc.description }}</p>
        </div>
        <RouterLink
          to="/incidents"
          class="round-arrow"
          aria-label="处置当前异常"
          ><ArrowUpRight :size="24"
        /></RouterLink>
      </div>
      <div v-else class="strip-event">
        <CheckCircle2 :size="25" />
        <p>当前没有授权范围内的异常事件。</p>
      </div>
    </section>
    <section class="overview-secondary">
      <div>
        <Route :size="23" />
        <h3>恢复方案</h3>
        <p>查看候选的任务调整、约束检查和审批状态。</p>
      </div>
      <RouterLink to="/plans" class="secondary"
        >查看恢复方案 <ArrowRight :size="17"
      /></RouterLink>
    </section>
  </main>
  <main
    v-else-if="route.path === '/incidents'"
    class="business-main incidents-main"
  >
    <div class="page-heading">
      <div>
        <h1>
          异常工作台<span class="count-tag">{{ ops.incidents.length }}</span>
        </h1>
        <p>选择异常查看影响、生成方案并确认后续处置。</p>
      </div>
      <button class="primary" @click="beginReport">
        <Plus :size="17" /> 登记异常
      </button>
    </div>
    <CasePulse :incidents="ops.incidents" @select="selectIncident" />
    <div class="dispatcher-map-workspace">
      <section class="incident-map-workspace" aria-label="异常处置路线工作区">
        <div class="incident-map-main">
          <div class="route-workspace-heading">
            <div>
              <h2>{{ mapScope || "全部车辆" }}</h2>
            </div>
            <label
              >选择车辆<select
                aria-label="工作台选择车辆"
                :value="mapScope"
                @change="
                  changeMapVehicle(($event.target as HTMLSelectElement).value)
                "
              >
                <option value="">全车队概况</option>
                <option
                  v-for="v in ops.fleet"
                  :key="v.vehicle_id"
                  :value="v.vehicle_id"
                >
                  {{ v.vehicle_no }} / {{ v.vehicle_id }}
                </option>
              </select></label
            >
          </div>
          <div class="map-workspace-actions">
            <label v-if="ops.workspace?.plans.length"
              >地图方案<select
                aria-label="工作台地图方案"
                :value="workbenchPlanId"
                @change="
                  chooseWorkbenchPlan(
                    ($event.target as HTMLSelectElement).value,
                  )
                "
              >
                <option value="">当前路线</option>
                <option
                  v-for="plan in ops.workspace.plans"
                  :key="plan.plan_id"
                  :value="plan.plan_id"
                  :disabled="
                    ops.expired(plan) ||
                    !['PENDING_APPROVAL', 'APPROVED_PENDING_ACK'].includes(
                      plan.status,
                    )
                  "
                >
                  {{ plan.plan_id }} · {{ label(plan.status) }}
                </option>
              </select></label
            ><button
              class="quiet"
              :disabled="ops.busy || ops.context?.routing_ready !== true"
              @click="ops.refreshMapRouting"
              title="仅道路服务和路由均就绪时可手动刷新"
            >
              刷新道路路由</button
            ><small v-if="ops.context?.routing_ready !== true"
              >真实道路服务待验收，不自动刷新</small
            ><small v-if="ops.mapRouting"
              >路由版本 {{ ops.mapRouting.routing_version }} ·
              {{ ops.mapRouting.node_count }} 节点（计划状态未改变）</small
            >
          </div>
          <MapView
            :demo-map="visibleMap"
            :reference-map="validMapPlan ? currentMap : undefined"
            :data-loading="mapLoading || previewMapLoading"
            :data-error="visibleMapError"
            :local-sample="ops.mode === 'demo' && ops.demoScenario === 'wuhan'"
            @refresh="refreshMaps"
            @select-node="selectMapNode"
            :context="ops.context"
            :vehicle-id="validMapPlan?.vehicleId || mapScope"
            v-model:selected-order="selectedOrder"
            :risk-orders="riskOrders"
            :data-mode="ops.mode"
            :candidate="!!validMapPlan"
            :plan-id="validMapPlan?.id || null"
            :plan-version="validMapPlan?.version"
            :plan-status="mapPlan?.status"
            :baseline="baseStops"
            :stops="
              workbenchPlan && validMapPlan
                ? workbenchPlan.target_routes
                : baseStops
            "
            title="武汉配送地图"
          />
        </div>
        <aside class="linked-orders-panel">
          <div class="linked-orders-title">
            <h3>车辆任务</h3>
            <span>{{ mapVehicle }} / 当前快照</span>
          </div>
          <button
            v-for="o in ops.context?.orders"
            :key="o.order_no"
            class="linked-order"
            :class="{
              selected: selectedOrder === o.order_no,
              risk: riskOrders.includes(o.order_no),
            }"
            @click="selectOrder(o.order_no)"
          >
            <span class="linked-order-dot" />
            <div>
              <b>{{ nodeName(o.order_no) }}</b
              ><small>{{ o.order_no }} · {{ o.delivery_node_id }}</small>
            </div>
            <span>{{
              riskOrders.includes(o.order_no) ? "受影响" : label(o.status)
            }}</span
            ><ChevronRight :size="14" />
          </button>
          <p v-if="!ops.context?.orders.length" class="empty-inline">
            当前车辆没有授权订单。
          </p>
          <div class="linked-orders-note">
            <ShieldCheck :size="16" />
            <p>
              武汉模拟配送采用授权点位与示意虚线；无实时车辆GPS，距离和ETA仍以业务分析为准。
            </p>
          </div>
        </aside>
      </section>
      <div class="workspace-layout">
        <section class="incident-list panel">
          <div class="table-toolbar">
            <div class="filter-tabs">
              <button
                :class="{ active: filter === 'ALL' }"
                @click="filter = 'ALL'"
              >
                全部事件</button
              ><button
                :class="{ active: filter === 'ROAD_DELAY' }"
                @click="filter = 'ROAD_DELAY'"
              >
                道路延误</button
              ><button
                :class="{ active: filter === 'MANUAL' }"
                @click="filter = 'MANUAL'"
              >
                人工处理
              </button>
            </div>
            <label class="search-box"
              ><Search :size="16" /><input
                v-model="ops.search"
                placeholder="搜索事件、车辆"
                aria-label="搜索异常"
            /></label>
          </div>
          <div class="data-table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>异常事件 / 车辆</th>
                  <th>类型</th>
                  <th>处理进度</th>
                  <th>上报时间</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in filteredIncidents"
                  :key="item.incident_id"
                  :class="{ selected: item.incident_id === inc?.incident_id }"
                  @click="selectIncident(item.incident_id)"
                  tabindex="0"
                  @keydown.enter="selectIncident(item.incident_id)"
                >
                  <td>
                    <b>{{ item.incident_id }}</b
                    ><small><Truck :size="12" /> {{ item.vehicle_id }}</small>
                  </td>
                  <td>{{ label(item.event_type) }}</td>
                  <td>
                    <span
                      class="badge"
                      :class="
                        item.status === 'MANUAL_REVIEW' ? 'danger' : 'warning'
                      "
                      >{{ label(item.status) }}</span
                    >
                  </td>
                  <td class="mono">{{ time(item.reported_at) }}</td>
                  <td>
                    <button
                      class="icon-button"
                      :aria-label="'查看异常 ' + item.incident_id"
                      @click.stop="selectIncident(item.incident_id)"
                    >
                      <ChevronRight :size="17" />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="!filteredIncidents.length" class="empty-state">
            <CheckCircle2 :size="28" />
            <h3>没有匹配的异常</h3>
            <p>调整筛选，或登记新的配送异常。</p>
          </div>
          <footer class="table-footer">
            {{ filteredIncidents.length }} 个授权事件
            <span>版本与状态均来自业务服务</span>
          </footer>
        </section>
        <aside class="incident-detail panel" v-if="inc">
          <header>
            <span class="eyebrow">事件详情</span
            ><span class="mono">V{{ inc.incident_version }}</span>
          </header>
          <span class="detail-icon"><TriangleAlert :size="26" /></span>
          <h2>{{ label(inc.event_type) }}</h2>
          <p class="detail-description">{{ inc.description }}</p>
          <div class="detail-facts">
            <div>
              <span>关联车辆</span><b>{{ inc.vehicle_id }}</b>
            </div>
            <div>
              <span>预计现场延误</span
              ><b>{{ number(inc.delay_minutes) }} <small>min</small></b>
            </div>
            <div>
              <span>受影响订单</span
              ><b>{{
                ops.analysis
                  ? ops.analysis.risk_order_count + " 单"
                  : "尚未分析"
              }}</b>
            </div>
            <div>
              <span>下一步</span><b>{{ label(inc.next_action) }}</b>
            </div>
          </div>
          <ProcessLedger
            :now="ops.realTime"
            :incident="inc"
            :analysis="ops.analysis"
            :plans="ops.workspace?.plans || []"
            :job="ops.job"
            :job-associated="
              lifecycleJob?.incidentId === inc.incident_id &&
              lifecycleJob?.jobId === ops.job?.job_id
            "
            :generation="ops.generation"
          />
          <div
            v-if="workbenchPlan && validMapPlan"
            class="map-candidate-summary"
          >
            <b>所选恢复候选</b
            ><span
              >预计延误
              {{ workbenchPlan.metrics.total_delay_minutes }} 分钟</span
            ><span
              >调整 {{ workbenchPlan.metrics.changed_order_count }} 单 ·
              {{ label(workbenchPlan.status) }}</span
            ><RouterLink
              to="/plans"
              @click="ops.selectedPlanId = workbenchPlan.plan_id"
              >比较并审批方案</RouterLink
            >
          </div>
          <div class="detail-actions">
            <button
              class="primary full"
              :disabled="ops.busy || !inc.can_analyze"
              @click="runLifecycle('analyze')"
            >
              <MessageSquareText :size="16" /> 执行影响分析
              <ArrowRight :size="16" /></button
            ><button
              class="secondary full"
              :disabled="ops.busy || !ops.analysis"
              @click="runLifecycle('generate')"
            >
              生成恢复候选 <GitCompareArrows :size="17" /></button
            ><RouterLink
              v-if="ops.workspace?.plans.length"
              to="/plans"
              class="text-link"
              >比较 {{ ops.workspace.plans.length }} 个候选
              <ArrowUpRight :size="16" /></RouterLink
            ><button
              v-if="
                inc.event_type === 'RECEIVING_WINDOW_CHANGE' && inc.order_no
              "
              class="secondary full"
              @click="dialog = 'verify'"
            >
              核实收货时间窗</button
            ><button class="quiet full" @click="dialog = 'handoff'">
              转人工协调
            </button>
          </div>
        </aside>
        <aside v-else class="panel empty-state">
          <Package :size="32" />
          <h3>选择一个异常</h3>
          <p>事件详情、影响和下一步将在此展示。</p>
        </aside>
      </div>
    </div>
    <section v-if="ops.analysis" class="impact-panel panel">
      <header class="section-header">
        <div>
          <h2>订单影响分析</h2>
        </div>
        <span class="badge success">当前有效分析</span>
      </header>
      <RiskRibbon
        :analysis="ops.analysis"
        :selected-order="selectedOrder"
        @select="selectOrder"
      />
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>订单</th>
              <th>原预计到达</th>
              <th>异常后预计到达</th>
              <th>收货截止</th>
              <th>预计延误</th>
              <th>约束风险</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="o in ops.analysis.order_impacts"
              :key="o.order_no"
              :class="{ 'order-linked-selected': selectedOrder === o.order_no }"
              tabindex="0"
              @click="selectOrder(o.order_no)"
              @keydown.enter="selectOrder(o.order_no)"
            >
              <td>
                <b>{{ o.order_no }}</b
                ><small>{{ nodeName(o.order_no) }}</small>
              </td>
              <td class="mono">{{ time(o.original_eta) }}</td>
              <td class="mono">{{ time(o.updated_eta) }}</td>
              <td class="mono">{{ time(o.latest_arrival) }}</td>
              <td class="mono">{{ o.delay_minutes }} min</td>
              <td>
                <span
                  class="badge"
                  :class="o.risk_type === 'NONE' ? 'success' : 'danger'"
                  >{{
                    o.risk_type === "NONE"
                      ? "无风险"
                      : o.risk_type === "SOFT_WINDOW_LATE"
                        ? "软时间窗延误"
                        : "硬约束冲突"
                  }}</span
                >
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
  <main v-else-if="route.path === '/plans'" class="business-main plans-main">
    <div class="page-heading">
      <div>
        <h1>恢复方案比较</h1>
        <p>对比任务顺序、延误指标及约束结果。</p>
      </div>
      <RouterLink to="/incidents" class="secondary"
        >返回异常工作台 <ArrowUpRight :size="17"
      /></RouterLink>
    </div>
    <div v-if="ops.incidents.length" class="plans-context">
      <label
        >当前事件<select
          :value="inc?.incident_id"
          @change="selectIncident(($event.target as HTMLSelectElement).value)"
        >
          <option
            v-for="i in ops.incidents"
            :key="i.incident_id"
            :value="i.incident_id"
          >
            {{ i.incident_id }} · {{ label(i.event_type) }} · {{ i.vehicle_id }}
          </option>
        </select></label
      ><span v-if="inc">{{ inc.description }}</span>
    </div>
    <template v-if="p"
      ><div class="candidate-tabs">
        <button
          v-for="(candidate, index) in ops.workspace?.plans"
          :key="candidate.plan_id"
          :class="{ active: p.plan_id === candidate.plan_id }"
          @click="ops.selectedPlanId = candidate.plan_id"
        >
          <span class="mono">{{ String(index + 1).padStart(2, "0") }}</span>
          恢复候选
          <span class="badge" :class="ops.expired(candidate) ? 'danger' : ''">{{
            label(candidate.status)
          }}</span>
        </button>
      </div>
      <div class="plan-status-bar">
        <span
          ><span class="status-dot" /> {{ label(p.status) }}
          <span class="mono"
            >/ {{ p.plan_id }} · V{{ p.plan_version }}</span
          ></span
        ><span
          v-if="p.status === 'PENDING_APPROVAL'"
          :class="{ dangerText: ops.expired(p) }"
          ><Clock3 :size="14" />
          {{
            ops.expired(p)
              ? "已过期，请重新分析"
              : `候选有效期剩余 ${countdown}s`
          }}</span
        >
      </div>
      <div
        v-if="
          p.status === 'APPROVED_PENDING_ACK' &&
          ops.reservationExpiry[p.plan_id]
        "
        class="ack-deadline"
      >
        <Clock3 :size="15" />司机接收预留截止
        {{ time(ops.reservationExpiry[p.plan_id]) }} · 剩余
        {{
          Math.max(
            0,
            Math.ceil(
              (Date.parse(ops.reservationExpiry[p.plan_id]!) - ops.realTime) /
                1000,
            ),
          )
        }}
        秒。到期后由后端确认最新状态。
      </div>
      <div v-if="ops.expired(p)" class="stale-callout">
        <TriangleAlert :size="21" />
        <div>
          <b>当前方案已失效，不能继续审批。</b>
          <p>
            快照过期、依赖变化或方案已被替代。请刷新上下文，重新分析并生成候选。
          </p>
        </div>
        <RouterLink to="/incidents" class="secondary">重新分析</RouterLink>
      </div>
      <div class="comparison-layout">
        <section class="plan-map-column">
          <div class="plan-spatial-heading">
            <div>
              <h2>最小扰动恢复</h2>
            </div>
            <div class="route-view-switch">
              <button
                :class="{ active: planMapMode === 'baseline' }"
                @click="planMapMode = 'baseline'"
              >
                {{ p.status === "ACTIVE" ? "当前快照" : "原配送计划" }}</button
              ><button
                :class="{ active: planMapMode === 'candidate' }"
                @click="planMapMode = 'candidate'"
              >
                所选恢复方案
              </button>
            </div>
          </div>
          <div class="recovery-flow">
            <span><i>01</i>原计划</span><ArrowRight :size="13" /><span
              class="risk"
              ><i>02</i>异常影响</span
            ><ArrowRight :size="13" /><span
              :class="{ highlight: planMapMode === 'candidate' }"
              ><i>03</i>恢复候选</span
            ><ArrowRight :size="13" /><span><i>04</i>变更与约束</span>
          </div>
          <MapView
            :demo-map="visibleMap"
            :reference-map="validMapPlan ? currentMap : undefined"
            :data-loading="mapLoading || previewMapLoading"
            :data-error="visibleMapError"
            :local-sample="ops.mode === 'demo' && ops.demoScenario === 'wuhan'"
            @refresh="refreshMaps"
            @select-node="selectMapNode"
            :key="'plan-network'"
            :context="ops.context"
            :stops="mapStops"
            :baseline="baseStops"
            :vehicle-id="inc?.vehicle_id || mapVehicle"
            v-model:selected-order="selectedOrder"
            :risk-orders="riskOrders"
            :candidate="!!validMapPlan"
            :plan-id="validMapPlan?.id || null"
            :plan-version="validMapPlan?.version || null"
            :plan-status="ops.expired(p) ? 'STALE' : p.status"
            :changes="planMapMode === 'candidate' ? p.changes : []"
            :data-mode="ops.mode"
            :title="
              planMapMode === 'baseline'
                ? p.status === 'ACTIVE'
                  ? '当前生效快照'
                  : '原计划地图'
                : '恢复方案地图'
            "
          />
          <div class="plan-preservation">
            <ShieldCheck :size="22" />
            <div>
              <b>{{ preserved }} 个站点保留原安排</b
              ><small>{{
                p.status === "ACTIVE"
                  ? "路线已生效；当前契约没有独立历史原路线。"
                  : "当前候选仅为预览，审批与司机确认后仍由后端决定是否生效。"
              }}</small>
            </div>
          </div>
          <details class="sequence-details">
            <summary>
              逐站对照 · 原计划 / 所选方案 <ChevronDown :size="15" />
            </summary>
            <div class="route-sequence-pair">
              <div>
                <h3>{{ p.status === "ACTIVE" ? "当前快照" : "原配送计划" }}</h3>
                <RouteTimeline
                  :stops="baseStops"
                  :interactive="true"
                  :selected-order="selectedOrder"
                  @select="selectStop"
                />
              </div>
              <div>
                <h3>所选恢复方案</h3>
                <RouteTimeline
                  :stops="targetStops"
                  :changes="p.changes"
                  :interactive="true"
                  :selected-order="selectedOrder"
                  @select="selectStop"
                />
              </div>
            </div>
          </details>
        </section>
        <aside class="metrics-column">
          <PlanInstrument
            :plan="p"
            :analysis="ops.analysis"
            :vehicle-id="inc?.vehicle_id || mapVehicle"
          />
          <h2>影响与约束</h2>
          <div class="decision-metrics">
            <div
              v-for="[title, value, unit] in metricRows"
              :key="String(title)"
            >
              <span>{{ title }}</span
              ><b
                >{{ value }}<small>{{ unit }}</small></b
              >
            </div>
          </div>
          <div
            class="constraint-check"
            :class="{ failed: !p.constraints.passed }"
          >
            <ShieldCheck :size="20" />
            <div>
              <b>{{ p.constraints.passed ? "约束检查通过" : "存在约束冲突" }}</b
              ><small>{{
                p.constraints.passed
                  ? "后端已验证当前候选的业务约束"
                  : "请查看约束详情并重新处理"
              }}</small>
            </div>
          </div>
          <p
            v-for="(v, i) in p.constraints.violations"
            :key="i"
            class="dangerText"
          >
            {{ v }}
          </p>
          <div class="approval-brief">
            <span>涉及司机</span
            ><b>{{ p.required_driver_ids.join(" / ") || "无" }}</b>
            <p>批准后仍需司机接收。仅后端确认 ACTIVE 时，计划才正式生效。</p>
          </div>
          <button
            class="primary full"
            :disabled="
              ops.busy ||
              p.status !== 'PENDING_APPROVAL' ||
              ops.expired(p) ||
              !p.constraints.passed
            "
            @click="dialog = 'approve'"
          >
            批准此方案 <ArrowRight :size="17" /></button
          ><button
            class="quiet full"
            :disabled="
              ops.busy || p.status !== 'PENDING_APPROVAL' || ops.expired(p)
            "
            @click="dialog = 'reject'"
          >
            拒绝并说明原因</button
          ><small class="metrics-source">{{
            ops.mode === "demo"
              ? "指标来自独立样例，不代表实际优化效果。"
              : "指标来自后端计算，不在前端推算优化结果。"
          }}</small>
        </aside>
      </div>
      <section class="plan-impact-table panel">
        <header class="section-header">
          <div>
            <h2>时间变化与任务关联</h2>
          </div>
          <span>点击任务，与路线节点双向联动</span>
        </header>
        <p class="eta-boundary">
          以下 ETA
          来自异常影响分析。当前候选接口没有逐站预计到达时间，不将异常后 ETA
          当作恢复后 ETA。
        </p>
        <div class="data-table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>任务 / 配送节点</th>
                <th>原预计到达</th>
                <th>异常后预计到达</th>
                <th>异常后延误</th>
                <th>候选调整</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="o in ops.analysis?.order_impacts || []"
                :key="o.order_no"
                tabindex="0"
                :class="{
                  'order-linked-selected': selectedOrder === o.order_no,
                }"
                @click="selectOrder(o.order_no)"
                @keydown.enter="selectOrder(o.order_no)"
              >
                <td>
                  <button
                    class="order-select-button"
                    @click.stop="selectOrder(o.order_no)"
                  >
                    {{ o.order_no }}<small>{{ nodeName(o.order_no) }}</small>
                  </button>
                </td>
                <td class="mono">{{ time(o.original_eta) }}</td>
                <td class="mono">{{ time(o.updated_eta) }}</td>
                <td class="mono">{{ o.delay_minutes }} min</td>
                <td>
                  <span
                    class="badge"
                    :class="
                      p.changes.some((c) => c.order_no === o.order_no)
                        ? 'changed-badge'
                        : ''
                    "
                    >{{
                      p.changes.find((c) => c.order_no === o.order_no)
                        ? "顺序 " +
                          p.changes.find((c) => c.order_no === o.order_no)
                            ?.old_sequence_no +
                          " → " +
                          p.changes.find((c) => c.order_no === o.order_no)
                            ?.new_sequence_no
                        : "保留原安排"
                    }}</span
                  >
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!ops.analysis" class="empty-inline">
          尚无当前版本的有效影响分析，逐站 ETA 待服务端提供。
        </p>
      </section>
      <section class="changes-section panel">
        <header class="section-header">
          <div>
            <h2>变更清单</h2>
          </div>
          <span>{{ p.changes.length }} 处明确调整</span>
        </header>
        <div
          class="change-row"
          v-for="c in p.changes"
          :key="c.order_no"
          :class="{ 'order-linked-selected': selectedOrder === c.order_no }"
          tabindex="0"
          @click="selectOrder(c.order_no)"
          @keydown.enter="selectOrder(c.order_no)"
        >
          <span class="change-number mono">{{ c.order_no }}</span>
          <div>
            <b>{{ nodeName(c.order_no) }}</b
            ><small
              >{{ label(c.change_type) }} · {{ c.from_vehicle_id }} →
              {{ c.to_vehicle_id }}</small
            >
          </div>
          <div class="sequence-change">
            停靠 {{ c.old_sequence_no }} <ArrowRight :size="16" />
            <b>{{ c.new_sequence_no }}</b>
          </div>
        </div>
        <p v-if="!p.changes.length" class="empty-inline">
          此候选未调整订单安排。
        </p>
      </section></template
    >
    <section v-else class="plans-empty panel">
      <div
        v-if="ops.generation?.solution_status === 'NO_FEASIBLE_PLAN_IN_SEARCH'"
        class="no-solution"
      >
        <b>本次搜索范围内没有可行方案</b>
        <p>这不表示全局无解。请核实约束、补充事实或转人工协调。</p>
        <pre>{{ ops.generation.infeasible_reasons }}</pre>
      </div>
      <div class="empty-graphic"><GitCompareArrows :size="50" /></div>
      <h2>当前事件尚无恢复方案</h2>
      <p>当前事件还没有恢复候选。完成影响分析后，生成可比较的方案。</p>
      <div class="button-row">
        <button
          class="secondary"
          :disabled="ops.busy || !inc?.can_analyze"
          @click="ops.analyze"
        >
          执行影响分析</button
        ><button
          class="primary"
          :disabled="ops.busy || !ops.analysis"
          @click="ops.generate"
        >
          生成恢复候选 <ArrowRight :size="17" />
        </button>
      </div>
      <p v-if="ops.analysis" class="subtle">
        分析已完成。如生成结果无可行方案，请转人工协调。
      </p>
    </section>
  </main>
  <main v-else-if="route.path === '/driver'" class="driver-main">
    <div class="driver-greeting">
      <div class="driver-heading-copy">
        <h1>今日配送</h1>
        <p>
          {{ currentVehicle?.vehicle_no || ops.user?.vehicle_id
          }}<span>·</span>本人授权任务与执行
        </p>
      </div>
      <nav class="driver-desktop-nav" aria-label="司机工作导航">
        <button
          :class="{ active: driverTab === 'today' }"
          @click="scrollDriver('today')"
        >
          <Truck :size="15" />今日配送</button
        ><button
          :class="{ active: driverTab === 'route' }"
          @click="scrollDriver('route')"
        >
          <Route :size="15" />我的路线</button
        ><button
          :class="{ active: driverTab === 'feedback' }"
          @click="scrollDriver('feedback')"
        >
          执行反馈</button
        ><button @click="beginReport"><Plus :size="15" />上报异常</button>
      </nav>
    </div>
    <div class="driver-layout">
      <div class="driver-information" id="driver-today">
        <section class="next-stop-card">
          <div class="next-stop-top">
            <span
              ><span class="status-dot" />
              {{
                nextStop
                  ? "配送进行中"
                  : ownStops.length
                    ? "当前任务已结束"
                    : "当前暂无配送任务"
              }}</span
            ><span class="mono"
              >REV {{ currentVehicle?.route_revision ?? "—" }}</span
            >
          </div>
          <span class="eyebrow">下一站</span>
          <h2>{{ nodeName(nextStop?.order_no) }}</h2>
          <div class="next-stop-meta">
            <div>
              <small>收货截止时间</small
              ><b>{{
                time(
                  ops.context?.orders.find(
                    (o) => o.order_no === nextStop?.order_no,
                  )?.latest_arrival,
                )
              }}</b>
            </div>
            <div>
              <small>剩余订单</small
              ><b
                >{{
                  ownStops.filter(
                    (s) => s.status !== "COMPLETED" && s.status !== "CANCELLED",
                  ).length
                }}
                <em>单</em></b
              >
            </div>
          </div>
          <p class="driver-eta-note">
            {{
              nextEta
                ? "异常分析预计到达 " + time(nextEta)
                : "预计到达时间待业务服务确认"
            }}
          </p>
          <button class="lemon-button full" @click="beginReport">
            <TriangleAlert :size="17" /> 上报配送异常
            <ArrowUpRight :size="18" />
          </button>
        </section>
        <div class="safety-note">
          <ShieldCheck :size="16" />
          请在安全停车后查看任务、上报异常或确认方案。
        </div>
        <section
          v-if="
            ops.workspace?.plans.some(
              (p) => p.status === 'APPROVED_PENDING_ACK',
            )
          "
          class="driver-adjustments panel"
        >
          <h2>有新的任务调整</h2>
          <div
            v-for="plan in ops.workspace.plans.filter(
              (p) => p.status === 'APPROVED_PENDING_ACK',
            )"
            :key="plan.plan_id"
          >
            <span class="badge warning">{{ plan.approval_mode === 'AUTOMATIC_RULE' ? '规则自动审核通过' : '已获调度批准' }} · 等待您的确认</span>
            <div
              v-for="c in plan.changes.filter(
                (c) =>
                  c.from_vehicle_id === ops.user?.vehicle_id ||
                  c.to_vehicle_id === ops.user?.vehicle_id,
              )"
              :key="c.order_no"
              class="driver-change"
            >
              <b>{{ nodeName(c.order_no) }}</b
              ><span
                >停靠 {{ c.old_sequence_no }} → {{ c.new_sequence_no }}</span
              >
            </div>
            <p>
              仅接受本人的任务调整；全部必要回执完成后，由后端确认计划生效。
            </p>
            <div class="button-row">
              <button
                class="secondary"
                @click="
                  ops.selectedPlanId = plan.plan_id;
                  dialog = 'decline';
                "
              >
                拒绝</button
              ><button
                class="primary"
                @click="
                  ops.selectedPlanId = plan.plan_id;
                  dialog = 'accept';
                "
              >
                接受调整 <Check :size="17" />
              </button>
            </div>
          </div>
        </section>
        <section class="driver-timeline panel">
          <DeliveryProgress :stops="ownStops" />
          <header class="section-header">
            <h2>今日配送路线</h2>
            <span>{{ ownStops.length }} 站</span>
          </header>
          <RouteTimeline
            :stops="ownStops"
            :interactive="true"
            @select="selectStop"
            :selected-order="selectedOrder"
          />
          <div v-if="selectedStop" class="stop-execution">
            <b>{{
              nodeName(
                ownStops.find((s) => s.stop_id === selectedStop)?.order_no,
              )
            }}</b>
            <p>
              当前状态：{{
                label(ownStops.find((s) => s.stop_id === selectedStop)?.status)
              }}
            </p>
            <div class="button-row">
              <button
                class="secondary"
                :disabled="
                  ops.busy ||
                  ownStops.find((s) => s.stop_id === selectedStop)?.status !==
                    'PENDING'
                "
                @click="ops.complete(selectedStop, 'ARRIVED')"
              >
                确认到达</button
              ><button
                class="primary"
                :disabled="
                  ops.busy ||
                  ownStops.find((s) => s.stop_id === selectedStop)?.status !==
                    'ARRIVED'
                "
                @click="ops.complete(selectedStop, 'COMPLETED')"
              >
                确认配送完成
              </button>
            </div>
          </div>
        </section>
      </div>
      <aside class="driver-map-column" id="driver-map">
        <div class="driver-map-intro">
          <div>
            <h2>路线与位置</h2>
          </div>
          <span class="badge">本人授权任务</span>
          <label v-if="ops.workspace?.plans.length"
            >地图方案<select
              aria-label="司机地图方案"
              v-model="workbenchPlanId"
            >
              <option value="">当前路线</option>
              <option
                v-for="plan in ops.workspace.plans"
                :key="plan.plan_id"
                :value="plan.plan_id"
                :disabled="
                  ops.expired(plan) ||
                  !['PENDING_APPROVAL', 'APPROVED_PENDING_ACK'].includes(
                    plan.status,
                  )
                "
              >
                {{ plan.plan_id }} · {{ label(plan.status) }}
              </option>
            </select></label
          >
        </div>
        <MapView
          :demo-map="visibleMap"
          :reference-map="validMapPlan ? currentMap : undefined"
          :data-loading="mapLoading || previewMapLoading"
          :data-error="visibleMapError"
          :local-sample="ops.mode === 'demo' && ops.demoScenario === 'wuhan'"
          @refresh="refreshMaps"
          @select-node="selectMapNode"
          :context="ops.context"
          :stops="
            validMapPlan && workbenchPlan
              ? workbenchPlan.target_routes
              : ownStops
          "
          :baseline="ownStops"
          :candidate="!!validMapPlan"
          :plan-id="validMapPlan?.id || null"
          :plan-version="validMapPlan?.version"
          :plan-status="mapPlan?.status"
          :vehicle-id="ops.user?.vehicle_id || ''"
          :selected-order="selectedOrder"
          @update:selected-order="selectOrder"
          :risk-orders="riskOrders"
          :next-order="nextStop?.order_no"
          :data-mode="ops.mode"
          title="我的配送路线"
        />
        <div class="driver-route-summary">
          <div>
            <Route :size="18" /><span
              >当前有效路线<b class="mono"
                >REV {{ currentVehicle?.route_revision ?? "—" }}</b
              ></span
            >
          </div>
          <div>
            <MapPin :size="18" /><span
              >最近确认位置<b>{{
                currentVehicle?.location_node_id || "未提供车辆位置"
              }}</b
              ><small>{{
                currentVehicle?.location_node_id
                  ? time(currentVehicle.location_time) + " · 非实时定位"
                  : "无GPS坐标，不绘制车辆Marker"
              }}</small></span
            >
          </div>
        </div>
        <section v-if="confirmedAdjustments" class="driver-confirmed-route">
          <CheckCircle2 :size="20" />
          <div>
            <b>已生效的路线调整</b>
            <p>
              {{ confirmedAdjustments.plan_id }} / V{{
                confirmedAdjustments.plan_version
              }}
              · 本人任务按当前配送快照执行
            </p>
          </div>
        </section>
        <div class="map-availability-note">
          <p>
            武汉模拟点位与虚线不代表真实地址、行驶道路或车辆GPS；地图不可用不影响任务执行。
          </p>
        </div>
        <section class="driver-feedback panel" id="driver-feedback">
          <header class="section-header">
            <h2>我的异常与反馈</h2>
            <button
              class="icon-button"
              aria-label="刷新司机异常"
              @click="ops.guard(ops.refresh)"
            >
              <RefreshCw :size="16" />
            </button>
          </header>
          <button
            v-for="i in ops.incidents"
            :key="i.incident_id"
            class="driver-incident"
            @click="selectIncident(i.incident_id)"
          >
            <div>
              <b>{{ label(i.event_type) }}</b
              ><small>{{ i.description }}</small>
            </div>
            <span class="badge warning">{{ label(i.status) }}</span>
          </button>
          <p v-if="!ops.incidents.length" class="empty-inline">
            当前没有已上报异常。
          </p>
          <div v-if="inc" class="next-action-note">
            下一步：{{ label(inc.next_action) }}
          </div>
        </section>
      </aside>
    </div>

    <nav class="driver-bottom-nav">
      <a href="#/driver"><Truck :size="20" />今日配送</a
      ><button @click="beginReport"><Plus :size="22" />异常上报</button
      ><button @click="ops.assistantOpen = true">
        <MessageSquareText :size="20" />智能协作
      </button>
    </nav>
  </main>
  <div
    v-if="showReport || dialog"
    class="modal-backdrop"
    @click.self="
      showReport = false;
      dialog = null;
    "
  >
    <section
      class="modal"
      role="dialog"
      aria-modal="true"
      :aria-label="showReport ? '上报配送异常' : '确认业务操作'"
    >
      <button
        class="modal-close icon-button"
        aria-label="关闭对话框"
        @click="
          showReport = false;
          dialog = null;
        "
      >
        <X :size="20" />
      </button>
      <p v-if="ops.error" class="modal-error">{{ ops.error }}</p>
      <template v-if="showReport">
        <h2>上报配送异常</h2>
        <p>请填写现场事实。系统会根据真实数据分析影响。</p>
        <form class="report-form" @submit.prevent="submitReport">
          <label
            >关联车辆<select
              v-model="vehicleId"
              required
              :disabled="ops.isDriver"
            >
              <option
                v-for="v in ops.context?.vehicles"
                :key="v.vehicle_id"
                :value="v.vehicle_id"
              >
                {{ v.vehicle_no }} / {{ v.vehicle_id }}
              </option>
            </select></label
          ><label
            >异常类型<select v-model="eventType">
              <option value="ROAD_DELAY">道路延误</option>
              <option value="RECEIVING_WINDOW_CHANGE">收货时间变化</option>
              <option value="VEHICLE_BREAKDOWN">车辆故障</option>
            </select></label
          ><label
            >关联订单<select
              v-model="orderNo"
              :required="eventType === 'RECEIVING_WINDOW_CHANGE'"
            >
              <option value="">未指定订单</option>
              <option
                v-for="o in ops.context?.orders.filter(
                  (o) => o.assigned_vehicle_id === vehicleId,
                )"
                :key="o.order_no"
                :value="o.order_no"
              >
                {{ o.order_no }} · {{ nodeName(o.order_no) }}
              </option>
            </select></label
          ><label v-if="eventType === 'ROAD_DELAY'"
            >预计延误（分钟）<input
              v-model.number="delay"
              type="number"
              min="0"
              max="1440"
              required
              placeholder="请输入整数，0 也是有效值"
          /></label>
          <div v-if="eventType === 'RECEIVING_WINDOW_CHANGE'" class="form-row">
            <label
              >新收货开始<input
                v-model="windowStart"
                type="datetime-local"
                required /></label
            ><label
              >新收货结束<input
                v-model="windowEnd"
                type="datetime-local"
                :min="windowStart"
                required
            /></label>
          </div>
          <label
            >现场描述<textarea
              v-model="description"
              required
              maxlength="1000"
              placeholder="例如：前面堵车了，估计晚 20 分钟。"
            /></label
          ><small
            >时间使用{{
              ops.context?.clock_mode === "SIMULATION"
                ? "后端仿真业务时钟"
                : "当前时间"
            }}。上报不等于处置完成。</small
          ><button class="primary full" :disabled="ops.busy">
            提交异常 <Send :size="16" /></button
          ><button
            class="quiet full"
            type="button"
            @click="
              showReport = false;
              ops.assistantOpen = true;
            "
          >
            改用自然语言与助手沟通 <MessageSquareText :size="16" />
          </button></form></template
      ><template v-else>
        <h2>
          {{
            dialog === "approve"
              ? "批准此恢复方案？"
              : dialog === "accept"
                ? "接受本次任务调整？"
                : dialog === "verify"
                  ? "核实收货时间窗"
                  : dialog === "handoff"
                    ? "转交人工协调？"
                    : "请说明拒绝原因"
          }}
        </h2>
        <div v-if="p && dialog !== 'handoff'" class="confirmation-summary">
          <b>{{ p.plan_id }} / V{{ p.plan_version }}</b>
          <p>
            {{ p.metrics.changed_order_count }} 个变更订单 ·
            {{ p.metrics.total_delay_minutes }} min 预计总延误
          </p>
          <small>涉及司机 {{ p.required_driver_ids.join("、") }}</small>
        </div>
        <p v-if="dialog === 'approve'">
          批准后进入等待司机确认状态。司机接收与计划生效将分别由后端确认。
        </p>
        <p v-if="dialog === 'accept'">
          请在安全停车后核对变更。接受方案不会自动完成配送任务。
        </p>
        <form @submit.prevent="confirm">
          <template v-if="dialog === 'verify'"
            ><p class="subtle">
              请先与收货方核实订单 {{ inc?.order_no }} 的新时间窗。
            </p>
            <label
              >核实后开始时间<input
                v-model="windowStart"
                type="datetime-local"
                required /></label
            ><label
              >核实后结束时间<input
                v-model="windowEnd"
                type="datetime-local"
                :min="windowStart"
                required /></label></template
          ><label v-if="['reject', 'decline', 'handoff'].includes(dialog!)"
            >{{ dialog === "handoff" ? "协调原因" : "拒绝原因"
            }}<textarea
              v-model="reason"
              required
              maxlength="1000"
              placeholder="请说明现场原因与需要的协助"
            /></label
          ><button class="primary full" :disabled="ops.busy">
            确认{{
              dialog === "approve"
                ? "批准"
                : dialog === "accept"
                  ? "接受"
                  : dialog === "verify"
                    ? "核实"
                    : dialog === "handoff"
                      ? "转人工"
                      : "拒绝"
            }}
            <ArrowRight :size="17" />
          </button></form
      ></template>
    </section>
  </div>
</template>
