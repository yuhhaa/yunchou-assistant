import { request, id } from "./client";
import type { Schema } from "./types";
export type ReadApi = <T>(path: string, signal?: AbortSignal) => Promise<T>;
const read: ReadApi = (path, signal) =>
  request(path, undefined, undefined, signal);
export function demoMapPath(vehicleId = "", planId = "") {
  if (planId && !vehicleId) throw Error("候选地图必须同时指定车辆与方案。");
  const params = new URLSearchParams();
  if (vehicleId) params.set("vehicle_id", vehicleId);
  if (planId) params.set("plan_id", planId);
  return "/map/demo" + (params.size ? "?" + params : "");
}
export const getDemoMap = (
  vehicleId = "",
  planId = "",
  signal?: AbortSignal,
  api: ReadApi = read,
) => api<Schema["DemoMapOutput"]>(demoMapPath(vehicleId, planId), signal);
export const getMapStatus = (
  vehicleId = "",
  signal?: AbortSignal,
  api: ReadApi = read,
) =>
  api<Schema["MapOutput"]>(
    "/map" + (vehicleId ? "?vehicle_id=" + id(vehicleId) : ""),
    signal,
  );
export const getRoadRoute = (
  vehicleId: string,
  planId = "",
  signal?: AbortSignal,
  api: ReadApi = read,
) =>
  api<Schema["MapRouteOutput"]>(
    "/map/route?vehicle_id=" +
      id(vehicleId) +
      (planId ? "&plan_id=" + id(planId) : ""),
    signal,
  );
export const geocode = (
  address: string,
  city = "",
  signal?: AbortSignal,
  api: ReadApi = read,
) =>
  api<Schema["GeocodeOutput"]>(
    "/map/geocode?address=" + id(address) + (city ? "&city=" + id(city) : ""),
    signal,
  );
export const getConversations = (
  offset = 0,
  signal?: AbortSignal,
  api: ReadApi = read,
) =>
  api<Schema["ConversationListOutput"]>(
    `/conversations?limit=20&offset=${offset}`,
    signal,
  );
export const getConversationMessages = (
  conversationId: string,
  offset = 0,
  signal?: AbortSignal,
  api: ReadApi = read,
) =>
  api<Schema["ConversationMessagesOutput"]>(
    `/conversations/${id(conversationId)}/messages?limit=20&offset=${offset}`,
    signal,
  );
export const updateMapNode = (
  nodeId: string,
  body: Schema["MapNodeInput"],
  originalKey: string,
  signal?: AbortSignal,
) =>
  request<Schema["MapNodeOutput"]>(
    "/map/nodes/" + id(nodeId),
    body,
    originalKey,
    signal,
    "PUT",
  );
// Calling code must gate permissions/readiness and retain the same key/body for uncertain retries.
export const refreshRoadRouting = (
  body: Schema["MapRefreshInput"],
  originalKey: string,
  signal?: AbortSignal,
) =>
  request<Schema["MapQueuedOutput"]>(
    "/map/routing/refresh",
    body,
    originalKey,
    signal,
  );
