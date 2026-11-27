import type { LngLat } from "./amapSdk";
import { validCoordinate } from "./mapDataAdapter";

// The road projection is a preview for simulated delivery sequences.
const cache = new Map<string, Promise<LngLat[] | null>>();
const format = ([lng, lat]: LngLat) => `${lng.toFixed(6)},${lat.toFixed(6)}`;

// AMap keys allow only a few requests per second: queue them, retry on QPS limits.
let queue: Promise<unknown> = Promise.resolve();
function throttled<T>(task: () => Promise<T>): Promise<T> {
  const run = queue.then(task, task);
  const gap = () => new Promise((r) => setTimeout(r, 380));
  queue = run.then(gap, gap);
  return run;
}
async function roadLeg(points: LngLat[]): Promise<LngLat[] | null> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const result = await throttled(() => roadLegOnce(points));
    if (result !== "RETRY") return result;
    await new Promise((r) => setTimeout(r, 1200 * (attempt + 1)));
  }
  return null;
}
async function roadLegOnce(points: LngLat[]): Promise<LngLat[] | null | "RETRY"> {
  const params = new URLSearchParams({
    origin: format(points[0]!),
    destination: format(points[points.length - 1]!),
  });
  if (points.length > 2)
    params.set("waypoints", points.slice(1, -1).map(format).join(";"));
  try {
    const response = await fetch(`/_AMapRoad/driving?${params}`, {
      credentials: "same-origin",
      signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) return null;
    const data = await response.json();
    if (data.status !== "1") {
      if (/QPS|LIMIT|OVER/i.test(String(data.info || ""))) return "RETRY";
      console.warn("[道路贴合] 高德驾车规划失败:", data.info, data.infocode || "");
      return null;
    }
    const points: LngLat[] = [];
    for (const step of data.route?.paths?.[0]?.steps || []) {
      for (const pair of String(step.polyline || "").split(";")) {
        const fields = pair.split(",");
        if (fields.length !== 2 || fields.some((field) => !field.trim()))
          return null;
        const point = fields.map(Number);
        if (!validCoordinate(point)) return null;
        points.push(point);
      }
    }
    return points.length > 1 ? points : null;
  } catch {
    return null;
  }
}

async function fetchRoute(points: LngLat[]): Promise<LngLat[] | null> {
  const coordinates: LngLat[] = [];
  // At most 16 waypoints per request. Chunks share one stop.
  for (let i = 0; i < points.length - 1; i += 17) {
    const result = await roadLeg(points.slice(i, i + 18));
    if (!result) return null;
    const previous = coordinates.at(-1);
    const sameEndpoint =
      previous?.[0] === result[0]![0] && previous?.[1] === result[0]![1];
    coordinates.push(...(sameEndpoint ? result.slice(1) : result));
  }
  return coordinates;
}

export function snapToRoads(coords: LngLat[]): Promise<LngLat[] | null> {
  const clean = coords.filter(
    (c, i) => !i || c[0] !== coords[i - 1]![0] || c[1] !== coords[i - 1]![1],
  );
  if (clean.length < 2) return Promise.resolve(null);
  const key = clean.map(format).join(";");
  const existing = cache.get(key);
  if (existing) return existing;
  if (cache.size >= 64) cache.delete(cache.keys().next().value!);
  const pending = fetchRoute(clean).then((result) => {
    if (!result && cache.get(key) === pending) cache.delete(key);
    return result;
  });
  cache.set(key, pending);
  return pending;
}
