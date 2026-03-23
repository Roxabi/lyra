"""JsonAgentStore: in-memory + JSON-file-backed agent store for testing.

Satisfies the same interface as AgentStore (AgentStoreProtocol) without
requiring a SQLite database or aiosqlite connection lifecycle.

Intended use: activate via ``LYRA_DB=json`` env var or instantiate directly in
tests via the ``json_agent_store`` pytest fixture.

All state is held in memory.  Write operations persist to the JSON file so the
store survives a close/connect cycle within the same test session (useful when
testing reconnect behaviour). Runtime state is intentionally not persisted — it
is session-scoped even in production.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from ..agent_models import VALID_AGENT_STATUSES, AgentRow, AgentRuntimeStateRow
from ..agent_seeder import seed_from_toml as _seed_from_toml

log = logging.getLogger(__name__)

__all__ = ["JsonAgentStore"]


class JsonAgentStore:
    """In-memory agent store backed by a JSON file.

    Mirrors the public interface of :class:`~lyra.core.agent_store.AgentStore`
    so it can be swapped in transparently anywhere the
    :class:`~lyra.core.agent_store_protocol.AgentStoreProtocol` is expected.

    Usage::

        store = JsonAgentStore(path=tmp_path / "agents_test.json")
        await store.connect()
        await store.upsert(row)
        agent = store.get("my-agent")
        await store.close()
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._agents: dict[str, AgentRow] = {}
        self._bot_map: dict[tuple[str, str], str] = {}
        self._bot_settings: dict[tuple[str, str], dict] = {}
        self._connected: bool = False

    @property
    def path(self) -> Path:
        """Path to the backing JSON file."""
        return self._path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Load state from JSON file if it exists; start empty otherwise.

        Idempotent — calling connect() a second time is a no-op.
        """
        if self._connected:
            return
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for row_dict in data.get("agents", []):
                    row = AgentRow(**row_dict)
                    self._agents[row.name] = row
                for key_str, name in data.get("bot_map", {}).items():
                    platform, bot_id = key_str.split(":", 1)
                    self._bot_map[(platform, bot_id)] = name
                for key_str, settings in data.get("bot_settings", {}).items():
                    platform, bot_id = key_str.split(":", 1)
                    self._bot_settings[(platform, bot_id)] = settings
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                log.warning(
                    "JsonAgentStore.connect(): failed to load %s (%s) — starting empty",
                    self._path,
                    exc,
                )
                self._agents.clear()
                self._bot_map.clear()
                self._bot_settings.clear()
        self._connected = True
        log.debug("JsonAgentStore connected (path=%s)", self._path)

    async def close(self) -> None:
        """Clear in-memory state. Idempotent."""
        self._agents.clear()
        self._bot_map.clear()
        self._bot_settings.clear()
        self._connected = False
        log.debug("JsonAgentStore closed")

    # ------------------------------------------------------------------
    # Sync reads (mirror AgentStore interface exactly)
    # ------------------------------------------------------------------

    def get(self, name: str) -> AgentRow | None:
        """Return AgentRow for *name*, or None."""
        return self._agents.get(name)

    def get_all(self) -> list[AgentRow]:
        """Return all cached agents."""
        return list(self._agents.values())

    def get_bot_agent(self, platform: str, bot_id: str) -> str | None:
        """Return agent_name for (platform, bot_id), or None."""
        return self._bot_map.get((platform, bot_id))

    def get_all_bot_mappings(self) -> dict[tuple[str, str], str]:
        """Return a snapshot of all (platform, bot_id) → agent_name mappings."""
        return dict(self._bot_map)

    def get_bot_settings(self, platform: str, bot_id: str) -> dict:
        """Return parsed settings dict for (platform, bot_id), or empty dict."""
        return self._bot_settings.get((platform, bot_id), {})

    # ------------------------------------------------------------------
    # Async writes
    # ------------------------------------------------------------------

    async def upsert(self, row: AgentRow) -> None:
        """Insert or update an agent row in the in-memory store and persist."""
        self._agents[row.name] = row
        self._persist()

    async def delete(self, name: str) -> None:
        """Delete an agent. Raises ValueError if any bot is still assigned to it."""
        assigned = [
            f"{p}:{b}" for (p, b), n in self._bot_map.items() if n == name
        ]
        if assigned:
            raise ValueError(
                f"Agent {name!r} is still assigned to one or more bots. "
                "Run 'lyra agent unassign' first."
            )
        self._agents.pop(name, None)
        self._persist()

    async def set_bot_agent(
        self,
        platform: str,
        bot_id: str,
        agent_name: str,
        *,
        settings: dict | None = None,
    ) -> None:
        """Upsert a bot → agent mapping with optional settings.

        Passing ``settings=None`` does not clear existing settings (mirrors
        the COALESCE behaviour of :class:`AgentStore`).
        """
        self._bot_map[(platform, bot_id)] = agent_name
        if settings is not None:
            self._bot_settings[(platform, bot_id)] = settings
        self._persist()

    async def set_bot_settings(
        self, platform: str, bot_id: str, settings: dict
    ) -> None:
        """Update settings for an existing bot mapping.

        Raises ValueError if no mapping row exists (mirrors AgentStore).
        """
        if (platform, bot_id) not in self._bot_map:
            raise ValueError(
                f"No bot_agent_map row for platform={platform!r}, bot_id={bot_id!r}."
                " Call set_bot_agent() first."
            )
        self._bot_settings[(platform, bot_id)] = settings
        self._persist()

    async def remove_bot_agent(self, platform: str, bot_id: str) -> None:
        """Remove a bot → agent mapping. No-op if it does not exist."""
        self._bot_map.pop((platform, bot_id), None)
        self._bot_settings.pop((platform, bot_id), None)
        self._persist()

    # ------------------------------------------------------------------
    # Runtime state (not persisted — session-scoped)
    # ------------------------------------------------------------------

    async def get_all_runtime_states(self) -> dict[str, AgentRuntimeStateRow]:
        """Always returns an empty dict — runtime state is not tracked in tests."""
        return {}

    async def set_runtime_state(
        self, agent_name: str, status: str, pool_count: int = 0
    ) -> None:
        """Validate status; no-op (runtime state not persisted in test store)."""
        if status not in VALID_AGENT_STATUSES:
            raise ValueError(
                f"invalid status {status!r} — must be one of "
                f"{sorted(VALID_AGENT_STATUSES)}"
            )
        # No-op: JsonAgentStore does not track runtime state.

    # ------------------------------------------------------------------
    # TOML seeding (delegated to agent_seeder)
    # ------------------------------------------------------------------

    async def seed_from_toml(self, path: Path, *, force: bool = False) -> int:
        """Import agent from TOML. Delegates to :func:`agent_seeder.seed_from_toml`."""
        return await _seed_from_toml(self, path, force=force)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        """Serialize in-memory state to the JSON file.

        Raises :class:`OSError` on write failure (e.g. disk full, bad permissions)
        after logging a warning so the error context is not lost.
        """
        data: dict = {
            "agents": [dataclasses.asdict(row) for row in self._agents.values()],
            "bot_map": {
                f"{p}:{b}": name for (p, b), name in self._bot_map.items()
            },
            "bot_settings": {
                f"{p}:{b}": s for (p, b), s in self._bot_settings.items()
            },
        }
        try:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning(
                "JsonAgentStore._persist(): failed to write %s (%s) — in-memory state "
                "is intact but will not survive reconnect",
                self._path,
                exc,
            )
            raise
