import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";
import { request, setToken, ApiError, id } from "../api/client";
import {
  isRunResult,
  abortableDelay,
  chatBusinessLabel,
  continuationLabel,
  continuationMessage,
} from "../lib/chat-state";
import { mockRequest, configureDemoScenario } from "../mock/transport";
import { isMapRoutingResult } from "../lib/map-result";
import type {
  Context,
  Incident,
  Plan,
  WorkspaceData,
  User,
  LoginResult,
  Job,
  Schema,
} from "../api/types";
type TaskRef = {
  jobId: string;
  scope: "chat" | "business";
  conversationId?: string;
  orderNo?: string;
  incidentId?: string;
};
type Pending = {
  userId: string;
  key: string;
  path: string;
  body: unknown;
  chatContext?: {
    conversationId?: string;
    orderNo: string;
    incidentId: string;
  };
};
export const useOps = defineStore("ops", () => {
  const user = ref<User | null>(null),
    mode = ref<"api" | "demo">("api"),
    demoScenario = ref<"legacy" | "wuhan">("legacy"),
    context = ref<Context | null>(null),
    incidents = ref<Incident[]>([]),
    workspace = ref<WorkspaceData | null>(null);
  const selectedPlanId = ref(""),
    busy = ref(false),
    error = ref(""),
    notice = ref(""),
    assistantOpen = ref(false),
    job = ref<Job | null>(null),
    conversationId = ref<string>(),
    messages = ref<
      {
        role: string;
        text: string;
        turnId?: string;
        createdAt?: string;
        jobId?: string;
      }[]
    >([]);
  const chatHistoryIncomplete = ref(false),
    mapRouting = ref<Schema["MapRoutingOutput"] | null>(null);
  const pending = ref<Pending | null>(null),
    pendingJob = ref(""),
    recoveryResult = ref(""),
    search = ref(""),
    generation = ref<Schema["GenerationOutput"] | null>(null);
  const fleet = ref<
    {
      vehicle_id: string;
      vehicle_no: string;
      driver_id: string | null;
      status: string;
    }[]
  >([]);
  const selectedVehicleId = ref("");
  const reservationExpiry = ref<Record<string, string>>({});
  const chatGeneration = ref<Schema["GenerationOutput"] | null>(null),
    chatJob = ref<Job | null>(null);
  const chatOrderNo = ref(""),
    chatIncidentId = ref(""),
    chatResult = ref<Schema["RunResult"] | null>(null),
    pendingTasks = ref<TaskRef[]>([]),
    polling = ref(false);
  const runtime = ref<{
    reachable: boolean;
    agentMode: string;
    checked: boolean;
  }>({ reachable: false, agentMode: "unknown", checked: false });
  const chatStateLabel = computed(() => chatBusinessLabel(chatResult.value));
  const chatHasPending = computed(() =>
    pendingTasks.value.some(
      (t) => t.scope === "chat" && t.conversationId === conversationId.value,
    ),
  );
  const chatContinueLabel = computed(() =>
    continuationLabel(chatResult.value, chatHasPending.value),
  );
  let activePoll: AbortController | null = null,
    activeScope: "chat" | "business" = "business",
    actionSerial = 0;
  let queryRevision = 0;
  const seenResults = new Set<string>();
  let epoch = 0;
  const selectedPlan = computed(
    () =>
      workspace.value?.plans.find((p) => p.plan_id === selectedPlanId.value) ||
      null,
  );
  const analysis = computed(() =>
    workspace.value?.analyses
      .filter(
        (a) =>
          a.analysis_status === "VALID" &&
          a.incident_version === workspace.value?.incident.incident_version,
      )
      .at(-1),
  );
  const isDriver = computed(() => user.value?.role === "DRIVER");
  const now = ref(Date.now());
  setInterval(() => (now.value = Date.now()), 1000);
  const businessTime = computed(() =>
    context.value?.clock_mode === "SIMULATION"
      ? Date.parse(context.value.clock_time)
      : now.value,
  );
  const expired = (p: Plan) =>
    p.status === "STALE" ||
    p.status === "SUPERSEDED" ||
    (p.status === "PENDING_APPROVAL" && Date.parse(p.expires_at) <= now.value);
  function persist() {
    if (mode.value === "api" && user.value)
      sessionStorage.setItem(
        "yc-recovery",
        JSON.stringify({
          userId: user.value.user_id,
          pending: pending.value,
          jobId: pendingJob.value,
          tasks: pendingTasks.value,
          conversationId: conversationId.value,
          chatOrderNo: chatOrderNo.value,
          chatIncidentId: chatIncidentId.value,
        }),
      );
  }
  function call<T>(
    path: string,
    body?: unknown,
    key?: string,
    signal?: AbortSignal,
  ) {
    return mode.value === "demo"
      ? mockRequest<T>(path, body)
      : request<T>(path, body, key, signal);
  }
  function cancelPolling(
    reason = "已停止本页查询，后台任务仍保留，可恢复查询。",
  ) {
    queryRevision++;
    if (!activePoll) return;
    activePoll.abort();
    activePoll = null;
    polling.value = false;
    actionSerial++;
    busy.value = false;
    notice.value = reason;
  }
  function setChatContext(orderNo = "", incidentId = "") {
    if (orderNo === chatOrderNo.value && incidentId === chatIncidentId.value)
      return;
    if (activeScope === "chat") cancelPolling();
    chatHistoryIncomplete.value = false;
    conversationId.value = undefined;
    chatResult.value = null;
    chatGeneration.value = null;
    chatJob.value = null;
    messages.value = [];
    seenResults.clear();
    chatOrderNo.value = orderNo;
    chatIncidentId.value = incidentId;
    persist();
  }
  function newConversation() {
    cancelPolling();
    chatHistoryIncomplete.value = false;
    chatJob.value = null;
    chatGeneration.value = null;
    conversationId.value = undefined;
    chatResult.value = null;
    messages.value = [];
    seenResults.clear();
    persist();
  }
  async function probeRuntime() {
    if (mode.value === "demo") return;
    try {
      const r = await request<{ agent_mode?: string }>("/ready");
      runtime.value = {
        reachable: true,
        agentMode: r.agent_mode || "unknown",
        checked: true,
      };
    } catch {
      runtime.value = { reachable: false, agentMode: "unknown", checked: true };
    }
  }
  function reset() {
    cancelPolling("");
    epoch++;
    pendingTasks.value = [];
    chatHistoryIncomplete.value = false;
    mapRouting.value = null;
    demoScenario.value = "legacy";
    chatResult.value = null;
    chatOrderNo.value = "";
    chatIncidentId.value = "";
    seenResults.clear();
    runtime.value = { reachable: false, agentMode: "unknown", checked: false };
    setToken("");
    user.value = null;
    fleet.value = [];
    selectedVehicleId.value = "";
    reservationExpiry.value = {};
    context.value = null;
    incidents.value = [];
    workspace.value = null;
    selectedPlanId.value = "";
    job.value = null;
    pendingJob.value = "";
    messages.value = [];
    conversationId.value = undefined;
    pending.value = null;
    busy.value = false;
    error.value = "";
    notice.value = "";
    recoveryResult.value = "";
    generation.value = null;
  }
  function logout() {
    reset();
  }
  async function guard(work: () => Promise<void>) {
    if (busy.value) return;
    const action = ++actionSerial;
    busy.value = true;
    error.value = "";
    notice.value = "";
    try {
      await work();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      if (action !== actionSerial) return;
      error.value =
        e instanceof ApiError
          ? `${e.message}${e.code ? " · " + e.code : ""}${e.missingFields.length ? " · 缺少 " + e.missingFields.join("、") : ""}${e.requestId ? " · 请求 " + e.requestId : ""}`
          : e instanceof Error
            ? e.message
            : "操作失败";
    } finally {
      if (action === actionSerial) busy.value = false;
    }
  }
  async function login(username: string, password: string) {
    await guard(async () => {
      const r = await request<LoginResult>("/auth/login", {
        username,
        password,
      });
      reset();
      busy.value = true;
      mode.value = "api";
      setToken(r.access_token);
      user.value = r.user;
      const raw = sessionStorage.getItem("yc-recovery");
      if (raw) {
        try {
          const saved = JSON.parse(raw);
          if (saved.userId === r.user.user_id) {
            pending.value = saved.pending;
            pendingJob.value = saved.jobId || "";
            pendingTasks.value = Array.isArray(saved.tasks)
              ? saved.tasks
              : pendingJob.value
                ? [{ jobId: pendingJob.value, scope: "business" }]
                : [];
            conversationId.value = saved.conversationId;
            chatOrderNo.value = saved.chatOrderNo || "";
            chatIncidentId.value = saved.chatIncidentId || "";
          } else sessionStorage.removeItem("yc-recovery");
        } catch {
          sessionStorage.removeItem("yc-recovery");
        }
      }
      await refresh();
      await probeRuntime();
      if (pendingJob.value)
        notice.value = "发现上次未结束的任务，可点击恢复查询。";
    });
  }
  async function demo(
    role: "DISPATCHER" | "DRIVER",
    scenario: "legacy" | "wuhan" = "legacy",
  ) {
    reset();
    demoScenario.value = scenario;
    configureDemoScenario(scenario, role);
    mode.value = "demo";
    user.value = {
      user_id: role === "DRIVER" ? "D01" : "DEMO_DISPATCHER",
      username: role === "DRIVER" ? "演示司机" : "演示调度员",
      role,
      vehicle_id: role === "DRIVER" ? "V01" : null,
    };
    await guard(refresh);
  }
  async function loadContext(vehicleId: string, signal?: AbortSignal) {
    const run = epoch;
    const ctx = await call<Context>(
      "/delivery-context" + (vehicleId ? "?vehicle_id=" + id(vehicleId) : ""),
      undefined,
      undefined,
      signal,
    );
    if (run === epoch && !signal?.aborted) {
      context.value = ctx;
      if (!conversationId.value && !chatOrderNo.value && !chatIncidentId.value)
        chatOrderNo.value =
          ctx.selected_order_no ||
          ctx.orders.find(
            (o) => o.status !== "COMPLETED" && o.status !== "CANCELLED",
          )?.order_no ||
          "";
      selectedVehicleId.value = vehicleId;
    }
  }
  async function refresh(signal?: AbortSignal) {
    const run = epoch;
    const [list, vehicles] = await Promise.all([
      call<{ incidents: Incident[] }>(
        "/incidents",
        undefined,
        undefined,
        signal,
      ),
      isDriver.value
        ? Promise.resolve(null)
        : call<{
            vehicles: {
              vehicle_id: string;
              vehicle_no: string;
              driver_id: string | null;
              status: string;
            }[];
          }>("/vehicles", undefined, undefined, signal),
    ]);
    if (run !== epoch || signal?.aborted) return;
    incidents.value =
      mode.value === "demo" && isDriver.value
        ? list.incidents.filter((i) => i.vehicle_id === user.value?.vehicle_id)
        : list.incidents;
    if (vehicles) fleet.value = vehicles.vehicles;
    const chosen = isDriver.value
      ? user.value?.vehicle_id || ""
      : workspace.value?.incident.vehicle_id ||
        selectedVehicleId.value ||
        list.incidents[0]?.vehicle_id ||
        fleet.value[0]?.vehicle_id ||
        "";
    if (chosen || isDriver.value) await loadContext(chosen, signal);
    else context.value = null;
    if (workspace.value)
      await openIncident(workspace.value.incident.incident_id, false, signal);
    else {
      const current = list.incidents.find((i) => i.vehicle_id === chosen);
      if (current) await openIncident(current.incident_id, false, signal);
    }
  }
  async function chooseVehicle(vehicleId: string) {
    cancelPolling();
    setChatContext();
    await guard(async () => {
      workspace.value = null;
      selectedPlanId.value = "";
      generation.value = null;
      selectedVehicleId.value = vehicleId;
      await refresh();
    });
  }
  async function openIncident(
    incidentId: string,
    refreshContext = true,
    signal?: AbortSignal,
  ) {
    const run = epoch;
    const data = await call<WorkspaceData>(
      "/incidents/" + id(incidentId) + "/workspace",
      undefined,
      undefined,
      signal,
    );
    if (run !== epoch) return;
    workspace.value = data;
    if (refreshContext) await loadContext(data.incident.vehicle_id, signal);
    if (!data.plans.some((p) => p.plan_id === selectedPlanId.value))
      selectedPlanId.value =
        data.plans.find((p) => !expired(p) && p.status === "PENDING_APPROVAL")
          ?.plan_id ||
        data.plans[0]?.plan_id ||
        "";
  }
  async function write<T>(path: string, body: unknown): Promise<T> {
    if (pending.value)
      throw Error("上次写入结果尚未确认，请先使用“恢复查询”核实。");
    const op = {
      userId: user.value!.user_id,
      key: crypto.randomUUID(),
      path,
      body,
      ...(path === "/chat"
        ? {
            chatContext: {
              conversationId: conversationId.value,
              orderNo: chatOrderNo.value,
              incidentId: chatIncidentId.value,
            },
          }
        : {}),
    };
    const submittedRevision = queryRevision,
      submittedEpoch = epoch;
    pending.value = op;
    persist();
    try {
      const result = await call<T>(path, body, op.key);
      if (submittedEpoch !== epoch || user.value?.user_id !== op.userId)
        throw new DOMException("登录身份已切换", "AbortError");
      pending.value = null;
      persist();
      const queued = result as { job_id?: string; conversation_id?: string };
      if (
        submittedRevision !== queryRevision &&
        queued &&
        typeof queued.job_id === "string"
      ) {
        const task: TaskRef =
          path === "/chat"
            ? {
                jobId: queued.job_id,
                scope: "chat",
                conversationId: queued.conversation_id,
                orderNo: op.chatContext?.orderNo,
                incidentId: op.chatContext?.incidentId,
              }
            : { jobId: queued.job_id, scope: "business" };
        if (
          task.scope === "chat" &&
          task.orderNo === chatOrderNo.value &&
          task.incidentId === chatIncidentId.value
        )
          conversationId.value = task.conversationId;
        addTask(task);
        notice.value = "操作已受理；页面已切换，保留任务引用供恢复查询。";
        throw new DOMException("当前页查询已停止", "AbortError");
      }
      return result;
    } catch (e) {
      if (
        submittedEpoch === epoch &&
        e instanceof ApiError &&
        e.status > 0 &&
        e.status < 500
      ) {
        pending.value = null;
        persist();
      }
      throw e;
    }
  }
  function addTask(task: TaskRef) {
    if (!pendingTasks.value.some((t) => t.jobId === task.jobId))
      pendingTasks.value.push(task);
    pendingJob.value = task.jobId;
    persist();
  }
  function finishTask(id: string) {
    pendingTasks.value = pendingTasks.value.filter((t) => t.jobId !== id);
    pendingJob.value = pendingTasks.value[0]?.jobId || "";
    persist();
  }
  async function poll(jobId: string, initialDelay = 0, task?: TaskRef) {
    const owner: TaskRef = task || { jobId, scope: "business" };
    const controller = new AbortController();
    activePoll?.abort();
    activePoll = controller;
    activeScope = owner.scope;
    polling.value = true;
    const run = epoch;
    const queue = [jobId];
    const visited = new Set<string>();
    addTask({ ...owner, jobId });
    try {
      while (queue.length) {
        const current = queue.shift()!;
        if (visited.has(current)) continue;
        visited.add(current);
        if (visited.size > 32)
          throw Error("关联任务数量异常，已停止查询，请核实服务端结果。");
        pendingJob.value = current;
        persist();
        let done = false;
        if (initialDelay && mode.value === "api")
          await abortableDelay(initialDelay, controller.signal);
        for (let i = 0; i < 45; i++) {
          if (run !== epoch || controller.signal.aborted)
            throw new DOMException("查询已取消", "AbortError");
          const data = await call<Job>(
            "/jobs/" + id(current),
            undefined,
            undefined,
            controller.signal,
          );
          if (run !== epoch || controller.signal.aborted)
            throw new DOMException("查询已取消", "AbortError");
          if (data.result_kind !== data.job_type)
            throw Error("任务类型与结果类型不一致，请核实后端响应。");
          job.value = data;
          if (
            owner.scope === "chat" &&
            owner.conversationId === conversationId.value
          )
            chatJob.value = data;
          if (data.job_status === "FAILED") {
            finishTask(current);
            throw Error(data.error?.reason || "后台任务失败，请核实后重试。");
          }
          if (data.job_status === "UNKNOWN")
            throw Error("任务状态未知，结果尚不能确认；请使用恢复查询。");
          if (data.job_status === "SUCCEEDED") {
            if (!data.result)
              throw Error("任务已结束但缺少结构化结果，请核实服务端响应。");
            if (data.result_kind === "CHAT") {
              if (!isRunResult(data.result))
                throw Error("CHAT 返回不符合 RunResult 契约。");
              const result = data.result;
              if (
                owner.scope === "chat" &&
                owner.conversationId === conversationId.value
              ) {
                chatResult.value = result;
                if (!seenResults.has(current)) {
                  messages.value.push({
                    role: "assistant",
                    text: result.reply,
                    jobId: current,
                  });
                  seenResults.add(current);
                }
              }
              if (result.incident_id)
                await openIncident(result.incident_id, true, controller.signal);
              for (const child of [...new Set(result.job_ids)]) {
                if (visited.has(child)) continue;
                addTask({ ...owner, jobId: child });
                queue.push(child);
              }
            } else if (data.result_kind === "PLAN_GENERATION") {
              if (!("solution_status" in data.result && "plans" in data.result))
                throw Error("候选计算返回类型不匹配。");
              generation.value = data.result as Schema["GenerationOutput"];
              if (owner.scope === "chat")
                chatGeneration.value =
                  data.result as Schema["GenerationOutput"];
            } else if (data.result_kind === "IMPACT_ANALYSIS") {
              if (!(
                "analysis_id" in data.result && "order_impacts" in data.result
              ))
                throw Error("影响分析返回类型不匹配。");
            } else if (data.result_kind === "MAP_ROUTING") {
              if (!isMapRoutingResult(data.result))
                throw Error("地图路由结果类型不匹配");
              mapRouting.value = data.result as Schema["MapRoutingOutput"];
            } else throw Error("未知的任务结果类型，不能作为聊天结果处理。");
            finishTask(current);
            await refresh(controller.signal);
            done = true;
            break;
          }
          await abortableDelay(data.poll_after_seconds, controller.signal);
        }
        if (!done) {
          notice.value = "后台任务仍未确认，已保留任务引用，可稍后恢复查询。";
          return;
        }
      }
      notice.value =
        owner.scope === "chat"
          ? "本轮回复及关联任务已查询；业务进度以服务端状态为准。"
          : "计算作业已返回，已刷新业务数据。";
    } finally {
      if (activePoll === controller) {
        activePoll = null;
        polling.value = false;
      }
    }
  }
  async function recover() {
    await guard(async () => {
      if (pending.value) {
        const op = pending.value;
        const result = await call<Schema["StatusOutput"]>(
          "/status?object_type=REQUEST&object_id=" + id(op.key),
        );
        recoveryResult.value = JSON.stringify(result, null, 2);
        if (result.result_kind === "MAP_ROUTING") {
          if (result.error || result.job_status === "FAILED")
            throw Error(result.error?.reason || "道路路由任务失败");
          if (result.job_status === "SUCCEEDED") {
            if (!isMapRoutingResult(result.result))
              throw Error("道路路由状态结果不符合契约");
            mapRouting.value = result.result;
          }
        }
        notice.value = "已查询原操作，请核对结果后刷新业务数据。";
      }
      if (pendingJob.value) {
        const task = pendingTasks.value.find(
          (t) => t.jobId === pendingJob.value,
        ) || { jobId: pendingJob.value, scope: "business" as const };
        if (
          task.scope === "chat" &&
          conversationId.value !== task.conversationId
        ) {
          setChatContext(task.orderNo, task.incidentId);
          conversationId.value = task.conversationId;
          notice.value = "已恢复任务与会话引用，可从历史会话读取已保存消息。";
        }
        await poll(task.jobId, 0, task);
      }
      await refresh();
    });
  }
  async function retryPending() {
    await guard(async () => {
      const op = pending.value;
      if (!op) return;
      if (op.chatContext) {
        setChatContext(op.chatContext.orderNo, op.chatContext.incidentId);
        conversationId.value = op.chatContext.conversationId;
      }
      const r = await call<{ job_id?: string; conversation_id?: string }>(
        op.path,
        op.body,
        op.key,
      );
      pending.value = null;
      if (r.conversation_id) conversationId.value = r.conversation_id;
      persist();
      if (r.job_id) {
        const body = op.body as Schema["ChatInput"];
        const task: TaskRef =
          op.path === "/chat"
            ? {
                jobId: r.job_id,
                scope: "chat",
                conversationId: r.conversation_id,
                orderNo:
                  op.chatContext?.orderNo ||
                  body.selected_order_no ||
                  undefined,
                incidentId:
                  op.chatContext?.incidentId || body.incident_id || undefined,
              }
            : { jobId: r.job_id, scope: "business" };
        await poll(r.job_id, 0, task);
      } else await refresh();
      notice.value = "已按原幂等键恢复提交。";
    });
  }
  async function analyze() {
    await guard(async () => {
      await refresh();
      const inc = workspace.value!.incident;
      if (!inc.can_analyze)
        throw Error("当前异常尚不具备分析条件，请补全信息或转人工。");
      const r = await write<Schema["QueuedOutput"]>("/impact-analyses", {
        incident_id: inc.incident_id,
        expected_incident_version: inc.incident_version,
        snapshot_id: context.value!.snapshot_id,
      });
      await poll(r.job_id);
    });
  }
  async function generate() {
    await guard(async () => {
      await refresh();
      const inc = workspace.value!.incident;
      const a = analysis.value;
      if (!a) throw Error("请先完成当前版本的影响分析。");
      const r = await write<Schema["QueuedOutput"]>("/recovery-plans", {
        incident_id: inc.incident_id,
        expected_incident_version: inc.incident_version,
        snapshot_id: a.snapshot_id,
        analysis_id: a.analysis_id,
        strategy_profile: "MIN_CHANGE_V1",
      });
      await poll(r.job_id);
    });
  }
  async function planAction(
    action: "approve" | "reject" | "receipts",
    reason = "",
    decision: "ACCEPTED" | "REJECTED" = "ACCEPTED",
  ) {
    await guard(async () => {
      if (isDriver.value && action !== "receipts")
        throw Error("司机不能批准或拒绝调度方案。");
      const prev = selectedPlan.value;
      if (!prev) return;
      const p = await call<Plan>("/plans/" + id(prev.plan_id));
      if (p.plan_version !== prev.plan_version) {
        await refresh();
        throw Error("方案版本已变化，已刷新；请重新核对后操作。");
      }
      if (expired(p)) throw Error("方案已失效，请重新分析并生成候选。");
      if (action === "approve" && p.status !== "PENDING_APPROVAL")
        throw Error("此方案目前不可审批。");
      if (action === "receipts" && p.status !== "APPROVED_PENDING_ACK")
        throw Error("此方案目前不等待司机回执。");
      const body =
        action === "approve"
          ? {
              expected_plan_version: p.plan_version,
              expected_incident_version: p.incident_version,
              snapshot_id: p.snapshot_id,
            }
          : action === "reject"
            ? { expected_plan_version: p.plan_version, reason: reason.trim() }
            : {
                expected_plan_version: p.plan_version,
                decision,
                ...(reason.trim() ? { reason: reason.trim() } : {}),
              };
      try {
        const result = await write<{ reservation_expires_at?: string }>(
          "/plans/" + id(p.plan_id) + "/" + action,
          body,
        );
        if (result.reservation_expires_at)
          reservationExpiry.value[p.plan_id] = result.reservation_expires_at;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) await refresh();
        throw e;
      }
      await refresh();
      notice.value = "操作已记录，页面显示后端最新状态。";
    });
  }
  async function report(input: Schema["IncidentInput"]) {
    await guard(async () => {
      const inc = await write<Incident>("/incidents", input);
      await refresh();
      await openIncident(inc.incident_id);
      notice.value = "异常已登记，尚未代表解决。";
    });
  }
  async function chat(message: string, orderNo?: string) {
    activeScope = "chat";
    if (orderNo && orderNo !== chatOrderNo.value) setChatContext(orderNo);
    await guard(async () => {
      if (chatHistoryIncomplete.value)
        throw Error("活动轮次尚未加载，请继续读取会话历史后再发送。");
      if (chatHasPending.value)
        throw Error("本会话仍有待确认作业，请先恢复查询，不要重复提交。");
      if (!chatOrderNo.value && !chatIncidentId.value && !isDriver.value)
        throw Error("请选择关联订单或异常。");
      const body: Schema["ChatInput"] = {
        message,
        ...(conversationId.value
          ? { conversation_id: conversationId.value }
          : {
              ...(chatIncidentId.value
                ? { incident_id: chatIncidentId.value }
                : {}),
              ...(chatOrderNo.value
                ? { selected_order_no: chatOrderNo.value }
                : {}),
            }),
      };
      const userMessage = { role: "user" as const, text: message, jobId: "" };
      messages.value.push(userMessage);
      const r = await write<Schema["ChatQueuedOutput"]>("/chat", body);
      userMessage.jobId = r.job_id;
      conversationId.value = r.conversation_id;
      await poll(r.job_id, r.poll_after_seconds, {
        jobId: r.job_id,
        scope: "chat",
        conversationId: r.conversation_id,
        orderNo: chatOrderNo.value,
        incidentId: chatIncidentId.value,
      });
    });
  }
  function read<T>(path: string, signal?: AbortSignal) {
    return call<T>(path, undefined, undefined, signal);
  }
  function restoreChatHistory(
    meta: Schema["ConversationOutput"],
    turns: Schema["ConversationTurnOutput"][],
    append = false,
  ) {
    const local = append
      ? messages.value.filter(
          (m) => !m.turnId && !turns.some((t) => t.job_id === m.jobId),
        )
      : [];
    conversationId.value = meta.conversation_id;
    chatOrderNo.value = meta.selected_order_no || "";
    chatIncidentId.value = meta.incident_id || "";
    chatResult.value = null;
    chatGeneration.value = null;
    chatJob.value = null;
    messages.value = turns
      .slice()
      .sort(
        (a, b) =>
          Date.parse(a.created_at) - Date.parse(b.created_at) ||
          a.turn_id.localeCompare(b.turn_id),
      )
      .flatMap((t) => [
        {
          role: "user",
          text: t.user_message,
          turnId: t.turn_id,
          createdAt: t.created_at,
          jobId: t.job_id,
        },
        ...(t.assistant_reply === null
          ? []
          : [
              {
                role: "assistant",
                text: t.assistant_reply,
                turnId: t.turn_id,
                createdAt: t.created_at,
                jobId: t.job_id,
              },
            ]),
      ]) as typeof messages.value;
    messages.value.push(...local);
    chatHistoryIncomplete.value =
      !!meta.active_turn_id &&
      !turns.some((t) => t.turn_id === meta.active_turn_id);
    for (const t of turns) {
      if (t.assistant_reply !== null) seenResults.add(t.job_id);
      if (["SUCCEEDED", "FAILED"].includes(t.status)) finishTask(t.job_id);
      if (["QUEUED", "RUNNING", "UNKNOWN"].includes(t.status))
        addTask({
          jobId: t.job_id,
          scope: "chat",
          conversationId: meta.conversation_id,
          orderNo: chatOrderNo.value,
          incidentId: chatIncidentId.value,
        });
    }
    notice.value = "已恢复服务端会话历史；旧消息不代表当前业务状态。";
    persist();
    void refreshChatBusinessState().catch(() => {
      if (conversationId.value === meta.conversation_id)
        notice.value = "历史消息已恢复，当前业务状态读取失败，请刷新后再继续。";
    });
  }
  async function refreshChatBusinessState() {
    const scope = conversationId.value;
    const owner = user.value?.user_id;
    const last = [...messages.value].reverse().find((m) => m.role === "assistant" && m.jobId);
    if (!last?.jobId) return;
    const latest = await call<Job>("/jobs/" + id(last.jobId));
    if (scope !== conversationId.value || owner !== user.value?.user_id) return;
    if (latest.result_kind === "CHAT" && latest.job_status === "SUCCEEDED" && isRunResult(latest.result)) {
      chatResult.value = latest.result;
      chatJob.value = latest;
      if (latest.result.incident_id) await openIncident(latest.result.incident_id, true);
    }
  }
  async function refreshMapRouting() {
    await guard(async () => {
      if (isDriver.value) throw Error("仅调度员可刷新道路数据");
      const state = await call<Schema["MapOutput"]>("/map");
      if (
        state.configuration_status !== "CONFIGURED" ||
        !state.routing_ready ||
        context.value?.routing_ready === false
      )
        throw Error("道路服务尚未就绪，未发起刷新");
      const body: Schema["MapRefreshInput"] = {
        expected_routing_version: state.routing_version,
      };
      const result = await write<Schema["MapQueuedOutput"]>(
        "/map/routing/refresh",
        body,
      );
      await poll(result.job_id, result.poll_after_seconds);
    });
  }
  async function continueChat() {
    if (!chatContinueLabel.value) return;
    if (chatResult.value?.next_action === "RETRY_SAME_KEY") return recover();
    if (chatResult.value?.next_action === "REFRESH_CONTEXT")
      return guard(refresh);
    if (chatResult.value?.next_action === "ANALYZE_IMPACT") {
      await analyze();
      if (!error.value) await guard(refreshChatBusinessState);
      return;
    }
    if (chatResult.value?.next_action === "GENERATE_PLANS") {
      // A button already expresses an explicit authorized business action.
      // Use current versions and the existing deterministic API, not a vague LLM turn.
      await guard(refresh);
      if (error.value) return;
      if (!analysis.value) {
        await analyze();
        if (error.value) return;
      }
      await generate();
      if (!error.value) await guard(refreshChatBusinessState);
      return;
    }
    await chat(continuationMessage(chatResult.value));
  }
  watch(assistantOpen, (open) => {
    if (!open && activeScope === "chat") cancelPolling();
  });
  async function complete(stopId: string, eventType: "ARRIVED" | "COMPLETED") {
    await guard(async () => {
      const v = context.value?.vehicles.find(
        (v) => v.vehicle_id === user.value?.vehicle_id,
      );
      if (!v) return;
      await write("/stops/" + id(stopId) + "/completion", {
        event_type: eventType,
        expected_route_revision: v.route_revision,
        occurred_at:
          context.value?.clock_mode === "SIMULATION"
            ? context.value.clock_time
            : new Date().toISOString(),
      });
      await refresh();
      notice.value = "执行反馈已保存。";
    });
  }
  async function handoff(reason: string) {
    await guard(async () => {
      await refresh();
      const inc = workspace.value!.incident;
      await write("/incidents/" + id(inc.incident_id) + "/manual-handoff", {
        expected_incident_version: inc.incident_version,
        reason,
      });
      await refresh();
      notice.value = "已转人工处理。";
    });
  }
  async function verifyWindow(orderNo: string, start: string, end: string) {
    await guard(async () => {
      await refresh();
      const inc = workspace.value!.incident;
      const order = context.value?.orders.find((o) => o.order_no === orderNo);
      if (!order) throw Error("当前快照没有该订单，请刷新。");
      await write("/incidents/" + id(inc.incident_id) + "/verify-window", {
        order_no: orderNo,
        expected_order_version: order.version,
        expected_incident_version: inc.incident_version,
        window_start: start,
        window_end: end,
      });
      await refresh();
      notice.value = "收货时间窗已核实，请重新分析影响。";
    });
  }
  window.addEventListener("auth-expired", () => {
    reset();
    error.value = "登录已过期，请重新登录。";
    window.location.hash = "/login";
  });
  return {
    read,
    restoreChatHistory,
    chatHistoryIncomplete,
    refreshMapRouting,
    mapRouting,
    demoScenario,
    cancelPolling,
    setChatContext,
    newConversation,
    probeRuntime,
    runtime,
    chatOrderNo,
    chatIncidentId,
    chatResult,
    chatGeneration,
    chatJob,
    chatStateLabel,
    chatContinueLabel,
    chatHasPending,
    continueChat,
    polling,
    pendingTasks,
    conversationId,
    user,
    mode,
    context,
    incidents,
    workspace,
    selectedPlanId,
    selectedPlan,
    analysis,
    busy,
    error,
    notice,
    assistantOpen,
    messages,
    job,
    pending,
    pendingJob,
    recoveryResult,
    search,
    isDriver,
    businessTime,
    realTime: now,
    fleet,
    selectedVehicleId,
    chooseVehicle,
    reservationExpiry,
    expired,
    generation,
    login,
    demo,
    logout,
    guard,
    refresh,
    openIncident,
    recover,
    retryPending,
    analyze,
    generate,
    planAction,
    report,
    chat,
    complete,
    handoff,
    verifyWindow,
  };
});
