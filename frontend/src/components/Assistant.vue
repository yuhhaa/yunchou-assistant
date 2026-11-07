<script setup lang="ts">
import { ref, nextTick, watch } from "vue";
import {
  X,
  ArrowUp,
  ArrowUpRight,
  History,
  MessageSquareText,
} from "lucide-vue-next";
import { useOps } from "../stores/ops";
import { label } from "../lib/format";
import { useConversations } from "../stores/conversations";

const history = useConversations(),
  historyOpen = ref(false);
watch(historyOpen, (open) => {
  if (open) void history.loadList();
});
const ops = useOps(),
  message = ref(""),
  feed = ref<HTMLElement>();
let returnFocus: HTMLElement | null = null;
watch(
  () => ops.assistantOpen,
  async (open) => {
    if (open) {
      returnFocus = document.activeElement as HTMLElement;
      await nextTick();
      document
        .querySelector<HTMLTextAreaElement>(".assistant-panel textarea")
        ?.focus();
    } else if (returnFocus?.isConnected) returnFocus.focus();
  },
);
async function send() {
  const value = message.value.trim();
  if (!value || history.loading) return;
  await ops.chat(value);
  if (!ops.error) message.value = "";
  await nextTick();
  feed.value?.scrollTo({
    top: feed.value.scrollHeight,
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
}
</script>
<template>
  <aside
    v-if="ops.assistantOpen && ops.user"
    class="assistant-panel"
    aria-label="事件协同"
    @keydown.esc.stop="ops.assistantOpen = false"
  >
    <header class="collaboration-header">
      <MessageSquareText :size="18" />
      <div>
        <b>事件协同</b
        ><small>{{ ops.mode === "demo" ? "本地演示" : "业务仿真" }}</small>
      </div>
      <button
        class="icon-button"
        aria-label="历史会话"
        :aria-expanded="historyOpen"
        @click="historyOpen = !historyOpen"
      >
        <History :size="17" />
      </button>
      <button
        class="icon-button"
        aria-label="关闭助手"
        @click="ops.assistantOpen = false"
      >
        <X :size="18" />
      </button>
    </header>
    <div ref="feed" class="chat-feed">
      <div class="collaboration-context-line">
        <span>当前上下文</span
        ><button
          class="quiet"
          :disabled="ops.busy"
          @click="
            history.cancel();
            ops.newConversation();
          "
        >
          新会话
        </button>
      </div>
      <div class="chat-context">
        <MessageSquareText :size="17" />
        <div>
          <b>{{
            ops.chatIncidentId || ops.chatOrderNo || "尚未选择关联任务"
          }}</b
          ><small
            >{{ ops.conversationId ? "同一会话多轮补充" : "新异常描述" }} ·
            {{ ops.chatStateLabel }}</small
          >
        </div>
      </div>
      <div class="conversation-history" v-if="historyOpen">
        <div v-if="historyOpen">
          <p v-if="history.error" class="assistant-error">
            {{ history.error }}
          </p>
          <p v-if="history.listLoading">正在读取会话…</p>
          <p v-else-if="!history.items.length">当前没有已保存会话。</p>
          <button
            v-for="item in history.items"
            :key="item.conversation_id"
            class="history-item"
            :disabled="history.loading || (ops.busy && !ops.polling)"
            @click="history.restore(item)"
          >
            <b>{{
              item.selected_order_no || item.incident_id || "未关联任务"
            }}</b
            ><span>{{ item.conversation_id }}</span
            ><time>{{
              new Date(item.created_at).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
              })
            }}</time></button
          ><button
            v-if="history.nextOffset !== null"
            class="quiet"
            :disabled="history.listLoading"
            @click="history.loadList(true)"
          >
            加载更多会话
          </button>
        </div>
      </div>
      <p v-if="history.loading" class="history-notice">正在读取会话消息…</p>
      <div
        v-if="
          history.selected &&
          history.selected.conversation_id === ops.conversationId
        "
        class="history-notice"
      >
        服务端历史记录；当前业务状态请刷新核对。<button
          v-if="history.messagesNextOffset !== null"
          class="quiet"
          :disabled="history.loading || ops.busy"
          @click="history.restore(history.selected!, true)"
        >
          加载更多消息</button
        ><small
          v-for="turn in history.turns.filter((t) => t.status !== 'SUCCEEDED')"
          :key="turn.turn_id"
          >{{ turn.turn_id }} · {{ label(turn.status) }}</small
        >
      </div>
      <p v-if="ops.chatHistoryIncomplete" class="assistant-error">
        活动轮次尚未读取，请继续加载消息后再发送。
      </p>
      <p v-if="ops.error" class="assistant-error">{{ ops.error }}</p>

      <div
        v-if="!ops.messages.length && !ops.chatJob && !historyOpen"
        class="assistant-empty"
      >
        <div v-if="ops.workspace" class="related-event-summary">
          <small>{{
            ops.chatIncidentId === ops.workspace.incident.incident_id
              ? "当前关联事件"
              : "相关事件 · 可在下方关联"
          }}</small>
          <b
            >{{ label(ops.workspace.incident.event_type) }} ·
            {{ ops.workspace.incident.vehicle_id }}</b
          >
          <p>{{ ops.workspace.incident.description }}</p>
        </div>
        <h3>{{ ops.chatIncidentId ? "补充事件信息" : "描述配送异常" }}</h3>
        <p>说明现场情况、预计延误或收货时间变化。信息不足时会继续补问。</p>
        <div class="assistant-starters" aria-label="异常描述草稿">
          <span>填写草稿：</span
          ><button type="button" @click="message = '前方道路出现拥堵，'">
            道路拥堵</button
          ><button type="button" @click="message = '收货方希望调整收货时间，'">
            收货时间变化
          </button>
        </div>
      </div>
      <details class="ai-runtime-status">
        <summary>
          {{
            ops.mode === "demo"
              ? "运行说明：本地规则演示"
              : ops.runtime.reachable
                ? "运行说明：后端可访问"
                : "运行说明：后端尚未核实"
          }}
        </summary>
        <p>
          {{
            ops.mode === "demo"
              ? "演示数据与规则均在独立 Mock 层，不代表真实 AI 理解能力。"
              : ops.runtime.agentMode === "local"
                ? "后端报告 local 模式，属于本地规则模拟。"
                : ops.runtime.agentMode === "direct_ai"
                  ? "后端报告 direct_ai 模式；是否使用真实模型仍需部署方提供调用验收证据。"
                  : "Direct AI 接线代码已交付，当前部署及真实模型调用尚未核实。"
          }}
        </p>
        <small>真实模型联调：未验收 · 云端端到端：未验收</small>
      </details>
      <article
        v-for="(m, i) in ops.messages"
        :key="i"
        class="chat-message"
        :class="m.role"
      >
        <small>{{ m.role === "user" ? "您" : "协作回复" }}</small>
        <p>{{ m.text }}</p>
      </article>
      <div v-if="ops.chatJob" class="job-progress">
        <span class="status-dot" /> 异步作业 · {{ label(ops.chatJob.job_status)
        }}<small>{{ ops.chatJob.job_id }}</small>
      </div>
      <button
        v-if="ops.chatHasPending && !ops.polling"
        class="secondary full"
        :disabled="ops.busy"
        @click="ops.recover"
      >
        恢复本轮查询
      </button>
      <section v-if="ops.chatGeneration" class="chat-calculation-outcome">
        <b>{{
          ops.chatGeneration.solution_status === "FEASIBLE"
            ? "候选计算已返回"
            : "本次搜索范围内无可行候选"
        }}</b>
        <p
          v-for="(reason, index) in ops.chatGeneration.infeasible_reasons"
          :key="index"
        >
          {{ reason.message }}
        </p>
        <small>计算作业结束不代表已经批准、生效或完成配送。</small>
      </section>
      <section
        v-if="ops.chatResult"
        class="chat-business-result"
        aria-label="AI业务结果"
      >
        <b>{{ ops.chatStateLabel }}</b>
        <p>下一步：{{ label(ops.chatResult.next_action) }}</p>
        <small v-if="ops.chatResult.business_state === 'ACTIVE'"
          >计划生效不等于配送完成，仍需逐站执行与反馈。</small
        ><small
          v-if="
            ops.chatResult.business_state === 'PROCESSING' &&
            !ops.chatHasPending
          "
          >关联计算已返回；服务端本轮业务状态仍为
          PROCESSING，可主动继续或查看结果。</small
        ><small
          v-if="
            ops.chatResult.business_state === 'NEEDS_INFO' &&
            ops.chatResult.next_action !== 'ASK_USER'
          "
          >请按下一步操作处理，并非一律需要继续填写字段。</small
        ><button
          v-if="ops.chatContinueLabel"
          class="secondary full"
          :disabled="ops.busy"
          @click="ops.continueChat"
        >
          {{ ops.chatContinueLabel }}
        </button>
        <details v-if="ops.chatResult.evidence_refs.length">
          <summary>业务证据引用</summary>
          <ul>
            <li v-for="ref in ops.chatResult.evidence_refs" :key="ref">
              {{ ref }}
            </li>
          </ul>
        </details>
      </section>
      <section
        v-if="
          ops.chatResult?.incident_id &&
          ops.workspace?.incident.incident_id === ops.chatResult.incident_id
        "
        class="chat-confirmed-facts"
        aria-label="已确认异常信息"
      >
        <span class="eyebrow">业务系统已保存</span
        ><b
          >{{ ops.workspace.incident.incident_id }} / V{{
            ops.workspace.incident.incident_version
          }}</b
        >
        <p>
          {{ label(ops.workspace.incident.event_type) }} ·
          {{ ops.workspace.incident.vehicle_id }} ·
          {{
            ops.workspace.incident.delay_minutes == null
              ? "延误待确认"
              : ops.workspace.incident.delay_minutes + " 分钟"
          }}
        </p>
        <small
          >{{ label(ops.workspace.incident.status) }} ·
          {{ ops.workspace.incident.description }}</small
        >
      </section>
      <RouterLink
        v-if="
          ops.chatResult?.plan_ids.length &&
          ops.workspace?.plans.length &&
          !ops.isDriver
        "
        to="/plans"
        class="text-link"
        @click="ops.assistantOpen = false"
        >查看恢复候选 <ArrowUpRight :size="16"
      /></RouterLink>
      <RouterLink
        v-if="
          ops.chatResult &&
          ops.isDriver &&
          ['WAITING_ACK', 'ACTIVE'].includes(ops.chatResult.business_state)
        "
        to="/driver"
        class="text-link"
        @click="ops.assistantOpen = false"
        >查看任务调整与执行反馈 <ArrowUpRight :size="16"
      /></RouterLink>
      <RouterLink
        v-if="
          ops.chatResult &&
          !ops.isDriver &&
          ['VERIFY_WINDOW', 'MANUAL_REVIEW'].includes(
            ops.chatResult.next_action,
          )
        "
        to="/incidents"
        class="text-link"
        @click="ops.assistantOpen = false"
        >前往异常工作台核实 <ArrowUpRight :size="16"
      /></RouterLink>
      <button
        v-if="
          ops.chatResult &&
          !ops.chatContinueLabel &&
          ![
            'ASK_USER',
            'POLL',
            'WAIT_DISPATCHER',
            'WAIT_DRIVER',
            'NONE',
          ].includes(ops.chatResult.next_action)
        "
        class="quiet full"
        :disabled="ops.busy"
        @click="ops.guard(ops.refresh)"
      >
        刷新业务上下文
      </button>
    </div>
    <form class="chat-compose" @submit.prevent="send">
      <div class="chat-context-controls">
        <label
          >关联订单<select
            :value="ops.chatOrderNo"
            :disabled="ops.busy"
            @change="
              history.cancel();
              ops.setChatContext(($event.target as HTMLSelectElement).value);
            "
            :required="!ops.isDriver && !ops.chatIncidentId"
          >
            <option value="">请选择订单</option>
            <option v-for="o in ops.context?.orders" :key="o.order_no">
              {{ o.order_no }}
            </option>
          </select></label
        ><button
          v-if="ops.workspace"
          type="button"
          class="quiet"
          :disabled="ops.busy"
          @click="
            history.cancel();
            ops.setChatContext('', ops.workspace!.incident.incident_id);
          "
        >
          关联当前异常
        </button>
      </div>
      <textarea
        v-model="message"
        aria-label="发送给智能助手"
        placeholder="描述异常，或询问下一步…"
        @keydown.ctrl.enter.prevent="send"
        @keydown.meta.enter.prevent="send"
        maxlength="4000"
        required
      />
      <footer>
        <small>{{
          ops.isDriver ? "请在安全停车后操作" : "Ctrl+Enter 发送"
        }}</small
        ><button
          class="send-button"
          :disabled="
            ops.busy ||
            ops.chatHasPending ||
            ops.chatHistoryIncomplete ||
            history.loading ||
            !message.trim()
          "
          aria-label="发送消息"
        >
          <ArrowUp :size="16" /><span>发送</span>
        </button>
      </footer>
    </form>
  </aside>
</template>
