<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import type { Schema } from "../../api/types";
import type { LngLat } from "../../services/maps/amapSdk";
import type { MapModel } from "../../services/maps/mapDataAdapter";
import AmapMap from "./AmapMap.vue";

const props = defineProps<{ initial?: Schema["RoadReport"] | null }>();
const report = defineModel<Schema["RoadReport"] | null>({ required: true });
const location = ref<Schema["RoadLocation"]>();
const roadName = ref(props.initial?.congestion.road_name || "");
const direction = ref(props.initial?.congestion.direction || "");
const start = ref<LngLat | undefined>(props.initial?.congestion.start);
const end = ref<LngLat | undefined>(props.initial?.congestion.end);
const picking = ref<"location" | "start" | "end">("location");
const retry = ref(0);
const locating = ref(false);
const message = ref("");
let alive = true;
const labels = { location: "车辆当前位置", start: "拥堵起点", end: "拥堵终点" };

const model = computed<MapModel>(() => {
  const point: LngLat | undefined =
    location.value?.coordinate_system === "GCJ02"
      ? [location.value.longitude, location.value.latitude]
      : undefined;
  const markers: MapModel["markers"] = [];
  if (point)
    markers.push({
      id: "location",
      label: labels.location,
      coordinates: point,
      kind: "vehicle",
    });
  if (start.value)
    markers.push({
      id: "start",
      label: labels.start,
      coordinates: start.value,
      kind: "stop",
    });
  if (end.value)
    markers.push({
      id: "end",
      label: labels.end,
      coordinates: end.value,
      kind: "stop",
    });
  return {
    markers,
    layers:
      start.value && end.value
        ? [
            {
              role: "original",
              coordinates: [start.value, end.value],
              geometryKind: "SCHEMATIC",
            },
          ]
        : [],
    issues: [],
    source: "AMAP",
    version: "road-pick",
    observedAt: "",
    demo: false,
    center: start.value || point,
  };
});

function place(point: LngLat) {
  message.value = "";
  if (picking.value === "location") {
    location.value = {
      longitude: point[0],
      latitude: point[1],
      captured_at: new Date().toISOString(),
      source: "MAP_PIN",
      coordinate_system: "GCJ02",
      accuracy_m: null,
    };
    picking.value = "start";
  } else if (picking.value === "start") {
    start.value = point;
    picking.value = "end";
  } else end.value = point;
}

function locate() {
  if (!navigator.geolocation || !isSecureContext) {
    message.value = "当前环境无法获取 GPS，请在地图上选点。";
    return;
  }
  locating.value = true;
  message.value = "";
  navigator.geolocation.getCurrentPosition(
    ({ coords, timestamp }) => {
      if (!alive) return;
      locating.value = false;
      if (coords.accuracy > 250) {
        message.value = "本次定位精度不足，请重试或在地图上选点。";
        return;
      }
      location.value = {
        longitude: coords.longitude,
        latitude: coords.latitude,
        accuracy_m: coords.accuracy,
        captured_at: new Date(timestamp).toISOString(),
        source: "GPS",
        coordinate_system: "WGS84",
      };
      picking.value = "start";
    },
    () => {
      if (!alive) return;
      locating.value = false;
      message.value = "未取得 GPS 位置，请重试或在地图上选点。";
    },
    { enableHighAccuracy: true, maximumAge: 0, timeout: 10000 },
  );
}

watch([location, roadName, direction, start, end], () => {
  report.value =
    location.value &&
    start.value &&
    end.value &&
    roadName.value.trim() &&
    direction.value.trim()
      ? {
          location: location.value,
          congestion: {
            road_name: roadName.value.trim(),
            direction: direction.value.trim(),
            start: start.value,
            end: end.value,
          },
        }
      : null;
});
onUnmounted(() => {
  alive = false;
});
</script>

<template>
  <fieldset class="road-fields">
    <legend>车辆位置与拥堵路段</legend>
    <div class="form-row">
      <label
        >道路名称<input
          v-model="roadName"
          required
          maxlength="80"
          placeholder="例如：雄楚大道"
      /></label>
      <label
        >行驶方向<input
          v-model="direction"
          required
          maxlength="48"
          placeholder="例如：向东"
      /></label>
    </div>
    <div class="button-row">
      <button
        type="button"
        class="secondary"
        :disabled="locating"
        @click="locate"
      >
        {{ locating ? "正在定位" : "获取当前位置" }}
      </button>
      <button
        v-for="(label, key) in labels"
        :key="key"
        type="button"
        class="secondary"
        :aria-pressed="picking === key"
        @click="picking = key"
      >
        {{ label }}
      </button>
    </div>
    <small
      >点击地图选择{{ labels[picking] }}。已选：{{
        location
          ? location.source === "GPS"
            ? "GPS 位置"
            : "车辆位置"
          : "位置待确认"
      }}
      · {{ start ? "已有起点" : "起点待选" }} ·
      {{ end ? "已有终点" : "终点待选" }}</small
    >
    <p v-if="message" role="status">{{ message }}</p>
    <div class="road-pick-map">
      <AmapMap
        :model="model"
        selected-order=""
        focus-order=""
        vehicle-id=""
        :plan-id="null"
        :candidate="false"
        :changed-orders="[]"
        :retry="retry"
        picking
        @pick="place"
        @retry="retry++"
      />
    </div>
  </fieldset>
</template>

<style scoped>
.road-fields {
  min-width: 0;
  border: 1px solid #d5dedb;
  border-radius: 8px;
  padding: 12px;
}
.road-fields .button-row {
  flex-wrap: wrap;
  margin: 8px 0;
}
.road-fields [aria-pressed="true"] {
  box-shadow: inset 0 0 0 2px #237267;
}
.road-pick-map {
  position: relative;
  height: 260px;
  margin-top: 8px;
}
</style>
