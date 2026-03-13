from __future__ import annotations

import logging
import os
import re
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lyra.stt import STTService
    from lyra.tts import TTSService

from .circuit_breaker import CircuitRegistry
from .command_router import CommandConfig, CommandRouter
from .message import InboundMessage, Response
from .messages import MessageManager
from .plugin_loader import PluginLoader
from .pool import Pool

log = logging.getLogger(__name__)

# Default agents config directory: src/lyra/agents/
_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"

_VALID_BACKENDS: frozenset[str] = frozenset({"claude-cli", "ollama", "anthropic-sdk"})
_MAX_PROMPT_BYTES = 64 * 1024  # 64 KB

_VAULT_DIR = Path(
    os.environ.get("ROXABI_VAULT_DIR", str(Path.home() / ".roxabi-vault"))
)
_PERSONAS_DIR = _VAULT_DIR / "personas"

_WORKSPACE_BUILTIN_CONFLICTS = frozenset(
    {"help", "circuit", "routing", "stop", "config", "clear", "new"}
)


@dataclass(frozen=True)
class ModelConfig:
    """Per-agent model configuration.

    backend: execution backend — "claude-cli" (Claude Code subscription)
             or "ollama" (local, future).
    model:   model identifier passed to the backend CLI.
    max_turns: max agentic turns per conversation turn.
    tools:   allowed tools (empty = backend defaults).
    cwd:     working directory for the Claude subprocess (claude-cli only).
             None → defaults to the Lyra project root.
             Useful to point a dedicated agent at another project so it reads
             that project's CLAUDE.md and has access to its files.

    This will evolve into an intelligent model selection system.
    """

    backend: str = "claude-cli"
    model: str = "claude-sonnet-4-5"
    max_turns: int = 10
    tools: tuple[str, ...] = field(default=())
    # compare=False: cwd is spawn-routing config, not model identity.
    # Changing cwd should not trigger the "model_config mismatch" warning
    # in CliPool.send() — that check is for backend/model/tools changes only.
    cwd: Path | None = field(default=None, compare=False)


class Complexity(Enum):
    """Message complexity levels for routing decisions (#134)."""

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass(frozen=True)
class SmartRoutingConfig:
    """Configuration for the smart routing decorator (#134)."""

    enabled: bool = False
    routing_table: dict[Complexity, str] = field(default_factory=dict)
    history_size: int = 50


@dataclass(frozen=True)
class IdentityConfig:
    name: str
    tagline: str = ""
    creator: str = ""
    role: str = ""
    goal: str = ""


@dataclass(frozen=True)
class PersonalityConfig:
    traits: tuple[str, ...] = ()
    communication_style: str = ""
    tone: str = ""
    humor: str = ""


@dataclass(frozen=True)
class ExpertiseConfig:
    areas: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoiceConfig:
    speaking_style: str = ""
    pace: str = ""
    warmth: str = ""


@dataclass(frozen=True)
class PersonaConfig:
    identity: IdentityConfig
    personality: PersonalityConfig = field(default_factory=PersonalityConfig)
    expertise: ExpertiseConfig = field(default_factory=ExpertiseConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)


@dataclass
class Agent:
    """Configuration record for an agent. Mutable for hot-reload."""

    name: str
    system_prompt: str
    memory_namespace: str
    model_config: ModelConfig = field(default_factory=ModelConfig)
    permissions: tuple[str, ...] = field(default=())
    commands: dict[str, CommandConfig] = field(default_factory=dict)
    plugins_enabled: tuple[str, ...] = field(default=())  # empty = default-open
    persona: PersonaConfig | None = None
    i18n_language: str = "en"
    smart_routing: SmartRoutingConfig | None = None
    show_intermediate: bool = False  # show ⏳-prefixed intermediate turns to the user
    workspaces: dict[str, Path] = field(default_factory=dict)


