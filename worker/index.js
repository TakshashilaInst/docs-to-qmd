/**
 * Takshashila Converter — Cloudflare Worker proxy
 *
 * Holds the GitHub PAT as a secret so the static frontend needs no token.
 *
 * Deploy:
 *   cd worker
 *   npx wrangler deploy
 *   npx wrangler secret put GH_TOKEN   ← paste the PAT when prompted
 *
 * The PAT needs only the "workflow" scope on this repo.
 *
 * Endpoints (all CORS-open so the GitHub Pages frontend can call them):
 *   POST /dispatch           — trigger the convert workflow
 *   GET  /find-run?after=MS  — find the run ID created after a timestamp
 *   GET  /run-status?run_id= — check if a run succeeded/failed
 *   HEAD /output-ready?token=— check if the output ZIP exists on gh-pages
 *   GET  /download?token=    — stream the output ZIP to the browser
 */

// ── Update these if the repo is ever renamed ────────────────────────────────
const REPO     = "TakshashilaInst/docs-to-qmd";
const WORKFLOW = "convert.yml";
const BRANCH   = "gh-pages";

const CORS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function ghHeaders(token) {
  return {
    Authorization:          `Bearer ${token}`,
    Accept:                 "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent":           "tsh-converter-proxy/1.0",
  };
}

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }

    const url   = new URL(request.url);
    const token = env.GH_TOKEN;

    // ── POST /dispatch ────────────────────────────────────────────────────
    if (request.method === "POST" && url.pathname === "/dispatch") {
      const resp = await fetch(
        `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
        {
          method:  "POST",
          headers: { ...ghHeaders(token), "Content-Type": "application/json" },
          body:    await request.text(),
        }
      );
      const body = resp.ok ? null : await resp.text();
      return new Response(body, { status: resp.status, headers: CORS });
    }

    // ── GET /find-run?after=EPOCH_MS ──────────────────────────────────────
    // Returns { id: NUMBER } for the first run created at or after `after`,
    // or {} if none found yet.
    if (request.method === "GET" && url.pathname === "/find-run") {
      const after = parseInt(url.searchParams.get("after") ?? "0");
      const resp  = await fetch(
        `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/runs` +
        `?event=workflow_dispatch&per_page=10`,
        { headers: ghHeaders(token) }
      );
      if (!resp.ok) return new Response(null, { status: resp.status, headers: CORS });

      const { workflow_runs: runs = [] } = await resp.json();
      const match = runs.find(r => new Date(r.created_at).getTime() >= after - 10_000);
      return Response.json(match ? { id: match.id } : {}, { headers: CORS });
    }

    // ── GET /run-status?run_id=ID ─────────────────────────────────────────
    // Returns { status: "queued"|"in_progress"|"completed",
    //           conclusion: "success"|"failure"|"cancelled"|null }
    if (request.method === "GET" && url.pathname === "/run-status") {
      const runId = url.searchParams.get("run_id");
      if (!runId) return new Response("missing run_id", { status: 400, headers: CORS });

      const resp = await fetch(
        `https://api.github.com/repos/${REPO}/actions/runs/${runId}`,
        { headers: ghHeaders(token) }
      );
      if (!resp.ok) return new Response(null, { status: resp.status, headers: CORS });

      const { status, conclusion } = await resp.json();
      return Response.json({ status, conclusion }, { headers: CORS });
    }

    // ── HEAD /output-ready?token=RUN_TOKEN ────────────────────────────────
    // 200 = ZIP exists, 404 = not yet. Used by frontend to poll for output.
    if (request.method === "HEAD" && url.pathname === "/output-ready") {
      const runToken = url.searchParams.get("token");
      if (!runToken) return new Response(null, { status: 400, headers: CORS });

      const resp = await fetch(
        `https://raw.githubusercontent.com/${REPO}/${BRANCH}/outputs/${runToken}/document.zip`,
        { method: "HEAD", headers: ghHeaders(token) }
      );
      return new Response(null, { status: resp.status, headers: CORS });
    }

    // ── GET /download?token=RUN_TOKEN ─────────────────────────────────────
    // Streams the output ZIP to the browser.
    if (request.method === "GET" && url.pathname === "/download") {
      const runToken = url.searchParams.get("token");
      if (!runToken) return new Response("missing token", { status: 400, headers: CORS });

      const resp = await fetch(
        `https://raw.githubusercontent.com/${REPO}/${BRANCH}/outputs/${runToken}/document.zip`,
        { headers: ghHeaders(token) }
      );
      if (!resp.ok) return new Response(null, { status: resp.status, headers: CORS });

      return new Response(resp.body, {
        headers: {
          ...CORS,
          "Content-Type":        "application/zip",
          "Content-Disposition": 'attachment; filename="document.zip"',
        },
      });
    }

    return new Response("Not found", { status: 404, headers: CORS });
  },
};
