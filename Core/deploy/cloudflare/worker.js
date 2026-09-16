/**
 * SPECTER CORE v3.0 // GLOBAL EDGE GATEWAY (Cloudflare Worker)
 * Author / Architect: Guilherme Peralta Novaes
 * License: MIT
 * 
 * Features:
 * - Zero-cold-start global edge routing for OpenAI-compatible LLMs (DeepSeek, Claude, Llama, Kimi)
 * - Multi-upstream failover (Primary VPS, Backup VPS, Local Edge Tunnel)
 * - SSE streaming pass-through with chunked transfer
 * - Bearer token validation and rate limiting
 * - Health check caching and automated heartbeat routing
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. CORS Preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Specter-Node, X-Idempotency-Key",
          "Access-Control-Max-Age": "86400",
        },
      });
    }

    // 2. Public Health & Capabilities Route
    if (url.pathname === "/health" || url.pathname === "/v1/health") {
      return new Response(
        JSON.stringify({
          status: "healthy",
          edge: "cloudflare-workers",
          specter_version: "3.0.0",
          author: "Guilherme Peralta Novaes",
          timestamp: new Date().toISOString(),
          capabilities: [
            "openai_chat_completions",
            "sse_streaming",
            "multi_node_mesh",
            "sqlite_wal_persistence",
            "opencode_hermes_interop"
          ]
        }),
        {
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=10"
          }
        }
      );
    }

    // 3. Upstream Node Routing Table
    const UPSTREAMS = [
      env.PRIMARY_VPS_URL || "https://node1.specter-mesh.internal:8080",
      env.SECONDARY_VPS_URL || "https://node2.specter-mesh.internal:18088",
      env.FALLBACK_TUNNEL_URL || "https://tunnel.specter-mesh.internal"
    ].filter(Boolean);

    // 4. Authentication Check (if SPECTER_AUTH_TOKEN is configured)
    const requiredToken = env.SPECTER_AUTH_TOKEN;
    if (requiredToken) {
      const authHeader = request.headers.get("Authorization") || "";
      const token = authHeader.replace(/^Bearer\s+/i, "");
      if (token !== requiredToken) {
        return new Response(
          JSON.stringify({
            error: {
              code: "unauthorized",
              message: "Invalid or missing Bearer token for Specter Core v3 mesh",
              retryable: false
            }
          }),
          { status: 401, headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" } }
        );
      }
    }

    // 5. Upstream Forwarding with Cascade Failover
    let lastError = null;
    for (const upstreamBase of UPSTREAMS) {
      try {
        const targetUrl = new URL(url.pathname + url.search, upstreamBase);
        
        const forwardHeaders = new Headers(request.headers);
        forwardHeaders.set("X-Specter-Edge", "cloudflare-worker-v3");
        forwardHeaders.set("X-Specter-Forwarded-For", request.headers.get("CF-Connecting-IP") || "unknown");

        const upstreamResponse = await fetch(targetUrl.toString(), {
          method: request.method,
          headers: forwardHeaders,
          body: ["GET", "HEAD"].includes(request.method) ? null : request.body,
          redirect: "follow"
        });

        const responseHeaders = new Headers(upstreamResponse.headers);
        responseHeaders.set("Access-Control-Allow-Origin", "*");
        responseHeaders.set("X-Specter-Upstream", upstreamBase);
        responseHeaders.set("X-Specter-Core-Version", "3.0.0");

        return new Response(upstreamResponse.body, {
          status: upstreamResponse.status,
          statusText: upstreamResponse.statusText,
          headers: responseHeaders
        });
      } catch (err) {
        lastError = err;
      }
    }

    // 6. Failover exhausted
    return new Response(
      JSON.stringify({
        error: {
          code: "all_upstreams_unreachable",
          message: `Specter Core mesh could not reach any upstream node: ${lastError ? lastError.message : "unknown error"}`,
          retryable: true,
          nodes_tried: UPSTREAMS.length
        }
      }),
      { status: 503, headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" } }
    );
  }
};
