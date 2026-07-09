/** Dev fixtures — aligned with src/factory/dashboard/e2e.py */

export const MOCK_AGENTS = ["lyra", "aryl"] as const;

const soulSections = {
  Identity: "Je suis Lyra, opératrice de la factory.",
  Personality: "Directe, calme, orientée exécution.",
  Values: "Fiabilité, clarté, sobriété.",
  Expertise: "Orchestration agents, supervision ops.",
  Guidelines: "Réponses courtes. Pas de secrets dans le soul.",
};

const agentConfigs: Record<
  string,
  {
    name: string;
    backend: "claude-cli" | "omp-rpc";
    model: string;
    voice_json: null;
    soul_meta_json: { header: { display_name: string; tagline: string } };
    soul_document_blob_ref: string | null;
    soul_document_bytes: number;
    updated_at: string;
    has_soul: boolean;
  }
> = {
  lyra: {
    name: "lyra",
    backend: "claude-cli",
    model: "sonnet",
    voice_json: null,
    soul_meta_json: { header: { display_name: "Lyra", tagline: "Ops console" } },
    soul_document_blob_ref: "sha256:dev-mock",
    soul_document_bytes: 1200,
    updated_at: "2026-06-29T12:00:00Z",
    has_soul: true,
  },
  aryl: {
    name: "aryl",
    backend: "omp-rpc",
    model: "omp-default",
    voice_json: null,
    soul_meta_json: { header: { display_name: "Aryl", tagline: "Field agent" } },
    soul_document_blob_ref: null,
    soul_document_bytes: 0,
    updated_at: "2026-06-28T09:00:00Z",
    has_soul: false,
  },
};

export const mockAgentsList = () => ({ agents: [...MOCK_AGENTS] });

export const mockAgentStatus = () => ({
  agents: MOCK_AGENTS.map((name) => {
    const cfg = agentConfigs[name];
    return {
      agent: name,
      in_roster: true,
      harness: cfg?.backend ?? "claude-cli",
      harness_reachable: name === "lyra",
      online: name === "lyra",
    };
  }),
});

export const mockSessions = (agent: string) => ({
  sessions: [
    {
      session_id: "dev-sess-1",
      pool_id: `web:smoke:agent:${agent}`,
      platform: "web",
      cli_session_id: "dev-cli-1",
      first_user_msg: "Bonjour depuis le mock dev",
      turn_count: 2,
      last_active_at: "2026-06-28T12:00:00+00:00",
    },
    {
      session_id: "dev-sess-2",
      pool_id: "telegram:main:chat:42",
      platform: "telegram",
      cli_session_id: "dev-cli-2",
      first_user_msg: "Session Telegram (mock)",
      turn_count: 5,
      last_active_at: "2026-06-27T18:00:00+00:00",
    },
  ],
});

export const mockJobs = () => ({
  jobs: [
    {
      job_id: "dev-job-1",
      pool_id: "web:smoke:agent:lyra",
      agent: "lyra",
      platform: "web",
      status: "open",
      started_at: "2026-06-28T12:00:00+00:00",
      concurrency_mode: "steer",
      worker_loc: "clipool-worker",
      steer_subject: "factory.job.dev-job-1.steer",
    },
    {
      job_id: "dev-job-2",
      pool_id: "telegram:main:chat:42",
      agent: "aryl",
      platform: "telegram",
      status: "closing",
      started_at: "2026-06-28T11:30:00+00:00",
      concurrency_mode: "queue",
      worker_loc: null,
      steer_subject: "factory.job.dev-job-2.steer",
    },
  ],
});

