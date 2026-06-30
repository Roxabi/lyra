import type { Connect, Plugin } from "vite";
import {
  mintStreamToken,
  createMockAdminUser,
  mockAdminAccess,
  patchMockAdminUser,
  mockAgentConfig,
  mockAgentSoul,
  mockAgentStatus,
  mockAgentsConfigList,
  mockAgentsList,
  mockFleet,
  mockJobs,
  mockOpsHealth,
  mockOpsLogs,
  mockSessions,
  mockSoulPreview,
  takeChatText,
  upsertMockAgentConfig,
  verifyStreamToken,
} from "./fixtures";

function readJsonBody(req: Connect.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw.trim()) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res: Connect.ServerResponse, status: number, body: unknown) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

function sendSseChatStream(res: Connect.ServerResponse, userText: string) {
  res.statusCode = 200;
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");

  const reply = `Réponse mock pour : « ${userText.slice(0, 80)} »`;
  const chunks = reply.match(/.{1,12}/g) ?? [reply];

  let i = 0;
  const tick = () => {
    if (i < chunks.length) {
      res.write(`data: ${JSON.stringify({ type: "delta", text: chunks[i] })}\n\n`);
      i += 1;
      setTimeout(tick, 80);
      return;
    }
    res.write(`data: ${JSON.stringify({ type: "done" })}\n\n`);
    res.end();
  };
  tick();
}

