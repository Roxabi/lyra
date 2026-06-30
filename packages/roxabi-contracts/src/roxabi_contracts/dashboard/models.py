"""Dashboard BFF wire models (#1771)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HarnessKind = Literal["claude-cli", "omp-rpc"]
SseEventType = Literal["delta", "done", "error", "ping"]
PlatformTag = Literal["telegram", "discord", "web"]


class ChatRequest(BaseModel):
    agent: str
    text: str
    session_id: str | None = None
    harness: HarnessKind = "claude-cli"
    model: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    stream_token: str
    accepted: bool = True


class SseEvent(BaseModel):
    type: SseEventType
    text: str | None = None
    message: str | None = None


class AgentHealth(BaseModel):
    agent: str
    in_roster: bool
    harness: HarnessKind
    harness_reachable: bool
    online: bool


class AgentHealthResponse(BaseModel):
    agents: list[AgentHealth]


class DashboardSession(BaseModel):
    session_id: str
    pool_id: str
    platform: PlatformTag
    cli_session_id: str | None = None
    first_user_msg: str | None = None
    turn_count: int = 0
    last_active_at: str


class DashboardSessionsListRequest(BaseModel):
    agent: str
    limit: int = Field(default=20, ge=1, le=50)


class DashboardSessionsListResponse(BaseModel):
    sessions: list[DashboardSession]


class DashboardSessionsResumeRequest(BaseModel):
    cli_session_id: str
    agent: str


class DashboardSessionsResumeResponse(BaseModel):
    accepted: bool
    message: str = ""


class DashboardTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: str


class DashboardSessionsTurnsRequest(BaseModel):
    session_id: str
    limit: int = Field(default=200, ge=1, le=500)


class DashboardSessionsTurnsResponse(BaseModel):
    turns: list[DashboardTurn]


class DashboardJob(BaseModel):
    job_id: str
    pool_id: str
    agent: str | None = None
    platform: PlatformTag | str | None = None
    status: Literal["open", "closing"] | str
    started_at: str
    concurrency_mode: str
    worker_loc: str | None = None
    steer_subject: str


class DashboardJobsListResponse(BaseModel):
    jobs: list[DashboardJob]


class DashboardJobsLaunchRequest(BaseModel):
    agent: str
    prompt: str = Field(min_length=1, max_length=8000)
    job_name: str = "omp"
    pool_id: str | None = None
    model: str | None = None
    system_prompt: str = ""


class DashboardJobsLaunchResponse(BaseModel):
    accepted: bool
    job_id: str = ""
    message: str = ""
    dispatch_subject: str = ""


class DashboardJobsSteerRequest(BaseModel):
    job_id: str
    text: str = Field(min_length=1, max_length=4000)


class DashboardJobsSteerResponse(BaseModel):
    accepted: bool
    message: str = ""


OpsEngineId = Literal["loki", "langfuse", "otel-collector"]
OpsLogPreset = Literal["hub-errors", "operator-events", "deploy-failures"]


class OpsEngineHealth(BaseModel):
    engine: OpsEngineId
    label: str
    reachable: bool
    detail: str = ""


class DashboardOpsHealthResponse(BaseModel):
    engines: list[OpsEngineHealth]


class OpsLogEntry(BaseModel):
    timestamp: str
    line: str
    labels: dict[str, str] = Field(default_factory=dict)


class DashboardOpsLogsResponse(BaseModel):
    preset: OpsLogPreset
    query: str
    engine_reachable: bool
    entries: list[OpsLogEntry]


class VoiceEngineInfo(BaseModel):
    name: str
    supports_voice: bool = False
    supports_clone: bool = False
    vram_gib_est: float | None = None


class VoiceSampleInfo(BaseModel):
    id: str
    store_key: str | None = None
    filename: str
    cached: bool = False


class VoiceTtsCapabilities(BaseModel):
    engines: list[VoiceEngineInfo] = []
    samples: list[VoiceSampleInfo] = []
    max_cached_engines: int = 1
    default_engine: str | None = None
    catalog_revision: str | None = None


class VoiceSttCapabilities(BaseModel):
    models: list[dict[str, str]] = []
    default_model: str | None = None


class DashboardVoiceCapabilitiesResponse(BaseModel):
    tts: VoiceTtsCapabilities | None = None
    stt: VoiceSttCapabilities | None = None
    error: str | None = None


class SoulMetaHeader(BaseModel):
    display_name: str = ""
    tagline: str = ""


class SoulMetaMemory(BaseModel):
    enabled: bool = False
    namespace: str | None = None
    source: str = "cortex"
    sync_mode: str = "none"


class SoulMetaEnvelope(BaseModel):
    schema_version: int = 1
    header: SoulMetaHeader = Field(default_factory=SoulMetaHeader)
    memory: SoulMetaMemory = Field(default_factory=SoulMetaMemory)
    extensions: dict[str, object] = Field(default_factory=dict)


class DashboardAgentSummary(BaseModel):
    name: str
    backend: HarnessKind
    model: str
    updated_at: str
    soul_document_bytes: int | None = None
    has_soul: bool = False


class DashboardAgentsListResponse(BaseModel):
    agents: list[DashboardAgentSummary]


class DashboardAgentConfigResponse(BaseModel):
    name: str
    backend: HarnessKind
    model: str
    voice_json: dict[str, object] | None = None
    soul_meta_json: SoulMetaEnvelope | None = None
    soul_document_blob_ref: str | None = None
    soul_document_bytes: int | None = None
    updated_at: str


class DashboardAgentPatchRequest(BaseModel):
    backend: HarnessKind | None = None
    model: str | None = None
    voice_json: dict[str, object] | None = None
    display_name: str | None = None
    tagline: str | None = None


class DashboardAgentSoulPutRequest(BaseModel):
    markdown: str = Field(min_length=0)


class DashboardAgentSoulSectionsResponse(BaseModel):
    sections: dict[str, str]
    soul_document_blob_ref: str | None = None
    soul_document_bytes: int | None = None
    updated_at: str


class DashboardAgentSoulPreviewRequest(BaseModel):
    sections: dict[str, str] = Field(default_factory=dict)


class DashboardAgentSoulPreviewResponse(BaseModel):
    composed: str
    truncated: bool = False


FleetStatus = Literal["ok", "stale", "unknown", "pinned"]


class DashboardFleetRow(BaseModel):
    container_name: str
    host: str = ""
    component_key: str
    image_ref: str
    image_revision: str | None = None
    health: str = "unknown"
    status: FleetStatus
    last_report_at: str | None = None
    age_s: float | None = None
    systemd_unit: str
    instrumented: bool = True
    source: str = "manifest"


class DashboardFleetResponse(BaseModel):
    rows: list[DashboardFleetRow]


ConnectorName = Literal["github", "cloudflare"]


class ConnectorInstallationRow(BaseModel):
    connector: str
    external_id: str
    factory_tenant: str
    enabled: bool


class DashboardConnectorInstallationsListRequest(BaseModel):
    connector: str
    factory_tenant: str


class DashboardConnectorInstallationsListResponse(BaseModel):
    installations: list[ConnectorInstallationRow]


class DashboardConnectorInstallationUpsertRequest(BaseModel):
    connector: str
    external_id: str
    factory_tenant: str
    operator_tenant: str
    metadata: dict[str, str] | None = None


class DashboardConnectorInstallationUpsertResponse(BaseModel):
    ok: bool = True


class DashboardConnectorInstallationDeleteRequest(BaseModel):
    connector: str
    external_id: str
    operator_tenant: str


class DashboardConnectorInstallationDeleteResponse(BaseModel):
    ok: bool = True


class DashboardGithubInstallUrlResponse(BaseModel):
    url: str
    app_slug: str
