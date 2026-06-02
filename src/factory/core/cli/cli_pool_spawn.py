"""Spawn-mixin for CliPool — extracted from cli_pool_worker.py (#1588)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from roxabi_contracts.audit import SecurityEvent
from roxabi_contracts.envelope import CONTRACT_VERSION

from ..agent.agent_config import ModelConfig
from ..trace import TraceContext
from .cli_pool_entry import _ProcessEntry
from .protocol.cli_protocol_types import _read_stderr_snippet, build_cmd

if TYPE_CHECKING:
    from factory.core.ports.audit_sink import AuditSink

log = logging.getLogger(__name__)

# Explicit env allowlist — only forward safe vars to the claude subprocess.
# CLAUDE_CODE_OAUTH_TOKEN: 1-year setup-token (auth precedence #5) bypasses the
# broken interactive-OAuth refresh path on headless subprocess invocations
# (anthropics/claude-code#50743). Injected via Podman secret in prod
# (ADR-054 (absorbed into ADR-055)).
# The token reaches /proc/<claude-pid>/environ — accepted residual risk per
# the single-tenant container threat model (DropCapability=all, ReadOnly=true,
# UserNS=keep-id; claude CLI accepts auth ONLY via env, no file alternative).
#
# DO NOT add ANTHROPIC_API_KEY here. Forwarding it overrides Pro/Max
# subscription billing and silently routes inference to Console pay-per-token
# (auth precedence #3 > #5). Use CLAUDE_CODE_OAUTH_TOKEN exclusively.
_SAFE_ENV_KEYS = {
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
}


def _find_project_root() -> Path:
    """Locate the project root by searching for pyproject.toml."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate project root (no pyproject.toml found)")


# Default cwd for the claude subprocess.
# FACTORY_CLAUDE_CWD overrides — used in containers where the app root (/app)
# differs from the workspace claude should operate in (~/projects).
_env_cwd = os.environ.get("FACTORY_CLAUDE_CWD")
_LYRA_ROOT = Path(_env_cwd) if _env_cwd else _find_project_root()