async function handleMockApi(
  req: Connect.IncomingMessage,
  res: Connect.ServerResponse,
  next: Connect.NextFunction,
) {
  const url = new URL(req.url ?? "/", "http://localhost");
  if (!url.pathname.startsWith("/api")) {
    next();
    return;
  }

  const method = req.method ?? "GET";
  const path = url.pathname;

  try {
    // GET /api/agents
    if (method === "GET" && path === "/api/agents") {
      sendJson(res, 200, mockAgentsList());
      return;
    }

    // GET /api/bff/agents/status
    if (method === "GET" && path === "/api/bff/agents/status") {
      sendJson(res, 200, mockAgentStatus());
      return;
    }

    // GET /api/bff/sessions
    if (method === "GET" && path === "/api/bff/sessions") {
      const agent = url.searchParams.get("agent") ?? "lyra";
      sendJson(res, 200, mockSessions(agent));
      return;
    }

    // GET /api/bff/jobs
    if (method === "GET" && path === "/api/bff/jobs") {
      sendJson(res, 200, mockJobs());
      return;
    }

    // POST /api/bff/jobs/launch
    if (method === "POST" && path === "/api/bff/jobs/launch") {
      const body = (await readJsonBody(req)) as { agent?: string };
      sendJson(res, 200, {
        accepted: true,
        job_id: "dev-launch-1",
        message: `Mock launch for ${body.agent ?? "agent"}`,
        dispatch_subject: "factory.jobs.omp",
      });
      return;
    }

    // POST /api/bff/jobs/steer
    if (method === "POST" && path === "/api/bff/jobs/steer") {
      const body = (await readJsonBody(req)) as { job_id?: string };
      sendJson(res, 200, {
        accepted: true,
        message: `Mock steer for ${body.job_id ?? "job"}`,
      });
      return;
    }

    // GET /api/bff/ops/health
    if (method === "GET" && path === "/api/bff/ops/health") {
      sendJson(res, 200, mockOpsHealth());
      return;
    }

    // GET /api/bff/ops/logs
    if (method === "GET" && path === "/api/bff/ops/logs") {
      const preset = url.searchParams.get("preset") ?? "hub-errors";
      sendJson(res, 200, mockOpsLogs(preset));
      return;
    }

    // GET /api/bff/fleet
    if (method === "GET" && path === "/api/bff/fleet") {
      sendJson(res, 200, mockFleet());
      return;
    }

    // GET /api/bff/admin/access
    if (method === "GET" && path === "/api/bff/admin/access") {
      sendJson(res, 200, mockAdminAccess());
      return;
    }

    // POST /api/bff/admin/users
    if (method === "POST" && path === "/api/bff/admin/users") {
      const body = (await readJsonBody(req)) as {
        display_name?: string;
        email?: string;
        telegram_uid?: string | null;
        discord_uid?: string | null;
        agents?: string[];
      };
      const result = createMockAdminUser({
        display_name: String(body.display_name ?? ""),
        email: String(body.email ?? ""),
        telegram_uid: body.telegram_uid,
        discord_uid: body.discord_uid,
        agents: body.agents,
      });
      if ("error" in result) {
        sendJson(res, 409, { detail: "email already registered" });
        return;
      }
      sendJson(res, 200, result);
      return;
    }

    const adminUserPatch = path.match(/^\/api\/bff\/admin\/users\/([^/]+)$/);
    if (adminUserPatch && method === "PATCH") {
      const userId = decodeURIComponent(adminUserPatch[1]);
      const body = (await readJsonBody(req)) as {
        display_name?: string;
        email?: string;
        telegram_uid?: string | null;
        discord_uid?: string | null;
        agents?: string[];
      };
      const result = patchMockAdminUser(userId, body);
      if ("error" in result) {
        sendJson(res, result.error === "not_found" ? 404 : 409, {
          detail: result.error,
        });
        return;
      }
      sendJson(res, 200, result);
      return;
    }

    // GET /api/bff/agents (config list)
    if (method === "GET" && path === "/api/bff/agents") {
      sendJson(res, 200, mockAgentsConfigList());
      return;
    }

    // POST /api/bff/agents (create)
    if (method === "POST" && path === "/api/bff/agents") {
      const body = (await readJsonBody(req)) as {
        name?: string;
        backend?: "claude-cli" | "omp-rpc";
        model?: string;
        display_name?: string;
        tagline?: string;
      };
      const name = String(body.name ?? "").trim();
      if (!name || !/^[a-z][a-z0-9-]*$/.test(name)) {
        sendJson(res, 422, { detail: "invalid agent name" });
        return;
      }
      if (mockAgentConfig(name)) {
        sendJson(res, 409, { detail: `agent '${name}' already exists` });
        return;
      }
      const cfg = upsertMockAgentConfig({
        name,
        backend: body.backend ?? "claude-cli",
        model: body.model ?? "sonnet",
        display_name: body.display_name,
        tagline: body.tagline,
      });
      sendJson(res, 200, cfg);
      return;
    }

    // Agent config + soul routes
    const agentSoulPreview = path.match(/^\/api\/bff\/agents\/([^/]+)\/soul\/preview$/);
    if (method === "POST" && agentSoulPreview) {
      const body = (await readJsonBody(req)) as { sections?: Record<string, string> };
      sendJson(res, 200, mockSoulPreview(body.sections ?? {}));
      return;
    }

    const agentSoul = path.match(/^\/api\/bff\/agents\/([^/]+)\/soul$/);
    if (agentSoul) {
      const name = decodeURIComponent(agentSoul[1]);
      if (method === "GET") {
        const soul = mockAgentSoul(name);
        if (!soul) {
          sendJson(res, 404, { detail: `unknown agent: ${name}` });
          return;
        }
        sendJson(res, 200, soul);
        return;
      }
      if (method === "PUT") {
        sendJson(res, 200, { ok: true });
        return;
      }
    }

    const agentConfig = path.match(/^\/api\/bff\/agents\/([^/]+)$/);
    if (agentConfig) {
      const name = decodeURIComponent(agentConfig[1]);
      const cfg = mockAgentConfig(name);
      if (!cfg) {
        sendJson(res, 404, { detail: `unknown agent: ${name}` });
        return;
      }
      if (method === "GET") {
        sendJson(res, 200, cfg);
        return;
      }
      if (method === "PATCH") {
        const body = (await readJsonBody(req)) as Record<string, unknown>;
        sendJson(res, 200, { ...cfg, ...body, updated_at: new Date().toISOString() });
        return;
      }
    }

    // POST /api/bff/sessions/resume
    if (method === "POST" && path === "/api/bff/sessions/resume") {
      sendJson(res, 200, { accepted: true, message: "Mock resume" });
      return;
    }

    // GET /api/bff/sessions/turns
    if (method === "GET" && path === "/api/bff/sessions/turns") {
      sendJson(res, 200, {
        turns: [
          {
            role: "user",
            content: "Bonjour depuis le mock dev",
            timestamp: "2026-06-28T12:00:00+00:00",
          },
          {
            role: "assistant",
            content: "Réponse assistant (mock) pour reprise de session.",
            timestamp: "2026-06-28T12:00:01+00:00",
          },
        ],
      });
      return;
    }

    // POST /api/chat
    if (method === "POST" && path === "/api/chat") {
      const body = (await readJsonBody(req)) as {
        text?: string;
        session_id?: string | null;
      };
      const sessionId = body.session_id ?? `dev-${Date.now().toString(36)}`;
      const streamToken = mintStreamToken(sessionId, body.text ?? "");
      sendJson(res, 200, { session_id: sessionId, stream_token: streamToken });
      return;
    }

    // GET /api/stream/:sessionId
    const streamMatch = path.match(/^\/api\/stream\/([^/]+)$/);
    if (method === "GET" && streamMatch) {
      const sessionId = decodeURIComponent(streamMatch[1]);
      const token = url.searchParams.get("token");
      if (!verifyStreamToken(sessionId, token)) {
        sendJson(res, 403, { detail: "invalid stream token" });
        return;
      }
      sendSseChatStream(res, takeChatText(sessionId));
      return;
    }

    sendJson(res, 404, { detail: `mock: no handler for ${method} ${path}` });
  } catch {
    sendJson(res, 500, { detail: "mock handler error" });
  }
}

/** Vite plugin — intercepts /api in dev when DASHBOARD_MOCK is enabled (default). */
export function dashboardDevMockPlugin(): Plugin {
  const enabled = process.env.DASHBOARD_MOCK !== "0";

  return {
    name: "dashboard-dev-mock",
    configureServer(server) {
      if (!enabled) return;
      server.middlewares.use((req, res, next) => {
        void handleMockApi(req, res, next);
      });
      server.config.logger.info("[dashboard] API mock enabled (DASHBOARD_MOCK=0 for live hub)");
    },
  };
}
