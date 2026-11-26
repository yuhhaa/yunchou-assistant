import type { Plugin } from "vite";
import type { IncomingMessage, ServerResponse } from "node:http";
import https from "node:https";

const prefix = "/_AMapService";
// Fixed upstream hosts and API families; never accept an arbitrary URL from the browser.
export function amapTarget(path: string, secret: string): URL | null {
  if (!path.startsWith(prefix + "/") || path.length > 8192) return null;
  const raw = path.slice(prefix.length);
  if (/%2e|%2f|%5c|\\|\.\./i.test(raw.split("?")[0]!)) return null;
  const incoming = new URL(raw, "https://restapi.amap.com");
  if (!/^\/v[345]\/[a-zA-Z0-9_/-]+$/.test(incoming.pathname)) return null;
  if (
    [...incoming.searchParams.keys()].some(
      (key) => key.toLowerCase() === "jscode",
    )
  )
    return null;
  const upstream = new URL(
    incoming.pathname + incoming.search,
    incoming.pathname === "/v4/map/styles"
      ? "https://webapi.amap.com"
      : "https://restapi.amap.com",
  );
  upstream.searchParams.set("jscode", secret);
  return upstream;
}

function json(res: ServerResponse, status: number, body: object) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(body));
}

export function createAmapMiddleware() {
  return (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    if (!req.url?.startsWith(prefix + "/")) return next();
    if (
      !["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(
        req.socket.remoteAddress || "",
      )
    )
      return json(res, 403, { error: "LOCAL_DEVELOPMENT_ONLY" });
    const secret = process.env.AMAP_SECURITY_JSCODE?.trim() || "";
    const external = process.env.AMAP_PROXY_TARGET?.trim() || "";
    const validSecret = /^[a-f0-9]{32}$/i.test(secret);
    let externalOrigin: URL | undefined;
    try {
      const url = new URL(external);
      if (
        url.protocol === "https:" &&
        !url.username &&
        !url.password &&
        url.pathname === "/" &&
        !url.search &&
        !url.hash
      )
        externalOrigin = url;
    } catch {
      /* No team proxy configured. */
    }
    if (req.method !== "GET")
      return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
    let referer = "";
    try {
      const origin = req.headers.origin;
      const url = req.headers.referer
        ? new URL(req.headers.referer)
        : undefined;
      if (
        (origin && new URL(origin).host !== req.headers.host) ||
        (url && url.host !== req.headers.host)
      )
        return json(res, 403, { error: "AMAP_SAME_ORIGIN_ONLY" });
      if (url) referer = url.origin + "/";
    } catch {
      return json(res, 403, { error: "AMAP_SAME_ORIGIN_ONLY" });
    }
    if (req.url === prefix + "/_status")
      return json(res, 200, {
        configured: !!externalOrigin || validSecret,
        mode: externalOrigin ? "team-proxy" : "local-development",
      });
    if (!validSecret && !externalOrigin)
      return json(res, 503, { error: "AMAP_AUTH_NOT_CONFIGURED" });
    const target = amapTarget(req.url, secret);
    if (!target) return json(res, 400, { error: "AMAP_PATH_NOT_ALLOWED" });
    if (externalOrigin) {
      target.searchParams.delete("jscode");
      target.host = externalOrigin.host;
      target.pathname = prefix + target.pathname;
    }
    // Never forward browser cookies, JWTs or authorization headers to the map service.
    const upstream = https.get(
      target,
      {
        headers: {
          Accept: "*/*",
          "Accept-Encoding": "identity",
          ...(referer ? { Referer: referer } : {}),
        },
        timeout: 12000,
      },
      (response) => {
        if (
          (response.statusCode || 500) >= 300 &&
          (response.statusCode || 500) < 400
        ) {
          response.resume();
          return json(res, 502, { error: "AMAP_UPSTREAM_REDIRECT" });
        }
        const chunks: Buffer[] = [];
        let bytes = 0;
        response.on("data", (chunk: Buffer) => {
          bytes += chunk.length;
          if (bytes > 8 * 1024 * 1024) upstream.destroy();
          else chunks.push(chunk);
        });
        response.on("end", () => {
          if (res.writableEnded) return;
          const body = Buffer.concat(chunks);
          // Do not pass a reflected server credential back to the browser.
          if (secret && body.includes(Buffer.from(secret)))
            return json(res, 502, { error: "AMAP_UNSAFE_UPSTREAM_RESPONSE" });
          res.writeHead(response.statusCode || 502, {
            "Content-Type":
              response.headers["content-type"] || "application/octet-stream",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
          });
          res.end(body);
        });
        response.on("error", () => {
          if (!res.writableEnded)
            json(res, 502, { error: "AMAP_UPSTREAM_UNAVAILABLE" });
        });
      },
    );
    upstream.on("timeout", () => upstream.destroy());
    upstream.on("error", () => {
      if (!res.writableEnded)
        json(res, 502, { error: "AMAP_UPSTREAM_UNAVAILABLE" });
    });
    req.on("aborted", () => upstream.destroy());
  };
}
const roadPrefix = "/_AMapRoad/driving";
// Server-side road snapping with the Web Service key; the key never reaches the browser.
export function createRoadMiddleware() {
  return (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    if (!req.url || req.url.split("?", 1)[0] !== roadPrefix) return next();
    if (
      !["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(
        req.socket.remoteAddress || "",
      )
    )
      return json(res, 403, { error: "LOCAL_DEVELOPMENT_ONLY" });
    if (req.method !== "GET")
      return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
    try {
      if (
        [req.headers.origin, req.headers.referer].some(
          (value) => value && new URL(value).host !== req.headers.host,
        ) ||
        req.headers["sec-fetch-site"] === "cross-site"
      )
        return json(res, 403, { error: "AMAP_SAME_ORIGIN_ONLY" });
    } catch {
      return json(res, 403, { error: "AMAP_SAME_ORIGIN_ONLY" });
    }
    if (req.url.length > 2048)
      return json(res, 414, { error: "AMAP_REQUEST_TOO_LONG" });
    const key = process.env.AMAP_WEB_SERVICE_KEY?.trim() || "";
    if (!/^[a-f0-9]{32}$/i.test(key))
      return json(res, 503, { error: "AMAP_WEB_SERVICE_KEY_MISSING" });
    const incoming = new URL(req.url, "http://local");
    function isCoordinate(value: string) {
      if (!/^-?\d{1,3}\.\d{1,8},-?\d{1,2}\.\d{1,8}$/.test(value)) return false;
      const [longitude, latitude] = value.split(",").map(Number);
      return Math.abs(longitude!) <= 180 && Math.abs(latitude!) <= 90;
    }
    const origin = incoming.searchParams.get("origin") || "";
    const destination = incoming.searchParams.get("destination") || "";
    const waypoints = incoming.searchParams.get("waypoints") || "";
    if (
      !isCoordinate(origin) ||
      !isCoordinate(destination) ||
      (waypoints && !waypoints.split(";").every(isCoordinate)) ||
      waypoints.split(";").length > 16
    )
      return json(res, 400, { error: "BAD_COORDINATES" });
    const target = new URL("https://restapi.amap.com/v3/direction/driving");
    target.searchParams.set("key", key);
    target.searchParams.set("origin", origin);
    target.searchParams.set("destination", destination);
    if (waypoints) target.searchParams.set("waypoints", waypoints);
    target.searchParams.set("strategy", "0");
    target.searchParams.set("extensions", "base");
    const fail = () => {
      if (!res.destroyed && !res.writableEnded)
        json(res, 502, { error: "AMAP_UPSTREAM_UNAVAILABLE" });
    };
    const upstream = https.get(target, { timeout: 12000 }, (response) => {
      if (response.statusCode !== 200) {
        response.resume();
        return fail();
      }
      const chunks: Buffer[] = [];
      let size = 0;
      response.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > 2 * 1024 * 1024) upstream.destroy();
        else chunks.push(chunk);
      });
      response.on("error", fail);
      response.on("end", () => {
        if (res.destroyed || res.writableEnded) return;
        const body = Buffer.concat(chunks).toString("utf8");
        if (body.includes(key))
          return json(res, 502, { error: "AMAP_UNSAFE_UPSTREAM_RESPONSE" });
        res.writeHead(200, {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        });
        res.end(body);
      });
    });
    upstream.on("error", fail);
    upstream.on("timeout", () => upstream.destroy());
    res.on("close", () => {
      if (!res.writableEnded) upstream.destroy();
    });
  };
}

export function amapDevelopmentProxy(): Plugin {
  return {
    name: "local-amap-security-proxy",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(createRoadMiddleware());
      server.middlewares.use(createAmapMiddleware());
    },
  };
}
