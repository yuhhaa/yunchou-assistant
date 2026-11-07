<script setup lang="ts">
import { Plus, Minus, LocateFixed } from "lucide-vue-next";
defineProps<{ ready: boolean }>();
defineEmits<{ zoomIn: []; zoomOut: []; fit: [] }>();
</script>
<template>
  <div class="amap-toolbar" aria-label="地图视野控制">
    <button :disabled="!ready" aria-label="放大地图" @click="$emit('zoomIn')">
      <Plus :size="18" />
    </button>
    <button :disabled="!ready" aria-label="缩小地图" @click="$emit('zoomOut')">
      <Minus :size="18" />
    </button>
    <button :disabled="!ready" aria-label="回到当前路线" @click="$emit('fit')">
      <LocateFixed :size="18" />
    </button>
  </div>
</template>
