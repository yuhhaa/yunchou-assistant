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

export function amapDevelopmentProxy(): Plugin {
  return {
    name: "local-amap-security-proxy",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(createAmapMiddleware());
    },
  };
}
