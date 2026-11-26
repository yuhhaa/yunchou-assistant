<script setup lang="ts">
import { ref } from "vue";
import {
  Plus,
  Minus,
  LocateFixed,
  Box,
  Map as MapIcon,
  Palette,
  Check,
} from "lucide-vue-next";
import { mapThemes } from "./mapThemes";
defineProps<{ ready: boolean; is3d?: boolean; theme?: string }>();
const emit = defineEmits<{
  zoomIn: [];
  zoomOut: [];
  fit: [];
  toggle3d: [];
  theme: [string];
}>();
const open = ref(false);
function pick(id: string) {
  emit("theme", id);
  open.value = false;
}
</script>
<template>
  <div class="amap-toolbar" aria-label="地图视野控制">
    <div class="amap-theme-wrap">
      <button
        type="button"
        :disabled="!ready"
        class="amap-theme-toggle"
        :class="{ active: open }"
        aria-label="切换地图配色"
        title="切换地图配色"
        @click="open = !open"
      >
        <Palette :size="17" />
      </button>
      <div v-if="open" class="amap-theme-menu" role="menu">
        <b>地图配色</b>
        <button
          type="button"
          v-for="t in mapThemes"
          :key="t.id"
          role="menuitemradio"
          :aria-checked="theme === t.id"
          :class="{ on: theme === t.id }"
          @click="pick(t.id)"
        >
          <i :style="{ background: t.swatch }" /><span>{{ t.name }}</span>
          <Check v-if="theme === t.id" :size="14" />
        </button>
      </div>
    </div>
    <button
      type="button"
      :disabled="!ready"
      class="amap-3d-toggle"
      :class="{ active: is3d }"
      :aria-label="is3d ? '切换到 2D 平面' : '切换到 3D 立体'"
      :title="is3d ? '切换到 2D 平面' : '切换到 3D 立体'"
      @click="$emit('toggle3d')"
    >
      <MapIcon v-if="is3d" :size="16" /><Box v-else :size="16" />
      <span>{{ is3d ? "2D" : "3D" }}</span>
    </button>
    <button
      type="button"
      :disabled="!ready"
      aria-label="放大地图"
      @click="$emit('zoomIn')"
    >
      <Plus :size="18" />
    </button>
    <button
      type="button"
      :disabled="!ready"
      aria-label="缩小地图"
      @click="$emit('zoomOut')"
    >
      <Minus :size="18" />
    </button>
    <button
      type="button"
      :disabled="!ready"
      aria-label="回到当前路线"
      @click="$emit('fit')"
    >
      <LocateFixed :size="18" />
    </button>
  </div>
</template>
