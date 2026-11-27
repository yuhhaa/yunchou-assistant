import type { AmapSDK, AmapInstance, LngLat } from "../../services/maps/amapSdk";
// Canvas comet renderer: every vehicle head + tapered trail is drawn on ONE canvas
// over the map each frame. No AMap overlays are created or mutated per frame,
// so 2D/3D, dragging and zooming stay smooth with full trails.
type Comet = {
  path: LngLat[];
  cum: number[];
  total: number;
  offset: number;
  speed: number;
  trail: number;
  color: string;
  alert: boolean;
  phase: number;
};
type Projector = { lngLatToContainer(p: LngLat): { x?: number; y?: number; getX?(): number; getY?(): number } };
function meters(a: LngLat, b: LngLat) {
  const k = Math.PI / 180;
  const x = (b[0] - a[0]) * Math.cos(((a[1] + b[1]) / 2) * k);
  const y = b[1] - a[1];
  return Math.sqrt(x * x + y * y) * k * 6371000;
}
function rgba(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a.toFixed(3)})`;
}
export function createComets(_sdk: AmapSDK, map: AmapInstance) {
  const list: Comet[] = [];
  const proj = map as unknown as Projector;
  const host = (map as unknown as { getContainer?(): HTMLElement }).getContainer?.();
  const canvas = document.createElement("canvas");
  canvas.className = "amap-comet-canvas";
  Object.assign(canvas.style, { position: "absolute", inset: "0", width: "100%", height: "100%", pointerEvents: "none", zIndex: "120" });
  host?.appendChild(canvas);
  const ctx = canvas.getContext("2d")!;
  let raf = 0, last = 0, paused = false, w = 0, h = 0;
  function at(c: Comet, d: number): LngLat {
    d = ((d % c.total) + c.total) % c.total;
    let lo = 0, hi = c.cum.length - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      if (c.cum[mid]! <= d) lo = mid; else hi = mid;
    }
    const a = c.path[lo]!, b = c.path[hi]!;
    const t = Math.min(1, Math.max(0, (d - c.cum[lo]!) / (c.cum[hi]! - c.cum[lo]! || 1)));
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  }
  // Trail follows the actual road: start/end interpolated, plus every road vertex in between.
  function trailPoints(c: Comet): { p: LngLat; k: number }[] {
    const from = c.offset - c.trail, to = c.offset;
    const out: { p: LngLat; k: number }[] = [{ p: at(c, from), k: 0 }];
    const push = (lo: number, hi: number, shift: number) => {
      for (let i = 0; i < c.cum.length; i++) {
        const d = c.cum[i]! + shift;
        if (d > lo && d < hi) out.push({ p: c.path[i]!, k: (d - from) / c.trail });
      }
    };
    if (from < 0) { push(from, 0, -c.total); push(0, to, 0); } else push(from, to, 0);
    out.push({ p: at(c, to), k: 1 });
    return out;
  }
  function px(p: LngLat): [number, number] {
    const r = proj.lngLatToContainer(p);
    return [r.x ?? r.getX?.() ?? 0, r.y ?? r.getY?.() ?? 0];
  }
  function fit() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cw = host?.clientWidth || 0, ch = host?.clientHeight || 0;
    if (cw !== w || ch !== h) {
      w = cw; h = ch;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }
  function draw(now: number) {
    fit();
    ctx.clearRect(0, 0, w, h);
    ctx.lineCap = "round";
    const t = now / 1000;
    for (const c of list) {
      const raw = trailPoints(c);
      const pts: { x: number; y: number; k: number }[] = [];
      for (const r of raw) {
        const [x, y] = px(r.p);
        const prev = pts[pts.length - 1];
        // drop sub-pixel points so curves stay clean
        if (prev && Math.abs(prev.x - x) + Math.abs(prev.y - y) < 1.5 && r.k < 1) continue;
        pts.push({ x, y, k: r.k });
      }
      if (pts.length < 2) continue;
      const head: [number, number] = [pts[pts.length - 1]!.x, pts[pts.length - 1]!.y];
      if (head[0] < -60 || head[1] < -60 || head[0] > w + 60 || head[1] > h + 60) continue;
      const mid = (i: number) => ({ x: (pts[i]!.x + pts[i + 1]!.x) / 2, y: (pts[i]!.y + pts[i + 1]!.y) / 2 });
      // soft wide glow: one smooth path (quadratic curves through segment midpoints)
      ctx.beginPath(); ctx.moveTo(pts[0]!.x, pts[0]!.y);
      for (let i = 1; i < pts.length - 1; i++) { const m = mid(i); ctx.quadraticCurveTo(pts[i]!.x, pts[i]!.y, m.x, m.y); }
      ctx.lineTo(head[0], head[1]);
      ctx.lineJoin = "round"; ctx.strokeStyle = rgba(c.color, 0.1); ctx.lineWidth = 10; ctx.stroke();
      // tapered gradient trail, drawn piece by piece along the same smooth curve
      let start = { x: pts[0]!.x, y: pts[0]!.y };
      for (let i = 1; i < pts.length; i++) {
        const end = i < pts.length - 1 ? mid(i) : { x: head[0], y: head[1] };
        const k = pts[i]!.k;
        ctx.beginPath(); ctx.moveTo(start.x, start.y);
        if (i < pts.length - 1) ctx.quadraticCurveTo(pts[i]!.x, pts[i]!.y, end.x, end.y);
        else ctx.lineTo(end.x, end.y);
        ctx.strokeStyle = k > 0.85 ? `rgba(255,255,255,${(0.6 + 0.4 * k).toFixed(3)})` : rgba(c.color, 0.08 + 0.92 * k * k);
        ctx.lineWidth = 0.8 + 4.2 * k * k; ctx.stroke();
        start = end;
      }
      // head: halo + hot core (+ alert pulse)
      const breathe = 1 + 0.25 * Math.sin(t * 4 + c.phase);
      const g = ctx.createRadialGradient(head[0], head[1], 0, head[0], head[1], 13 * breathe);
      g.addColorStop(0, rgba(c.color, 0.85)); g.addColorStop(1, rgba(c.color, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(head[0], head[1], 13 * breathe, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(head[0], head[1], 3.2, 0, Math.PI * 2); ctx.fill();
      if (c.alert) {
        const k = (t * 0.9 + c.phase) % 1;
        ctx.strokeStyle = rgba(c.color, 0.7 * (1 - k)); ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(head[0], head[1], 6 + 18 * k, 0, Math.PI * 2); ctx.stroke();
      }
    }
  }
  function frame(now: number) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - (last || now)) / 1000);
    last = now;
    if (!paused) for (const c of list) c.offset = (c.offset + c.speed * dt) % c.total;
    draw(now); // always redraw so the canvas follows drag/zoom/pitch
  }
  return {
    add(road: LngLat[], color: string, alert: boolean, count = 4) {
      if (road.length < 2) return;
      const path = [...road, ...road.slice().reverse().slice(1)];
      const cum = [0];
      for (let i = 1; i < path.length; i++) cum.push(cum[i - 1]! + meters(path[i - 1]!, path[i]!));
      const total = cum[cum.length - 1]!;
      if (total < 50) return;
      for (let n = 0; n < count; n++)
        list.push({
          path, cum, total, color, alert,
          offset: (total * n) / count,
          speed: (total / Math.max(11, total / 800)) * (0.85 + Math.random() * 0.3),
          trail: Math.min(total * 0.12, 2600),
          phase: Math.random() * 6.28,
        });
      if (!raf) raf = requestAnimationFrame(frame);
    },
    pause() { paused = true; },
    resume() { paused = false; },
    setLite(_on: boolean) { /* trails stay on; canvas is cheap */ },
    setBusy(_busy: boolean) { /* keep animating; canvas follows the camera */ },
    stop() {
      cancelAnimationFrame(raf); raf = 0;
      list.length = 0;
      canvas.remove();
    },
  };
}
