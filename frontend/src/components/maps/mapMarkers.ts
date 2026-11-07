import type { DeliveryMarker } from "../../services/maps/mapDataAdapter";
const warehouseIcon =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 10 9-7 9 7v11H3Z M9 21V12h6v9"/></svg>';
const vehicleIcon =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h11v12H3z M14 9h4l3 4v4h-7 M7 17v3 M18 17v3"/></svg>';
export function markerContent(
  point: DeliveryMarker,
  selected: boolean,
  next: boolean,
  risk: boolean,
  changed: boolean,
) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = [
    "amap-delivery-marker",
    point.kind,
    selected && "selected",
    next && "next",
    risk && "risk",
    changed && "changed",
    point.status === "COMPLETED" && "completed",
  ]
    .filter(Boolean)
    .join(" ");
  element.setAttribute(
    "aria-label",
    point.kind === "stop"
      ? "选择配送点 " +
          (point.orderNo || point.nodeId || "") +
          " " +
          point.label
      : point.label,
  );
  element.setAttribute("aria-pressed", String(selected));
  if (point.nodeId) element.dataset.node = point.nodeId;
  const badge = document.createElement("span");
  badge.className = "amap-marker-badge";
  if (point.kind === "stop")
    badge.textContent = String(point.sequence).padStart(2, "0");
  else badge.innerHTML = point.kind === "vehicle" ? vehicleIcon : warehouseIcon;
  element.append(badge);
  const name = document.createElement("span");
  name.className = "amap-marker-name";
  name.textContent = point.label;
  element.append(name);
  if (risk) {
    const riskTag = document.createElement("span");
    riskTag.className = "amap-risk-tag";
    riskTag.textContent = "!";
    element.append(riskTag);
  }
  return element;
}
export function infoContent(point: DeliveryMarker, preview: boolean) {
  const box = document.createElement("div");
  box.className = "amap-delivery-info";
  const title = document.createElement("b");
  title.textContent = point.label;
  box.append(title);
  const text = document.createElement("p");
  text.textContent = point.orderNo
    ? `${point.orderNo} · 停靠 ${point.sequence} · ${preview ? "方案预览，未确认生效" : "当前配送安排"}`
    : point.observedAt
      ? "最新确认位置 · " + point.observedAt
      : point.kind === "warehouse"
        ? "模拟仓库 / 起点"
        : "模拟配送点 · " + (point.nodeId || "");
  box.append(text);
  return box;
}