export const mockFleet = () => ({
  rows: [
    {
      container_name: "factory-hub",
      host: "roxabituwer",
      component_key: "hub",
      image_ref: "ghcr.io/roxabi/factory:staging-svc",
      image_revision: "dev-mock",
      health: "healthy",
      status: "ok",
      last_report_at: "2026-06-29T12:00:00+00:00",
      age_s: 12,
      systemd_unit: "factory-hub.service",
      instrumented: true,
      source: "live",
    },
    {
      container_name: "factory-clipool",
      host: "roxabituwer",
      component_key: "clipool",
      image_ref: "ghcr.io/roxabi/factory:staging",
      image_revision: null,
      health: "healthy",
      status: "stale",
      last_report_at: null,
      age_s: 120,
      systemd_unit: "factory-clipool.service",
      instrumented: true,
      source: "manifest",
    },
  ],
});

export const mockOpsHealth = () => ({
  engines: [
    { engine: "loki", label: "Loki", reachable: true, detail: "dev mock" },
    { engine: "langfuse", label: "Langfuse", reachable: true, detail: "dev mock" },
    {
      engine: "otel",
      label: "Factory OTel",
      reachable: false,
      detail: "dev mock offline",
    },
  ],
});

export const mockOpsLogs = (preset: string) => ({
  preset,
  query: `dev-mock-${preset}`,
  engine_reachable: true,
  entries: [
    {
      timestamp: "2026-06-28T12:00:00+00:00",
      line: `[mock] log line for ${preset}`,
      labels: { job: "factory-journal", systemd_unit: "factory-hub.service" },
    },
    {
      timestamp: "2026-06-28T11:59:00+00:00",
      line: "[mock] hub heartbeat ok",
      labels: { job: "factory-journal" },
    },
  ],
});

type MockAdminUser = {
  user_id: string;
  display_name: string;
  email: string;
  telegram: {
    platform: string;
    platform_uid: string;
    platform_key: string;
  } | null;
  discord: {
    platform: string;
    platform_uid: string;
    platform_key: string;
  } | null;
  agents: string[];
};

const mockAdminUsers: MockAdminUser[] = [
  {
    user_id: "rx:user:abc123",
    display_name: "Mickael",
    email: "mickael@roxabi.dev",
    telegram: {
      platform: "telegram",
      platform_uid: "7377831990",
      platform_key: "tg:user:7377831990",
    },
    discord: {
      platform: "discord",
      platform_uid: "987654321012345678",
      platform_key: "dc:user:987654321012345678",
    },
    agents: ["lyra", "aryl"],
  },
  {
    user_id: "rx:user:def456",
    display_name: "Guest",
    email: "guest@roxabi.dev",
    telegram: null,
    discord: {
      platform: "discord",
      platform_uid: "111222333444555666",
      platform_key: "dc:user:111222333444555666",
    },
    agents: ["lyra"],
  },
];

export const mockAdminAccess = () => ({ users: [...mockAdminUsers] });

function mockPlatformIdentity(platform: "telegram" | "discord", uid: string) {
  const prefix = platform === "telegram" ? "tg:user:" : "dc:user:";
  return {
    platform,
    platform_uid: uid,
    platform_key: `${prefix}${uid}`,
  };
}

export function createMockAdminUser(body: {
  display_name: string;
  email: string;
  telegram_uid?: string | null;
  discord_uid?: string | null;
  agents?: string[];
}) {
  const email = body.email.trim().toLowerCase();
  if (mockAdminUsers.some((u) => u.email === email)) {
    return { error: "conflict" as const };
  }
  const telegramUid = (body.telegram_uid ?? "").trim();
  const discordUid = (body.discord_uid ?? "").trim();
  const user: MockAdminUser = {
    user_id: `rx:user:${Math.random().toString(16).slice(2, 10)}`,
    display_name: body.display_name.trim(),
    email,
    telegram: telegramUid ? mockPlatformIdentity("telegram", telegramUid) : null,
    discord: discordUid ? mockPlatformIdentity("discord", discordUid) : null,
    agents: [...(body.agents ?? [])],
  };
  mockAdminUsers.push(user);
  return user;
}

