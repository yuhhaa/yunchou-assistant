export type MapTheme = {
  id: string;
  name: string;
  style: string;
  dark: boolean;
  swatch: string;
  wall: string;
  roof: string;
};
export const mapThemes: MapTheme[] = [
  {
    id: "whitesmoke",
    name: "远山黛",
    style: "amap://styles/whitesmoke",
    dark: false,
    swatch: "#eef0ec",
    wall: "rgba(160, 178, 170, 0.85)",
    roof: "rgba(236, 240, 236, 0.95)",
  },
  {
    id: "normal",
    name: "标准",
    style: "amap://styles/normal",
    dark: false,
    swatch: "#f5f3ea",
    wall: "rgba(190, 186, 170, 0.85)",
    roof: "rgba(250, 248, 240, 0.95)",
  },
  {
    id: "fresh",
    name: "草色青",
    style: "amap://styles/fresh",
    dark: false,
    swatch: "#dcefe2",
    wall: "rgba(140, 190, 165, 0.85)",
    roof: "rgba(225, 245, 232, 0.95)",
  },
  {
    id: "grey",
    name: "雅士灰",
    style: "amap://styles/grey",
    dark: false,
    swatch: "#c9ccce",
    wall: "rgba(130, 136, 140, 0.85)",
    roof: "rgba(205, 208, 210, 0.95)",
  },
  {
    id: "macaron",
    name: "马卡龙",
    style: "amap://styles/macaron",
    dark: false,
    swatch: "#f4e3e8",
    wall: "rgba(205, 170, 190, 0.85)",
    roof: "rgba(250, 236, 242, 0.95)",
  },
  {
    id: "darkblue",
    name: "极夜蓝",
    style: "amap://styles/darkblue",
    dark: true,
    swatch: "#13263d",
    wall: "rgba(52, 96, 140, 0.85)",
    roof: "rgba(120, 180, 230, 0.9)",
  },
];
const KEY = "yunchou.mapTheme";
/** Presentation only: share the map palette with the application chrome. */
export function applyAppTheme(id: string) {
  const selected = mapThemes.find((item) => item.id === id) || mapThemes[0]!;
  document.documentElement.dataset.mapTheme = selected.id;
}
export function savedTheme(): MapTheme {
  const id = (() => {
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null;
    }
  })();
  return mapThemes.find((t) => t.id === id) || mapThemes[0]!;
}
export function saveTheme(id: string) {
  applyAppTheme(id);
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* private mode */
  }
}
