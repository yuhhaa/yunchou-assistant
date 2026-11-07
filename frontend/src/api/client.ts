import type { Envelope } from "./types";
export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = "NETWORK_ERROR",
    public nextAction = "",
    public requestId = "",
    public missingFields: string[] = [],
  ) {
    super(message);
  }
}
let token = "";
export function setToken(value: string) {
  token = value;
}
export async function request<T>(
  path: string,
  body?: unknown,
  key?: string,
  externalSignal?: AbortSignal,
  method?: "GET" | "POST" | "PUT",
): Promise<T> {
  const controller = new AbortController();
  const requestToken = token;
  const timeout = setTimeout(() => controller.abort(), 18000);
  try {
    const headers: Record<string, string> = {
      "X-Contract-Version": "1.0",
      "X-Request-ID": crypto.randomUUID(),
    };
    if (requestToken) headers.Authorization = `Bearer ${requestToken}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (key) headers["Idempotency-Key"] = key;
    const response = await fetch("/api/v1" + path, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: externalSignal
        ? AbortSignal.any([externalSignal, controller.signal])
        : controller.signal,
    });
    let envelope: Envelope<T>;
    try {
      envelope = await response.json();
    } catch {
      throw new ApiError(
        "服务返回了无法识别的响应，请检查后端连接。",
        response.status,
      );
    }
    if (!response.ok || envelope.error || envelope.data === null) {
      const e = envelope.error;
      if (response.status === 401 && requestToken === token)
        window.dispatchEvent(new Event("auth-expired"));
      throw new ApiError(
        e?.reason || envelope.message || `请求失败 (${response.status})`,
        response.status,
        e?.code || envelope.code,
        e?.next_action,
        envelope.request_id,
        e?.missing_fields,
      );
    }
    return envelope.data;
  } catch (e) {
    if (externalSignal?.aborted)
      throw new DOMException("查询已取消", "AbortError");
    if (e instanceof ApiError) throw e;
    throw new ApiError(
      controller.signal.aborted
        ? "请求超时。写入结果尚不确定，请先查询原操作。"
        : "无法连接业务服务，请检查后端及同源代理。",
    );
  } finally {
    clearTimeout(timeout);
  }
}
export const id = encodeURIComponent;