export function patchMockAdminUser(
  userId: string,
  body: {
    display_name?: string;
    email?: string;
    telegram_uid?: string | null;
    discord_uid?: string | null;
    agents?: string[];
  },
) {
  const user = mockAdminUsers.find((u) => u.user_id === userId);
  if (!user) return { error: "not_found" as const };
  if (body.email) {
    const email = body.email.trim().toLowerCase();
    if (mockAdminUsers.some((u) => u.user_id !== userId && u.email === email)) {
      return { error: "conflict" as const };
    }
    user.email = email;
  }
  if (body.display_name) {
    user.display_name = body.display_name.trim();
  }
  if ("telegram_uid" in body) {
    const telegramUid = (body.telegram_uid ?? "").trim();
    user.telegram = telegramUid ? mockPlatformIdentity("telegram", telegramUid) : null;
  }
  if ("discord_uid" in body) {
    const discordUid = (body.discord_uid ?? "").trim();
    user.discord = discordUid ? mockPlatformIdentity("discord", discordUid) : null;
  }
  if ("agents" in body) {
    user.agents = [...(body.agents ?? [])];
  }
  return user;
}

const mockAgentPlatforms: Record<
  string,
  { has_telegram: boolean; has_discord: boolean; has_email: boolean }
> = {
  lyra: { has_telegram: true, has_discord: true, has_email: false },
  aryl: { has_telegram: false, has_discord: true, has_email: false },
};

export const mockAgentsConfigList = () => ({
  agents: Object.keys(agentConfigs).map((name) => {
    const cfg = agentConfigs[name];
    const platforms = mockAgentPlatforms[name] ?? {
      has_telegram: false,
      has_discord: false,
      has_email: false,
    };
    return {
      name: cfg.name,
      backend: cfg.backend,
      model: cfg.model,
      updated_at: cfg.updated_at,
      soul_document_bytes: cfg.soul_document_bytes,
      has_soul: cfg.has_soul,
      ...platforms,
    };
  }),
});

export function mockAgentConfig(name: string) {
  const cfg = agentConfigs[name];
  if (!cfg) return null;
  const { has_soul: _hasSoul, ...rest } = cfg;
  return rest;
}

export function upsertMockAgentConfig(cfg: {
  name: string;
  backend: "claude-cli" | "omp-rpc";
  model: string;
  display_name?: string;
  tagline?: string;
}) {
  const row = {
    name: cfg.name,
    backend: cfg.backend,
    model: cfg.model,
    voice_json: null as null,
    soul_meta_json: {
      header: {
        display_name: cfg.display_name ?? cfg.name,
        tagline: cfg.tagline ?? "",
      },
    },
    soul_document_blob_ref: null as string | null,
    soul_document_bytes: 0,
    updated_at: new Date().toISOString(),
    has_soul: false,
  };
  agentConfigs[cfg.name] = row;
  const { has_soul: _hasSoul, ...rest } = row;
  return rest;
}

export function mockAgentSoul(name: string) {
  if (!agentConfigs[name]) return null;
  return {
    sections: name === "lyra" ? { ...soulSections } : {},
    updated_at: agentConfigs[name].updated_at,
  };
}

export function mockSoulPreview(sections: Record<string, string>) {
  const composed = Object.entries(sections)
    .filter(([, body]) => body.trim())
    .map(([title, body]) => `## ${title}\n${body.trim()}`)
    .join("\n\n");
  return { composed: composed || "(empty)", truncated: false };
}

const streamTokens = new Map<string, string>();
const pendingChatText = new Map<string, string>();

export function mintStreamToken(sessionId: string, userText = ""): string {
  const token = `mock-${sessionId}`;
  streamTokens.set(sessionId, token);
  pendingChatText.set(sessionId, userText);
  return token;
}

export function takeChatText(sessionId: string): string {
  return pendingChatText.get(sessionId) ?? "message";
}

export function verifyStreamToken(sessionId: string, token: string | null): boolean {
  return streamTokens.get(sessionId) === token;
}

