import type { Schema } from "../api/types";
export type RunResult = Schema["RunResult"];
export function chatBusinessLabel(result: RunResult | null): string {
  if (!result) return "等待描述异常";
  const states: Record<RunResult["business_state"], string> = {
    NEEDS_INFO:
      result.next_action === "ASK_USER" ? "等待补充信息" : "等待下一步操作",
    PROCESSING: "业务处理中",
    WAITING_APPROVAL: "等待调度员批准",
    WAITING_ACK: "等待司机确认",
    ACTIVE: "新计划已生效",
    MANUAL_REVIEW: "需要人工处理",
    DONE: "本次处置已完成",
  };
  return states[result.business_state];
}
export function continuationLabel(
  result: RunResult | null,
  hasPending: boolean,
): string {
  if (!result || hasPending) return "";
  if (
    result.business_state === "NEEDS_INFO" &&
    result.next_action === "ASK_USER"
  )
    return "";
  const actions: Partial<Record<RunResult["next_action"], string>> = {
    ANALYZE_IMPACT: "继续分析影响",
    GENERATE_PLANS: "继续生成恢复方案",
    REFRESH_CONTEXT: "刷新配送上下文",
    RETRY_SAME_KEY: "查询原操作",
  };
  if (result.business_state === "PROCESSING" && result.next_action === "POLL")
    return "计算已返回，继续处理";
  return actions[result.next_action] || "";
}
export function continuationMessage(result: RunResult | null): string {
  if (result?.next_action === "GENERATE_PLANS")
    return "请为当前异常生成恢复候选方案。先读取已有任务结果，使用当前有效影响分析的 analysis_id、snapshot_id 和事件版本调用 generate_recovery_plans。若分析已过期则先重新分析，不要新建异常，也不要只查询状态后结束。";
  if (result?.next_action === "ANALYZE_IMPACT")
    return "请继续处理当前异常的影响分析。已有有效分析或运行中任务时复用，否则获取最新配送快照并调用 analyze_impact；不要新建异常。";
  return "请继续处理当前异常：读取已有任务结果，已有有效分析则生成恢复候选，已有候选则展示，不要新建异常或重复提交运行中的计算。";
}
export function isRunResult(result: unknown): result is RunResult {
  return (
    !!result &&
    typeof result === "object" &&
    "reply" in result &&
    typeof result.reply === "string" &&
    "business_state" in result &&
    [
      "NEEDS_INFO",
      "PROCESSING",
      "WAITING_APPROVAL",
      "WAITING_ACK",
      "ACTIVE",
      "MANUAL_REVIEW",
      "DONE",
    ].includes(String(result.business_state)) &&
    "next_action" in result &&
    "job_ids" in result &&
    Array.isArray(result.job_ids) &&
    "plan_ids" in result &&
    Array.isArray(result.plan_ids) &&
    "evidence_refs" in result &&
    Array.isArray(result.evidence_refs) &&
    "incident_id" in result
  );
}
export function abortableDelay(seconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted)
      return reject(new DOMException("查询已取消", "AbortError"));
    const stop = () => {
      clearTimeout(timer);
      reject(new DOMException("查询已取消", "AbortError"));
    };
    const timer = setTimeout(
      () => {
        signal.removeEventListener("abort", stop);
        resolve();
      },
      Math.max(0, seconds) * 1000,
    );
    signal.addEventListener("abort", stop, { once: true });
  });
}
