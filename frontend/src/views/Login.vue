<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import {
  ArrowRight,
  ArrowUpRight,
  LockKeyhole,
  Truck,
  ShieldCheck,
} from "lucide-vue-next";
import { useOps } from "../stores/ops";
const ops = useOps(),
  router = useRouter(),
  username = ref(""),
  password = ref(""),
  showDemo = ref(false),
  wuhanDemo = ref(false);
async function login() {
  await ops.login(username.value, password.value);
  password.value = "";
  if (ops.user) router.push(ops.isDriver ? "/driver" : "/overview");
}
async function demo(role: "DISPATCHER" | "DRIVER") {
  await ops.demo(role, wuhanDemo.value ? "wuhan" : "legacy");
  router.push(role === "DRIVER" ? "/driver" : "/overview");
}
</script>
<template>
  <main class="login-page">
    <section class="login-visual">
      <div class="login-wordmark">
        <span class="brand-symbol"><i /><i /><i /></span>运筹助手
        <span>YUNCHOU</span>
      </div>
      <div class="login-story">
        <span class="eyebrow">城市配送运营系统</span>
        <h1>运筹助手</h1>
        <p>
          城市配送异常处置与最小扰动恢复。<br />查看任务、上报异常并确认配送调整。
        </p>
        <div class="login-tags">
          <span>异常上报</span><span>方案恢复与执行</span>
        </div>
      </div>
      <div class="login-scene-note">原创微缩城市场景 / 非真实地理底图</div>
    </section>
    <section class="login-form-side">
      <div class="login-form-wrap">
        <div class="section-kicker">账号登录</div>
        <h2>进入工作空间</h2>
        <p>连接您的车队，继续今天的配送。</p>
        <form @submit.prevent="login">
          <label
            >工作账号<input
              v-model="username"
              autocomplete="username"
              placeholder="请输入调度员或司机账号"
              required
              maxlength="128" /></label
          ><label
            >登录密码<input
              v-model="password"
              type="password"
              autocomplete="current-password"
              placeholder="请输入密码"
              required
              maxlength="256"
          /></label>
          <div class="login-security">
            <LockKeyhole :size="14" /> 角色由业务服务验证，密码仅用于本次登录
          </div>
          <button class="primary full" :disabled="ops.busy">
            {{ ops.busy ? "正在连接…" : "登录工作空间"
            }}<ArrowRight :size="18" />
          </button>
        </form>
        <div class="login-divider"><span>探索产品体验</span></div>
        <button
          class="secondary full"
          @click="
            wuhanDemo = false;
            showDemo = !showDemo;
          "
        >
          体验独立演示空间 <ArrowUpRight :size="17" />
        </button>
        <button
          class="secondary full wuhan-demo-entry"
          @click="
            wuhanDemo = true;
            showDemo = true;
          "
        >
          武汉联调本地样例 <ArrowUpRight :size="17" />
        </button>
        <div v-if="showDemo" class="demo-choices">
          <b v-if="wuhanDemo">武汉模拟配送 · 本地样例</b>
          <p>此模式使用独立样例，所有指标均为演示数据，不连接后端。</p>
          <button @click="demo('DISPATCHER')">
            <ShieldCheck :size="18" /> 调度员演示
            <ArrowRight :size="16" /></button
          ><button @click="demo('DRIVER')">
            <Truck :size="18" /> 司机演示 <ArrowRight :size="16" />
          </button>
        </div>
        <div class="login-footnote">
          <span class="status-dot" /> SIMULATION 环境
          <small>华为 ICT 创新赛 · 赛题一</small>
        </div>
      </div>
    </section>
  </main>
</template>
