<script setup lang="ts">
import { useRoute, useRouter } from "vue-router";
import {
  ArrowUpRight,
  Bell,
  CircleHelp,
  LogOut,
  Menu,
  RefreshCw,
  MessageSquareText,
  X,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
} from "lucide-vue-next";
import { ref } from "vue";
import { useOps } from "./stores/ops";
import Assistant from "./components/Assistant.vue";
import { label } from "./lib/format";
const ops = useOps(),
  route = useRoute(),
  router = useRouter(),
  menu = ref(false);
const links = [
  ["/overview", "城市总览"],
  ["/incidents", "异常工作台"],
  ["/plans", "恢复方案"],
];
function logout() {
  ops.assistantOpen = false;
  ops.logout();
  router.push("/login");
}
</script>
<template>
  <div
    class="app"
    :class="{
      'driver-app': ops.isDriver,
      'overview-app': route.path === '/overview',
      'collaboration-open':
        ops.assistantOpen && !!ops.user && route.path !== '/login',
    }"
  >
    <header v-if="route.path !== '/login' && ops.user" class="app-header">
      <RouterLink :to="ops.isDriver ? '/driver' : '/overview'" class="brand"
        ><span class="brand-symbol"><i /><i /><i /></span
        ><span>运筹助手<small>YUNCHOU / MOBILITY</small></span></RouterLink
      >
      <span v-if="ops.isDriver" class="driver-header-role">司机工作空间</span>
      <nav v-if="!ops.isDriver" class="pill-nav" :class="{ open: menu }">
        <RouterLink
          v-for="[to, title] in links"
          :key="to"
          :to="to!"
          @click="menu = false"
          >{{ title }}</RouterLink
        >
      </nav>
      <div class="header-actions">
        <span class="environment"
          ><span class="status-dot" />
          {{ ops.mode === "demo" ? "DEMO · 演示数据" : "SIMULATION" }}</span
        ><button
          class="icon-button refresh"
          aria-label="刷新业务数据"
          :disabled="ops.busy"
          @click="ops.guard(ops.refresh)"
        >
          <RefreshCw :size="17" :class="{ spinning: ops.busy }" /></button
        ><button
          class="icon-button"
          aria-label="打开智能助手"
          title="事件协同"
          @click="ops.assistantOpen = !ops.assistantOpen"
        >
          <MessageSquareText :size="19" /><span
            class="collaboration-entry-label"
            >事件协同</span
          ></button
        ><button class="user-button" @click="logout" title="退出登录">
          <span>{{ ops.isDriver ? "司" : "调" }}</span
          ><span class="user-name">{{ ops.user.username }}</span
          ><LogOut :size="15" /></button
        ><button
          v-if="!ops.isDriver"
          class="icon-button menu-toggle"
          aria-label="展开导航"
          @click="menu = !menu"
        >
          <Menu :size="20" />
        </button>
      </div>
    </header>
    <div v-if="ops.error" class="feedback error" role="alert">
      <AlertTriangle :size="18" /><span>{{ ops.error }}</span
      ><button
        class="icon-button"
        aria-label="关闭错误提示"
        @click="ops.error = ''"
      >
        <X :size="16" />
      </button>
    </div>
    <div v-if="ops.notice" class="feedback notice" role="status">
      <CheckCircle2 :size="18" /><span>{{ ops.notice }}</span
      ><button
        class="icon-button"
        aria-label="关闭提示"
        @click="ops.notice = ''"
      >
        <X :size="16" />
      </button>
    </div>
    <div
      v-if="ops.user && (ops.pending || ops.pendingJob)"
      class="recovery-banner"
    >
      <span>存在待核实操作 {{ ops.pendingJob || ops.pending?.key }}</span
      ><button @click="ops.recover" :disabled="ops.busy">
        恢复查询 <ArrowRight :size="14" /></button
      ><button
        v-if="ops.pending"
        @click="ops.retryPending"
        :disabled="ops.busy"
      >
        按原键重试
      </button>
    </div>
    <details v-if="ops.recoveryResult" class="recovery-result">
      <summary>原操作查询结果</summary>
      <pre>{{ ops.recoveryResult }}</pre>
    </details>
    <div v-if="ops.pendingJob" class="task-banner" role="status">
      <RefreshCw :size="15" :class="{ spinning: ops.busy }" /><span
        >{{
          ops.job?.job_id === ops.pendingJob
            ? label(ops.job.job_status)
            : "等待任务状态"
        }}
        · {{ ops.pendingJob }}</span
      ><small>任务受理不等于业务已完成</small>
    </div>
    <RouterView /><Assistant />
    <footer v-if="route.path !== '/login' && !ops.isDriver" class="app-footer">
      <span>运筹助手</span
      ><span
        >配送异常处置 <i />
        {{
          ops.mode === "demo"
            ? "独立前端演示 · 所有指标为样例"
            : "业务仿真环境 · 非生产调度"
        }}</span
      >
    </footer>
  </div>
</template>
