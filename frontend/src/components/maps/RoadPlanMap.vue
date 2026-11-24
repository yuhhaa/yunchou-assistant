<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { id } from "../../api/client";
import type { Plan, Schema } from "../../api/types";
import { useOps } from "../../stores/ops";
import { time } from "../../lib/format";
import type { MapModel } from "../../services/maps/mapDataAdapter";
import AmapMap from "./AmapMap.vue";

const props = defineProps<{
  plan: Plan;
  vehicleId: string;
  area?: Schema["RoadComparison"]["avoid_area"];
}>();
const ops = useOps();
const road = ref<Schema["MapRouteOutput"]>();
const error = ref("");
const retry = ref(0);
const model = computed<MapModel | undefined>(() => {
  const data = road.value;
  if (!data) return;
  const geometry = data.geometry;
  const parts = !geometry ? [] : geometry.type === "LineString" ? [geometry.coordinates] : geometry.coordinates;
  const origin = data.reported_origin ?? parts[0]?.[0];
  const layers: MapModel["layers"] = parts.map((coordinates) => ({
    role: data.route_kind === "CURRENT" ? "current" : "candidate",
    coordinates,
    geometryKind: "ROAD",
  }));
  if (props.area && props.area.length)
    layers.unshift({
      role: "original",
      coordinates: [...props.area, props.area[0]!],
      geometryKind: "SCHEMATIC",
    });
  return {
    layers,
    markers: origin
      ? [
          {
            id: "road-origin",
            label: "上报位置",
            kind: "vehicle",
            coordinates: origin,
          },
        ]
      : [],
    issues: [],
    source: data.source,
    observedAt: data.retrieved_at,
    version: String(data.plan_version),
    demo: false,
  };
});

watch(
  () => [
    props.plan.plan_id,
    props.plan.plan_version,
    props.plan.status,
    props.vehicleId,
    ops.context?.vehicles[0]?.route_revision,
  ],
  async (_, __, onCleanup) => {
    const controller = new AbortController();
    onCleanup(() => controller.abort());
    road.value = undefined;
    error.value = "";
    try {
      road.value = await ops.read<Schema["MapRouteOutput"]>(
        `/map/route?vehicle_id=${id(props.vehicleId)}&plan_id=${id(props.plan.plan_id)}`,
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted)
        error.value = e instanceof Error ? e.message : "未能读取道路轨迹";
    }
  },
  { immediate: true },
);
</script>

<template>
  <section class="road-plan-map" aria-label="现场道路轨迹">
    <p v-if="error" role="status">{{ error }}</p>
    <template v-else-if="model && road">
      <p>
        {{ road.route_kind === "CURRENT" ? "已生效路线" : "候选轨迹" }} ·
        高德道路 · {{ (road.distance_m / 1000).toFixed(1) }} km · 计算于
        {{ time(road.retrieved_at)
        }}<template v-if="area?.length"> · 虚线为报告避让范围</template>
      </p>
      <div class="road-map-canvas">
        <AmapMap
          :model="model"
          :vehicle-id="vehicleId"
          :plan-id="plan.plan_id"
          selected-order=""
          focus-order=""
          :candidate="road.route_kind !== 'CURRENT'"
          :changed-orders="[]"
          :retry="retry"
          @retry="retry++"
        />
      </div>
    </template>
    <p v-else role="status">正在读取道路轨迹…</p>
  </section>
</template>

<style scoped>
.road-map-canvas {
  position: relative;
  height: 330px;
}
.road-plan-map p {
  font-size: 0.9rem;
  margin: 0.65rem 0;
}
</style>
