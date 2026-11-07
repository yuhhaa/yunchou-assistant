import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { useOps } from "./ops";
import { getConversations, getConversationMessages } from "../api/extended";
import type { Schema } from "../api/types";
export const useConversations = defineStore("conversations", () => {
  const ops = useOps(),
    items = ref<Schema["ConversationOutput"][]>([]),
    turns = ref<Schema["ConversationTurnOutput"][]>([]),
    nextOffset = ref<number | null>(null),
    messagesNextOffset = ref<number | null>(null),
    loading = ref(false),
    listLoading = ref(false),
    error = ref(""),
    selected = ref<Schema["ConversationOutput"]>();
  let listController: AbortController | undefined,
    messageController: AbortController | undefined,
    listRun = 0,
    messageRun = 0;
  const read = <T>(path: string, signal?: AbortSignal) =>
    ops.read<T>(path, signal);
  function cancel() {
    listController?.abort();
    messageController?.abort();
    listRun++;
    messageRun++;
    loading.value = false;
    listLoading.value = false;
  }
  async function loadList(more = false) {
    if (more && nextOffset.value === null) return;
    listController?.abort();
    const run = ++listRun,
      controller = (listController = new AbortController());
    listLoading.value = true;
    error.value = "";
    try {
      const data = await getConversations(
        more ? nextOffset.value! : 0,
        controller.signal,
        read,
      );
      if (run !== listRun || controller.signal.aborted) return;
      items.value = more
        ? [
            ...items.value,
            ...data.items.filter(
              (i) =>
                !items.value.some(
                  (o) => o.conversation_id === i.conversation_id,
                ),
            ),
          ]
        : data.items;
      nextOffset.value = data.next_offset;
    } catch (e) {
      if (!controller.signal.aborted && run === listRun)
        error.value = e instanceof Error ? e.message : "会话列表加载失败";
    } finally {
      if (run === listRun) listLoading.value = false;
    }
  }
  async function restore(meta: Schema["ConversationOutput"], more = false) {
    if (
      (ops.busy && !ops.polling) ||
      (more && messagesNextOffset.value === null)
    )
      return;
    ops.cancelPolling();
    messageController?.abort();
    const run = ++messageRun,
      controller = (messageController = new AbortController());
    loading.value = true;
    error.value = "";
    try {
      const data = await getConversationMessages(
        meta.conversation_id,
        more ? messagesNextOffset.value! : 0,
        controller.signal,
        read,
      );
      if (run !== messageRun || controller.signal.aborted) return;
      if (data.conversation_id !== meta.conversation_id)
        throw Error("会话历史归属不一致");
      turns.value = more
        ? [
            ...turns.value,
            ...data.items.filter(
              (t) => !turns.value.some((o) => o.turn_id === t.turn_id),
            ),
          ]
        : data.items;
      messagesNextOffset.value = data.next_offset;
      selected.value = meta;
      ops.restoreChatHistory(meta, turns.value, more);
    } catch (e) {
      if (!controller.signal.aborted && run === messageRun)
        error.value = e instanceof Error ? e.message : "会话恢复失败";
    } finally {
      if (run === messageRun) loading.value = false;
    }
  }
  watch(
    () => ops.assistantOpen,
    (open) => {
      if (!open) cancel();
    },
  );
  watch(
    () => [ops.user?.user_id, ops.mode],
    () => {
      cancel();
      items.value = [];
      turns.value = [];
      selected.value = undefined;
      nextOffset.value = null;
      messagesNextOffset.value = null;
      error.value = "";
    },
  );
  window.addEventListener("hashchange", cancel);
  watch(
    () => [ops.chatOrderNo, ops.chatIncidentId, ops.conversationId],
    () => {
      // Context controls must invalidate an in-flight old-history request.
      if (
        loading.value &&
        selected.value?.conversation_id !== ops.conversationId
      )
        cancel();
    },
    { flush: "sync" },
  );
  return {
    items,
    turns,
    nextOffset,
    messagesNextOffset,
    loading,
    listLoading,
    error,
    selected,
    loadList,
    restore,
    cancel,
  };
});
