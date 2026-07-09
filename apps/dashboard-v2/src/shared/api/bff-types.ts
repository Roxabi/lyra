import type { HarnessKind } from "@/shared/lib/chats-storage";

export interface AgentHealth {
  agent: string;
  in_roster: boolean;
  harness: HarnessKind;
  harness_reachable: boolean;
  online: boolean;
}

export interface DashboardSession {
  session_id: string;
  pool_id: string;
  platform: "telegram" | "discord" | "web";
  cli_session_id: string | null;
  first_user_msg: string | null;
  turn_count: number;
  last_active_at: string;
}

export type FleetStatus = "ok" | "stale" | "unknown" | "pinned";
export type ImageDigestStatus = "current" | "stale" | "unknown_compare" | "n/a";

export interface FleetRow {
  container_name: string;
  host: string;
  component_key: string;
  image_ref: string;
  image_revision: string | null;
  health: string;
  status: FleetStatus;
  last_report_at: string | null;
  age_s: number | null;
  systemd_unit: string;
  instrumented: boolean;
  source: string;
  image_digest_status: ImageDigestStatus;
}

export type PipelineStageStatus =
  | "pending"
  | "running"
  | "success"
  | "failure"
  | "skipped"
  | "unknown"
  | "n/a";

export interface PipelineCheck {
  name: string;
  status: string;
  conclusion: string | null;
}

export interface PipelineRun {
  repo: string;
  pr_number: number;
  title: string;
  head_sha: string | null;
  head_ref: string | null;
  html_url: string | null;
  reviewed: boolean;
  open: boolean;
  ci_status: PipelineStageStatus;
  merge_status: PipelineStageStatus;
  publish_status: PipelineStageStatus;
  m1_deploy_status: PipelineStageStatus;
  cf_deploy_status: PipelineStageStatus;
  checks: PipelineCheck[];
  last_event_at: string | null;
  updated_at: string | null;
}

export interface DashboardTurn {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface DashboardJob {
  job_id: string;
  pool_id: string;
  agent: string | null;
  platform: string | null;
  status: string;
  started_at: string;
  concurrency_mode: string;
  worker_loc: string | null;
  steer_subject: string;
}

export type JobsStreamEvent =
  | { type: "snapshot"; jobs: DashboardJob[] }
  | { type: "ping" }
  | { type: "error"; message: string };

export type PipelineStreamEvent =
  | { type: "snapshot"; runs: PipelineRun[] }
  | { type: "ping" }
  | { type: "error"; message: string };

export type OpsLogPreset =
  | "hub-errors"
  | "operator-events"
  | "deploy-failures"
  | "container-journal";

export interface OpsEngineHealth {
  engine: "loki" | "langfuse" | "otel";
  label: string;
  reachable: boolean;
  detail: string;
}

export interface OpsLogEntry {
  timestamp: string;
  line: string;
  labels: Record<string, string>;
}

export interface SpanRow {
  trace_id: string;
  span_id: string;
  job_id: string | null;
  pool_id: string | null;
  component: string | null;
  envelope_name: string | null;
  subject: string | null;
  name: string;
  start_ts: number;
  duration_ms: number;
  attributes: Record<string, string | number | boolean>;
}

export interface ConnectorDescriptor {
  name: string;
  family: string;
  label: string;
}

export interface ConnectorInstallation {
  connector: string;
  external_id: string;
  factory_tenant: string;
  enabled: boolean;
}

export const MODEL_CATALOG: Record<HarnessKind, string[]> = {
  "claude-cli": ["sonnet", "opus", "haiku"],
  "omp-rpc": ["omp-default", "omp-fast"],
};

export function defaultModelForHarness(harness: HarnessKind): string {
  const models = MODEL_CATALOG[harness];
  const first = models[0];
  if (!first) throw new Error(`no models configured for harness ${harness}`);
  return first;
}