class CliPoolSpawnMixin:
    """Mixin providing _spawn() for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _entries: dict[str, _ProcessEntry]
        _cwd_overrides: dict[str, Path]
        _resume_session_ids: dict[str, str]
        _default_timeout: int
        _read_buffer_bytes: int
        _audit_sink: AuditSink | None
        _audit_tasks: set[asyncio.Task[None]]

    def _build_cmd(
        self,
        model_config: ModelConfig,
        session_id: str | None = None,
        system_prompt: str = "",
    ) -> tuple[list[str], str | None]:
        return build_cmd(model_config, session_id, system_prompt)

    @staticmethod
    def _identity_env(
        agent_name: str | None,
        agent_email: str | None,
        lyra_session_id: str | None,
    ) -> dict[str, str]:
        """Build the per-session attribution env vars (#1150) for the subprocess.

        Layered gate: agent_name alone → trailers-only mode (Lyra-Agent +
        Lyra-Session-Id, appended by the prepare-commit-msg hook). agent_name
        + agent_email → trailers AND committer identity. Without agent_name
        the subprocess falls back entirely to the image-baked template.

        These keys are NOT in `_SAFE_ENV_KEYS` — they're synthesised here, not
        inherited from the parent process; the allowlist filters inherited env.

        Sanitisation: CR/LF stripped from every value before injection. An
        embedded LF in FACTORY_SESSION_ID / FACTORY_AGENT lands verbatim in
        `git interpret-trailers --trailer "Lyra-Foo: $val"` and git's parser
        (and GitHub's) treats the LF as a trailer-line separator, smuggling
        arbitrary trailers (e.g. fake Co-Authored-By). An LF in
        GIT_COMMITTER_NAME / GIT_COMMITTER_EMAIL corrupts the commit-object
        header. The guard lives here, not in the bash hook. `is not None and
        != ""` (rather than truthiness) keeps empty-string as a distinct
        invalid state surfaced by callers instead of silently dropped.
        """
        if agent_name is None or agent_name == "":
            return {}
        strip = lambda v: v.replace("\r", "").replace("\n", "")  # noqa: E731
        safe_name = strip(agent_name)
        ident: dict[str, str] = {"FACTORY_AGENT": safe_name}
        if lyra_session_id is not None and lyra_session_id != "":
            ident["FACTORY_SESSION_ID"] = strip(lyra_session_id)
        if agent_email is not None and agent_email != "":
            ident["GIT_COMMITTER_NAME"] = safe_name
            ident["GIT_COMMITTER_EMAIL"] = strip(agent_email)
        return ident

    async def _spawn(  # noqa: PLR0913
        self,
        pool_id: str,
        model_config: ModelConfig,
        system_prompt: str = "",
        agent_name: str | None = None,
        agent_email: str | None = None,
        lyra_session_id: str | None = None,
    ) -> _ProcessEntry | None:
        spawn_cwd = self._cwd_overrides.get(pool_id) or model_config.cwd or _LYRA_ROOT
        resume_session_id = self._resume_session_ids.pop(pool_id, None)
        cmd, prompt_file = build_cmd(
            model_config,
            session_id=resume_session_id,
            system_prompt=system_prompt,
        )
        log.info(
            "[pool:%s] spawning: backend=%s model=%s cwd=%s resume=%s",
            pool_id,
            model_config.backend,
            model_config.model,
            spawn_cwd,
            resume_session_id or "-",
        )
        log.debug("[pool:%s] cmd: %s", pool_id, " ".join(cmd))
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        env["HOME"] = str(Path.home())
        env.update(self._identity_env(agent_name, agent_email, lyra_session_id))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(spawn_cwd),
                limit=self._read_buffer_bytes,  # prevents LimitOverrunError
                env=env,
            )
        except Exception as exc:  # noqa: BLE001  — DEBT:boundary-broad-catch# top-level boundary
            log.error("[pool:%s] failed to spawn: %s", pool_id, exc)
            if prompt_file:
                Path(prompt_file).unlink(missing_ok=True)
            return None

        # Early liveness check: if the process dies within 100ms (e.g. auth
        # failure, missing binary, bad flags), capture stderr and fail fast
        # instead of returning an entry that will produce "stdout EOF".
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            pass  # still alive — expected happy path
        else:
            # Process already exited
            stderr_snippet = await _read_stderr_snippet(proc)
            log.error(
                "[pool:%s] process exited immediately (rc=%d): %s",
                pool_id,
                proc.returncode or -1,
                stderr_snippet or "(no stderr)",
            )
            if prompt_file:
                Path(prompt_file).unlink(missing_ok=True)
            return None

        # Wire the session persist callback if the subclass provides it.
        _persist_fn = getattr(self, "_persist_cli_session", None)
        entry = _ProcessEntry(
            proc=proc,
            pool_id=pool_id,
            model_config=model_config,
            system_prompt=system_prompt,
            resumed_from=resume_session_id,
            prompt_file=prompt_file,
            _on_session_update=_persist_fn,
        )
        self._entries[pool_id] = entry
        log.info("[pool:%s] spawned (PID=%d)", pool_id, proc.pid)

        if self._audit_sink is not None:
            tools = model_config.tools
            task: asyncio.Task[None] = asyncio.create_task(
                self._audit_sink.emit(
                    SecurityEvent(
                        contract_version=CONTRACT_VERSION,
                        trace_id=(
                            TraceContext.get_trace_id()
                            or f"synthetic-{TraceContext.generate()}"
                        ),
                        issued_at=datetime.now(UTC),
                        kind="cli.subprocess.spawned",
                        pool_id=pool_id,
                        agent_name=TraceContext.get_agent_name() or "",
                        skip_permissions=model_config.skip_permissions,
                        tools_restricted=bool(tools),
                        tools_allowlist=list(tools),
                        model=model_config.model or "unknown",
                        pid=proc.pid,
                    )
                )
            )
            self._audit_tasks.add(task)
            task.add_done_callback(self._audit_tasks.discard)

        return entry
