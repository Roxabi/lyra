"""Store lifecycle helpers for multibot bootstrap.

#417 — auth.db split: AgentStore, PrefsStore now connect to config.db.
ThreadStore connects to discord.db (owned by the Discord adapter after S4 lands).
AuthStore remains on auth.db (grants only).
Bot tokens are read from /run/secrets (Podman secrets) per #1057.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

from nats.aio.client import Client

from factory.bootstrap.bootstrap_store_migrations import (
    _ensure_auth_db_schema,
    _ensure_config_db,
    _ensure_config_db_bot_migrations,
    _ensure_discord_db,
)
from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore
from factory.infrastructure.stores.identity.auth_store import AuthStore
from factory.infrastructure.stores.identity.identity_alias_store import IdentityAliasStore
from factory.infrastructure.stores.identity.user_store import UserStore
from factory.infrastructure.stores.kv.message_index_kv import (
    MessageIndexKvStore,
    ensure_kv,
)
from factory.infrastructure.stores.registry.agent_store import AgentStore
from factory.infrastructure.stores.registry.bot_store import BotStore
from factory.infrastructure.stores.registry.prefs_store import PrefsStore
from factory.infrastructure.stores.session.turn_store import TurnStore
from factory.paths import factory_turns_db_path


@dataclass
class StoreBundle:
    """All persistent stores needed by the multibot bootstrap.

    ThreadStore is NOT included — owned by the Discord adapter (#417 / S4).
    Bot tokens read from /run/secrets (Podman secrets) per #1057 — no credential store.
    """

    auth: AuthStore
    agent: AgentStore
    turn: TurnStore
    prefs: PrefsStore
    message_index: MessageIndexKvStore
    user: UserStore
    identity_alias: IdentityAliasStore
    grant: AgentGrantStore
    bot: BotStore


@asynccontextmanager
async def open_stores(
    vault_dir: Path, nc: Client | None = None
) -> AsyncGenerator[StoreBundle, None]:
    """Open every store, yield a *StoreBundle*, and close on exit.

    Runs the auth.db → config.db migration guard before opening stores (#417).
    The finally block closes each store that was successfully opened,
    regardless of which later store (if any) failed to connect.
    """
    _ensure_config_db(vault_dir)
    _ensure_discord_db(vault_dir)
    _ensure_auth_db_schema(vault_dir)
    await _ensure_config_db_bot_migrations(vault_dir)

    auth_store: AuthStore | None = None
    agent_store: AgentStore | None = None
    turn_store: TurnStore | None = None
    prefs_store: PrefsStore | None = None
    message_index_store: MessageIndexKvStore | None = None
    user_store: UserStore | None = None
    identity_alias_store: IdentityAliasStore | None = None
    grant_store: AgentGrantStore | None = None
    bot_store: BotStore | None = None
    try:
        auth_store = AuthStore(db_path=vault_dir / "auth.db")
        await auth_store.connect()

        user_store = UserStore(db_path=vault_dir / "auth.db")
        await user_store.connect()

        identity_alias_store = IdentityAliasStore(
            db_path=vault_dir / "auth.db",
            user_store=user_store,
        )
        await identity_alias_store.connect()

        grant_store = AgentGrantStore(
            db_path=vault_dir / "auth.db",
            user_store=user_store,
        )
        await grant_store.connect()

        agent_store = AgentStore(db_path=vault_dir / "config.db")
        await agent_store.connect()

        turns_db_path = factory_turns_db_path()
        turns_db_path.parent.mkdir(parents=True, exist_ok=True)
        turn_store = TurnStore(db_path=turns_db_path)
        await turn_store.connect()

        bot_store = BotStore(db_path=vault_dir / "config.db")
        await bot_store.connect()

        prefs_store = PrefsStore(db_path=vault_dir / "config.db")
        await prefs_store.connect()

        if nc is None:
            raise RuntimeError(
                "NATS connection (nc) is required for MessageIndexKvStore;"
                " message_index is no longer SQLite-backed (#1059)."
            )
        js = nc.jetstream()
        await ensure_kv(js)
        message_index_store = MessageIndexKvStore(js)
        await message_index_store.connect()

        yield StoreBundle(
            auth=auth_store,
            agent=agent_store,
            turn=turn_store,
            prefs=prefs_store,
            message_index=message_index_store,
            user=user_store,
            identity_alias=identity_alias_store,
            grant=grant_store,
            bot=bot_store,
        )
    finally:
        all_stores = (
            auth_store,
            agent_store,
            turn_store,
            bot_store,
            prefs_store,
            message_index_store,
            user_store,
            identity_alias_store,
            grant_store,
        )
        for store in all_stores:
            if store is not None:
                await store.close()