export const mockPipelineRuns = () => ({
  runs: [
    {
      repo: "Roxabi/roxabi-factory",
      pr_number: 42,
      title: "feat: dashboard v2",
      head_sha: "abc123",
      head_ref: "staging",
      html_url: "https://github.com/Roxabi/roxabi-factory/pull/42",
      reviewed: false,
      open: true,
      ci_status: "failure",
      merge_status: "pending",
      publish_status: "n/a",
      m1_deploy_status: "n/a",
      cf_deploy_status: "n/a",
      checks: [{ name: "ci", status: "completed", conclusion: "failure" }],
      last_event_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
    {
      repo: "Roxabi/roxabi-factory",
      pr_number: 41,
      title: "chore: deps",
      head_sha: "def456",
      head_ref: "main",
      html_url: "https://github.com/Roxabi/roxabi-factory/pull/41",
      reviewed: true,
      open: false,
      ci_status: "success",
      merge_status: "success",
      publish_status: "pending",
      m1_deploy_status: "pending",
      cf_deploy_status: "n/a",
      checks: [],
      last_event_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ],
});

export const mockSpans = () => ({
  items: [
    {
      trace_id: "trace-dev-1",
      span_id: "span-dev-1",
      job_id: "dev-job-1",
      pool_id: "web:smoke:agent:lyra",
      component: "hub",
      envelope_name: "InboundMessage",
      subject: "factory.inbound.web.lyra",
      name: "process_turn",
      start_ts: Date.now() - 5000,
      duration_ms: 42,
      attributes: { agent: "lyra", status: "ok" },
    },
    {
      trace_id: "trace-dev-2",
      span_id: "span-dev-2",
      job_id: null,
      pool_id: null,
      component: "adapter",
      envelope_name: null,
      subject: null,
      name: "webhook",
      start_ts: Date.now() - 10000,
      duration_ms: 12,
      attributes: { platform: "web" },
    },
  ],
  total: 2,
  page: 1,
  page_size: 50,
});

const connectorInstallations: Array<{
  connector: string;
  external_id: string;
  factory_tenant: string;
  enabled: boolean;
}> = [
  {
    connector: "github",
    external_id: "12345678",
    factory_tenant: "roxabi",
    enabled: true,
  },
];

export const mockConnectors = () => ({
  connectors: [
    { name: "github", family: "vcs", label: "GitHub" },
    { name: "cloudflare", family: "dns", label: "Cloudflare" },
  ],
  factory_tenant: "roxabi",
});

export const mockGithubInstallUrl = () => ({
  url: "https://github.com/apps/roxabi-factory/installations/new",
  app_slug: "roxabi-factory",
});

export function mockConnectorInstallations(connector: string) {
  return {
    installations: connectorInstallations.filter((i) => i.connector === connector),
  };
}

export function upsertMockConnectorInstallation(
  connector: string,
  externalId: string,
  factoryTenant = "roxabi",
) {
  const existing = connectorInstallations.find(
    (i) => i.connector === connector && i.external_id === externalId,
  );
  if (existing) {
    existing.enabled = true;
    return existing;
  }
  const row = {
    connector,
    external_id: externalId,
    factory_tenant: factoryTenant,
    enabled: true,
  };
  connectorInstallations.push(row);
  return row;
}

export function deleteMockConnectorInstallation(connector: string, externalId: string) {
  const idx = connectorInstallations.findIndex(
    (i) => i.connector === connector && i.external_id === externalId,
  );
  if (idx >= 0) connectorInstallations.splice(idx, 1);
}

const jobsStreamTokens = new Map<string, string>();
const pipelineStreamTokens = new Map<string, string>();

export function mintJobsStreamToken(): string {
  const token = `jobs-mock-${Date.now()}`;
  jobsStreamTokens.set(token, token);
  return token;
}

export function verifyJobsStreamToken(token: string | null): boolean {
  return token !== null && jobsStreamTokens.has(token);
}

export function mintPipelineStreamToken(): string {
  const token = `pipeline-mock-${Date.now()}`;
  pipelineStreamTokens.set(token, token);
  return token;
}

export function verifyPipelineStreamToken(token: string | null): boolean {
  return token !== null && pipelineStreamTokens.has(token);
}
