import type { LngLat } from "../../services/maps/amapSdk";
// Simulated warehouses for the city overview only. Not business data.
export type Depot = { id: string; name: string; coordinates: LngLat; main?: boolean };
export const simulatedDepots: Depot[] = [
  { id: "W", name: "中心仓 · 汉口", coordinates: [114.3, 30.59], main: true },
  { id: "D1", name: "汉口北分拨仓", coordinates: [114.262, 30.705] },
  { id: "D2", name: "东西湖冷链仓", coordinates: [114.142, 30.622] },
  { id: "D3", name: "沌口前置仓", coordinates: [114.168, 30.492] },
  { id: "D4", name: "光谷前置仓", coordinates: [114.405, 30.505] },
  { id: "D5", name: "青山分拨仓", coordinates: [114.392, 30.638] },
];
export function depotContent(d: Depot) {
  return `<div class="amap-depot${d.main ? " main" : ""}">
    <span class="depot-ring"></span><span class="depot-ring r2"></span>
    <span class="depot-beam"></span>
    <span class="depot-prism"><i></i><i></i></span>
    <span class="depot-core"></span>
    <b class="depot-label">${d.name}</b>
  </div>`;
}

// Simulated delivery loops: each depot -> a few customer stops -> back to depot.
// Snapped onto real roads at runtime; purely decorative traffic for the overview.
export type DepotRoute = { depot: string; stops: LngLat[] };
export const depotRoutes: DepotRoute[] = [
  { depot: "D1", stops: [[114.235, 30.69], [114.255, 30.66], [114.29, 30.675]] },
  { depot: "D1", stops: [[114.3, 30.72], [114.33, 30.695]] },
  { depot: "D2", stops: [[114.17, 30.645], [114.2, 30.62], [114.18, 30.6]] },
  { depot: "D2", stops: [[114.12, 30.6], [114.11, 30.64]] },
  { depot: "D3", stops: [[114.2, 30.51], [114.23, 30.535], [114.2, 30.55]] },
  { depot: "D3", stops: [[114.14, 30.47], [114.18, 30.46]] },
  { depot: "D4", stops: [[114.37, 30.52], [114.35, 30.545], [114.39, 30.55]] },
  { depot: "D4", stops: [[114.44, 30.49], [114.43, 30.52]] },
  { depot: "D5", stops: [[114.37, 30.62], [114.35, 30.6], [114.38, 30.6]] },
  { depot: "D5", stops: [[114.42, 30.65], [114.41, 30.62]] },
  { depot: "W", stops: [[114.32, 30.57], [114.34, 30.555], [114.31, 30.55]] },
  { depot: "W", stops: [[114.27, 30.6], [114.25, 30.585]] },
];