def load_persona(name: str, personas_dir: Path | None = None) -> PersonaConfig:
    """Load PersonaConfig from a TOML file in the vault.

    Resolves name to {personas_dir}/{name}.persona.toml.
    Validates: name safe, file exists, [identity].name present.
    Creates personas_dir if absent.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"Invalid persona name {name!r}: only [a-zA-Z0-9_-] allowed")

    directory = personas_dir or _PERSONAS_DIR
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{name}.persona.toml"
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Persona name {name!r} escapes personas directory")
    if not path.exists():
        raise FileNotFoundError(f"Persona config not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    identity_data = data.get("identity", {})
    if not identity_data.get("name"):
        raise ValueError(f"Persona {name!r} missing required [identity].name")

    identity = IdentityConfig(
        name=identity_data["name"],
        tagline=identity_data.get("tagline", ""),
        creator=identity_data.get("creator", ""),
        role=identity_data.get("role", ""),
        goal=identity_data.get("goal", ""),
    )

    personality_data = data.get("personality", {})
    personality = PersonalityConfig(
        traits=tuple(personality_data.get("traits", [])),
        communication_style=personality_data.get("communication_style", ""),
        tone=personality_data.get("tone", ""),
        humor=personality_data.get("humor", ""),
    )

    expertise_data = data.get("expertise", {})
    expertise = ExpertiseConfig(
        areas=tuple(expertise_data.get("areas", [])),
        instructions=tuple(expertise_data.get("instructions", [])),
    )

    voice_data = data.get("voice", {})
    voice = VoiceConfig(
        speaking_style=voice_data.get("speaking_style", ""),
        pace=voice_data.get("pace", ""),
        warmth=voice_data.get("warmth", ""),
    )

    return PersonaConfig(
        identity=identity,
        personality=personality,
        expertise=expertise,
        voice=voice,
    )


def compose_system_prompt(persona: PersonaConfig) -> str:  # noqa: C901 — many optional persona fields each add one branch
    """Build a natural prose system prompt from PersonaConfig fields."""
    parts: list[str] = []

    # Identity paragraph
    ident = persona.identity
    intro = f"You are {ident.name}"
    if ident.tagline:
        intro += f", {ident.tagline}"
    if ident.creator:
        intro += f", created by {ident.creator}"
    intro += "."
    if ident.goal:
        intro += f" {ident.goal}"
    parts.append(intro)

    # Personality paragraph
    p = persona.personality
    if p.traits or p.communication_style or p.tone:
        personality_parts: list[str] = []
        if p.traits:
            personality_parts.append(f"Your core traits are: {', '.join(p.traits)}.")
        if p.communication_style:
            personality_parts.append(
                f"Your communication style is {p.communication_style}."
            )
        if p.tone:
            personality_parts.append(f"Your tone is {p.tone}.")
        if p.humor:
            personality_parts.append(f"Your sense of humor: {p.humor}.")
        parts.append(" ".join(personality_parts))

    # Expertise paragraph
    e = persona.expertise
    if e.areas:
        parts.append(f"Your areas of expertise include: {', '.join(e.areas)}.")
    if e.instructions:
        instruction_lines = "\n".join(f"- {i}" for i in e.instructions)
        parts.append(f"Guidelines:\n{instruction_lines}")

    composed = "\n\n".join(parts)

    encoded = composed.encode()
    if len(encoded) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"Composed system prompt exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded)} bytes)"
        )

    return composed


def load_agent_config(  # noqa: C901, PLR0915 — config parsing with many independent TOML sections and validation branches
    name: str,
    agents_dir: Path | None = None,
    personas_dir: Path | None = None,
) -> Agent:
    """Load an Agent from a TOML config file.

    Looks for <agents_dir>/<name>.toml.
    Falls back to _AGENTS_DIR if agents_dir is not specified.

    TOML structure:
        [agent]
        memory_namespace = "lyra"
        permissions = []
        persona = "lyra_default"   # optional: loads from vault

        [model]
        backend = "claude-cli"
        model = "claude-sonnet-4-5"
        max_turns = 10
        tools = []

        [prompt]
        system = "..."             # optional: overrides persona composition
    """
    directory = agents_dir or _AGENTS_DIR

    # Primary gate: only safe characters allowed in agent names
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"Invalid agent name {name!r}: only [a-zA-Z0-9_-] allowed")

    path = directory / f"{name}.toml"
    # Secondary gate (defence-in-depth): guard against symlinks or edge cases
    # in path resolution even though the regex above makes traversal impossible.
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Agent name {name!r} escapes agents directory")
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    agent_section = data.get("agent", {})

    # Validate declared name matches the filename stem (if declared)
    declared_name = agent_section.get("name")
    if declared_name is not None and declared_name != name:
        raise ValueError(
            f"Agent name mismatch: file {name!r} declares name {declared_name!r}"
        )

    model_section = data.get("model", {})
    prompt_section = data.get("prompt", {})

    backend = model_section.get("backend", "claude-cli")
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"Invalid backend {backend!r} for agent {name!r}: "
            f"must be one of {sorted(_VALID_BACKENDS)}"
        )

    model = model_section.get("model", "claude-sonnet-4-5")
    if not re.match(r"^[a-zA-Z0-9_.:-]+$", model):
        raise ValueError(
            f"Invalid model {model!r} for agent {name!r}: "
            "only [a-zA-Z0-9_.:-] characters allowed"
        )

    cwd: Path | None = None
    raw_cwd = model_section.get("cwd")
    if raw_cwd is not None:
        resolved = Path(raw_cwd).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(
                f"[model].cwd {raw_cwd!r} for agent {name!r} is not a directory"
            )
        cwd = resolved

    model_cfg = ModelConfig(
        backend=backend,
        model=model,
        max_turns=int(model_section.get("max_turns", 10)),
        tools=tuple(model_section.get("tools", [])),
        cwd=cwd,
    )

    # Persona loading
    persona_name = agent_section.get("persona")
    persona: PersonaConfig | None = None
    if persona_name:
        persona = load_persona(persona_name, personas_dir)

    system_prompt = prompt_section.get("system", "")
    if not system_prompt and persona:
        system_prompt = compose_system_prompt(persona)

    encoded_prompt = system_prompt.encode()
    if len(encoded_prompt) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"system_prompt for agent {name!r} exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded_prompt)} bytes)"
        )

    commands_section = data.get("commands", {})
    commands: dict[str, CommandConfig] = {}
    for cmd_name, cmd_data in commands_section.items():
        commands[cmd_name] = CommandConfig(
            description=cmd_data.get("description", ""),
            builtin=bool(cmd_data.get("builtin", False)),
            timeout=float(cmd_data.get("timeout", 30.0)),
        )

    plugins_section = data.get("plugins", {})
    plugins_enabled: tuple[str, ...] = tuple(plugins_section.get("enabled", []))

    i18n_section = data.get("i18n", {})
    i18n_language: str = i18n_section.get("default_language", "en")
    if not re.match(r"^[a-z]{2,8}$", i18n_language):
        log.warning(
            "Invalid i18n language %r in agent config — falling back to 'en'",
            i18n_language,
        )
        i18n_language = "en"

    # Smart routing config (#134)
    smart_routing: SmartRoutingConfig | None = None
    sr_section = agent_section.get("smart_routing")
    if sr_section is not None:
        sr_models = sr_section.get("models", {})
        routing_table: dict[Complexity, str] = {}
        for level in Complexity:
            model_id = sr_models.get(level.value)
            if model_id:
                if not re.match(r"^[a-zA-Z0-9_.:-]+$", model_id):
                    raise ValueError(
                        f"Invalid model {model_id!r} for smart_routing "
                        f"level {level.value!r}: "
                        "only [a-zA-Z0-9_.:-] characters allowed"
                    )
                routing_table[level] = model_id
        smart_routing = SmartRoutingConfig(
            enabled=bool(sr_section.get("enabled", False)),
            routing_table=routing_table,
            history_size=int(sr_section.get("history_size", 50)),
        )

    workspaces_section = data.get("workspaces", {})
    workspaces: dict[str, Path] = {}
    for key, raw_path in workspaces_section.items():
        if not re.match(r"^[a-zA-Z0-9_-]+$", key):
            raise ValueError(
                f"Invalid workspace name {key!r} in agent {name!r}: "
                "only [a-zA-Z0-9_-] allowed"
            )
        if key in _WORKSPACE_BUILTIN_CONFLICTS:
            raise ValueError(
                f"Workspace key {key!r} in agent {name!r} clashes with "
                f"built-in command /{key}"
            )
        resolved = Path(raw_path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(
                f"[workspaces].{key} path {raw_path!r} for agent {name!r} "
                "is not a directory"
            )
        workspaces[key] = resolved

    return Agent(
        name=name,
        system_prompt=system_prompt,
        memory_namespace=agent_section.get("memory_namespace", name),
        model_config=model_cfg,
        permissions=tuple(agent_section.get("permissions", [])),
        commands=commands,
        plugins_enabled=plugins_enabled,
        persona=persona,
        i18n_language=i18n_language,
        smart_routing=smart_routing,
        show_intermediate=bool(agent_section.get("show_intermediate", False)),
        workspaces=workspaces,
    )


class AgentBase(ABC):
    """Abstract base for concrete agent implementations.

    All mutable state lives in Pool.
    Supports hot-reload: edit the TOML or persona file and config updates
    on next message.
    """

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        config: Agent,
        agents_dir: Path | None = None,
        plugins_dir: Path | None = None,
        circuit_registry: CircuitRegistry | None = None,
        admin_user_ids: set[str] | None = None,
        msg_manager: MessageManager | None = None,
        stt: "STTService | None" = None,
        tts: "TTSService | None" = None,
        smart_routing_decorator: Any | None = None,
    ) -> None:
        self.config = config
        self._agents_dir = agents_dir or _AGENTS_DIR
        self._config_path = self._agents_dir / f"{config.name}.toml"
        self._last_mtime: float = (
            self._config_path.stat().st_mtime if self._config_path.exists() else 0.0
        )
        self._circuit_registry = circuit_registry
        self._admin_user_ids = admin_user_ids
        self._msg_manager = msg_manager
        # Subclasses must invoke self._stt when msg.type == MessageType.AUDIO.
        # Temp file cleanup (AudioContent.url) must live in a finally block in
        # process() — the agent owns it, not STTService. See ADR-013.
        self._stt = stt
        # Subclasses invoke self._tts when /voice command detected in process().
        self._tts = tts
        self._smart_routing_decorator = smart_routing_decorator
        self._plugins_dir = plugins_dir or _PLUGINS_DIR
        self._plugin_loader = PluginLoader(self._plugins_dir)
        self._effective_plugins = self._init_plugins()
        self._plugin_mtimes: dict[str, float] = self._record_plugin_mtimes()
        self.command_router: CommandRouter = CommandRouter(
            self._plugin_loader,
            self._effective_plugins,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            smart_routing_decorator=smart_routing_decorator,
            **self._build_router_kwargs(),
        )
        self._persona_path: Path | None = None
        self._persona_mtime: float = 0.0
        self._update_persona_tracking()

    def _update_persona_tracking(self) -> None:
        """Resolve and track persona vault file for hot-reload."""
        if self.config.persona is None:
            self._persona_path = None
            self._persona_mtime = 0.0
            return
        try:
            with self._config_path.open("rb") as f:
                data = tomllib.load(f)
            persona_name = data.get("agent", {}).get("persona", "")
            if persona_name:
                p_path = _PERSONAS_DIR / f"{persona_name}.persona.toml"
                self._persona_path = p_path
                self._persona_mtime = p_path.stat().st_mtime if p_path.exists() else 0.0
        except Exception:
            pass

    def _init_plugins(self) -> list[str]:
        """Load plugins and return the effective enabled list.

        Only plugins that load successfully are included in the returned list.
        If a plugin has enabled=false in its manifest, load() raises ValueError
        and the plugin is skipped — this enforces SC-9 regardless of agent config.
        """
        if self.config.plugins_enabled:
            names = list(self.config.plugins_enabled)
        else:
            # default-open: load all manifest.enabled=True plugins discovered in
            # plugins_dir. Security assumption: plugins_dir is a trusted directory
            # controlled by the operator. Do not point plugins_dir at a
            # world-writable or network-accessible path.
            manifests = self._plugin_loader.discover()
            names = [m.name for m in manifests if m.enabled]
        effective: list[str] = []
        for name in names:
            try:
                self._plugin_loader.load(name)
                effective.append(name)
            except ValueError as exc:
                log.warning("Skipping plugin %r: %s", name, exc)
            except Exception:  # noqa: BLE001  # resilient: don't let one bad plugin block startup
                log.warning("Failed to load plugin %r", name, exc_info=True)
        return effective

    def _record_plugin_mtimes(self) -> dict[str, float]:
        """Record current mtime for each loaded plugin's handlers.py."""
        mtimes: dict[str, float] = {}
        for name in self._effective_plugins:
            handlers_path = self._plugins_dir / name / "handlers.py"
            try:
                mtimes[name] = handlers_path.stat().st_mtime
            except OSError:
                pass
        return mtimes

    @property
    def name(self) -> str:
        return self.config.name

    def _maybe_reload(self) -> None:
        """Reload config from TOML if the agent or persona file changed."""
        try:
            mtime = self._config_path.stat().st_mtime
        except OSError:
            return

        config_changed = mtime > self._last_mtime
        persona_changed = False

        if self._persona_path:
            try:
                p_mtime = self._persona_path.stat().st_mtime
                persona_changed = p_mtime > self._persona_mtime
            except OSError:
                pass

        if config_changed or persona_changed:
            try:
                new_config = load_agent_config(self.config.name, self._agents_dir)
                if new_config != self.config:
                    log.info(
                        "Hot-reloaded config for agent %r (model: %s -> %s)",
                        self.config.name,
                        self.config.model_config.model,
                        new_config.model_config.model,
                    )
                    self.config = new_config
                    self.command_router = CommandRouter(
                        self._plugin_loader,
                        self._effective_plugins,
                        circuit_registry=self._circuit_registry,
                        admin_user_ids=self._admin_user_ids,
                        msg_manager=self._msg_manager,
                        smart_routing_decorator=self._smart_routing_decorator,
                        **self._build_router_kwargs(),
                    )
                self._last_mtime = mtime
                self._update_persona_tracking()
            except Exception as exc:
                log.warning("Failed to reload config for %r: %s", self.config.name, exc)

        self._reload_plugins()

    def _reload_plugins(self) -> None:
        plugins_changed = False
        for name in list(self._plugin_mtimes):
            handlers_path = self._plugins_dir / name / "handlers.py"
            try:
                new_mtime = handlers_path.stat().st_mtime
            except OSError:
                continue
            if new_mtime > self._plugin_mtimes[name]:
                try:
                    self._plugin_loader.reload(name)
                    self._plugin_mtimes[name] = new_mtime
                    plugins_changed = True
                    log.info("Hot-reloaded plugin %r", name)
                except Exception:  # noqa: BLE001  # resilient: don't let hot-reload crash the agent
                    log.warning("Failed to reload plugin %r", name, exc_info=True)
        if plugins_changed:
            self.command_router = CommandRouter(
                self._plugin_loader,
                self._effective_plugins,
                circuit_registry=self._circuit_registry,
                admin_user_ids=self._admin_user_ids,
                msg_manager=self._msg_manager,
                smart_routing_decorator=self._smart_routing_decorator,
                **self._build_router_kwargs(),
            )

    def _build_router_kwargs(self) -> dict:
        """Hook for subclasses to inject extra CommandRouter constructor kwargs."""
        return {}

    async def _handle_voice_command(self, msg: InboundMessage) -> "Response | None":
        """Handle a /voice command if TTS is configured. Returns None to fall through.

        Pre-router called at the top of each agent's process(). If the message
        starts with "/voice <text>" and self._tts is set, synthesizes speech and
        returns a Response with audio set. Returns None for all other messages
        (text or bare /voice with no args) so normal processing continues.
        """
        if self._tts is None:
            return None
        stripped = msg.text.strip()
        _VOICE_PREFIX = "/voice "
        if not stripped.lower().startswith(_VOICE_PREFIX):
            return None
        text = stripped[len(_VOICE_PREFIX) :].strip()
        if not text:
            return None
        from .message import OutboundAudio, Response

        try:
            result = await self._tts.synthesize(text)
            audio = OutboundAudio(
                audio_bytes=result.audio_bytes,
                mime_type=result.mime_type,
                duration_ms=result.duration_ms,
            )
            return Response(content="", audio=audio)
        except Exception:
            log.error("TTS synthesis failed for /voice command", exc_info=True)
            return Response(content="Sorry, I couldn't generate audio.")

    def is_backend_alive(self, _pool_id: str) -> bool:
        """Return True if the backend process for this pool is alive.

        Subclasses backed by persistent processes (e.g. SimpleAgent with
        claude-cli) should override this to check the actual process state.
        """
        return True

    @abstractmethod
    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate: "Callable[[str], Awaitable[None]] | None" = None,
    ) -> Response: ...
