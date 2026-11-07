import { ref, watch, onUnmounted, type Ref } from "vue";
import { useOps } from "../stores/ops";
import { ApiError } from "../api/client";
import { getDemoMap } from "../api/extended";
import type { Schema } from "../api/types";
export function useDeliveryMaps(options: {
  enabled: () => boolean;
  scope: Ref<string>;
  preview: () => { id: string; version: number; vehicleId: string } | null;
}) {
  const ops = useOps(),
    current = ref<Schema["DemoMapOutput"]>(),
    preview = ref<Schema["DemoMapOutput"]>(),
    loading = ref(false),
    previewLoading = ref(false),
    error = ref(""),
    previewError = ref("");
  let currentRun = 0,
    previewRun = 0,
    currentAbort: AbortController | undefined,
    previewAbort: AbortController | undefined,
    blocked = "",
    currentIdentity = "",
    previewIdentity = "";
  const usable = () =>
    options.enabled() &&
    !!ops.user &&
    !!ops.context &&
    (ops.mode === "api" || ops.demoScenario === "wuhan");
  const read = <T>(path: string, signal?: AbortSignal) =>
    ops.read<T>(path, signal);
  async function loadCurrent() {
    currentAbort?.abort();
    const run = ++currentRun;
    const identity = [
      ops.user?.user_id,
      ops.mode,
      ops.demoScenario,
      options.scope.value,
    ].join(":");
    if (identity !== currentIdentity || !usable()) current.value = undefined;
    currentIdentity = identity;
    error.value = "";
    loading.value = false;
    if (!usable()) return;
    const controller = (currentAbort = new AbortController());
    loading.value = true;
    try {
      const result = await getDemoMap(
        ops.isDriver ? "" : options.scope.value,
        "",
        controller.signal,
        read,
      );
      if (run === currentRun && !controller.signal.aborted)
        current.value = result;
    } catch (e) {
      if (!controller.signal.aborted && run === currentRun) {
        current.value = undefined;
        error.value = e instanceof Error ? e.message : "地图业务数据暂不可用";
      }
    } finally {
      if (run === currentRun) loading.value = false;
    }
  }
  async function loadPreview() {
    previewAbort?.abort();
    const run = ++previewRun;
    previewLoading.value = false;
    const selected = options.preview(),
      key = selected
        ? [
            ops.user?.user_id,
            ops.mode,
            ops.demoScenario,
            selected.id,
            selected.version,
            selected.vehicleId,
          ].join(":")
        : "";
    if (key !== previewIdentity || !usable()) preview.value = undefined;
    previewIdentity = key;
    if (key !== blocked) previewError.value = "";
    if (!usable() || !selected || key === blocked) return;
    const controller = (previewAbort = new AbortController());
    previewLoading.value = true;
    try {
      const result = await getDemoMap(
        selected.vehicleId,
        selected.id,
        controller.signal,
        read,
      );
      if (run !== previewRun || controller.signal.aborted) return;
      if (
        result.routes.some(
          (r) =>
            r.route_kind !== "CANDIDATE_PREVIEW" ||
            r.plan_id !== selected.id ||
            r.plan_version !== selected.version,
        )
      )
        throw Error("候选地图版本不一致，请刷新方案");
      preview.value = result;
    } catch (e) {
      if (controller.signal.aborted || run !== previewRun) return;
      preview.value = undefined;
      if (e instanceof ApiError && e.status === 409) {
        blocked = key;
        previewError.value = "候选地图已失效，已停止旧预览；请刷新并重新分析。";
        void ops.refresh().catch(() => {
          previewError.value += "业务对象刷新失败，请手动刷新。";
        });
      } else
        previewError.value =
          e instanceof Error ? e.message : "候选地图暂不可用";
    } finally {
      if (run === previewRun) previewLoading.value = false;
    }
  }
  watch(
    () => [
      options.enabled(),
      ops.user?.user_id,
      ops.mode,
      ops.demoScenario,
      options.scope.value,
      ops.context?.snapshot_id,
      ops.context?.snapshot_version,
      ops.context?.route_stops
        .map((s) => s.stop_id + ":" + s.status + ":" + s.sequence_no)
        .join("|"),
      ops.context?.vehicles
        .map((v) => v.vehicle_id + ":" + v.route_revision)
        .join("|"),
    ],
    loadCurrent,
    { immediate: true },
  );
  watch(
    () => [
      options.enabled(),
      ops.user?.user_id,
      ops.mode,
      ops.demoScenario,
      options.scope.value,
      options.preview()?.id,
      options.preview()?.version,
      options.preview()?.vehicleId,
      ops.context?.snapshot_id,
    ],
    loadPreview,
    { immediate: true },
  );
  onUnmounted(() => {
    currentAbort?.abort();
    previewAbort?.abort();
    currentRun++;
    previewRun++;
  });
  return {
    current,
    preview,
    loading,
    previewLoading,
    error,
    previewError,
    refresh: async () => {
      await Promise.all([loadCurrent(), loadPreview()]);
    },
  };
}
