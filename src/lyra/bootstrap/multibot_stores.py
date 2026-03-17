"""Store lifecycle helpers for multibot bootstrap."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from lyra.core.agent_store import AgentStore
from lyra.core.auth_store import AuthStore
from lyra.core.credential_store import CredentialStore, LyraKeyring
from lyra.core.message_index import MessageIndex
from lyra.core.prefs_store import PrefsStore
from lyra.core.thread_store import ThreadStore
from lyra.core.turn_store import TurnStore

log = logging.getLogger(__name__)


@dataclass
class StoreBundle:
    """All persistent stores needed by the multibot bootstrap."""

    auth: AuthStore
    cred: CredentialStore
    agent: AgentStore
    turn: TurnStore
    thread: ThreadStore
    prefs: PrefsStore
    message_index: MessageIndex


@asynccontextmanager
async def open_stores(vault_dir: Path) -> AsyncIterator[StoreBundle]:
    """Open every store, yield a *StoreBundle*, and close on exit.

    The finally block closes each store that was successfully opened,
    regardless of which later store (if any) failed to connect.
    """
    auth_store: AuthStore | None = None
    cred_store: CredentialStore | None = None
    agent_store: AgentStore | None = None
    turn_store: TurnStore | None = None
    thread_store: ThreadStore | None = None
    prefs_store: PrefsStore | None = None
    message_index_store: MessageIndex | None = None
    try:
        auth_store = AuthStore(db_path=vault_dir / "auth.db")
        await auth_store.connect()

        keyring = LyraKeyring.load_or_create(vault_dir / "keyring.key")
        cred_store = CredentialStore(
            db_path=vault_dir / "auth.db",
            keyring=keyring,
        )
        await cred_store.connect()

        agent_store = AgentStore(db_path=vault_dir / "auth.db")
        await agent_store.connect()

        turn_store = TurnStore(db_path=vault_dir / "turns.db")
        await turn_store.connect()

        thread_store = ThreadStore(db_path=vault_dir / "auth.db")
        await thread_store.connect()

        prefs_store = PrefsStore(db_path=vault_dir / "auth.db")
        await prefs_store.connect()

        message_index_store = MessageIndex(db_path=vault_dir / "message_index.db")
        await message_index_store.connect()

        yield StoreBundle(
            auth=auth_store,
            cred=cred_store,
            agent=agent_store,
            turn=turn_store,
            thread=thread_store,
            prefs=prefs_store,
            message_index=message_index_store,
        )
    finally:
        all_stores = (
            cred_store,
            auth_store,
            agent_store,
            turn_store,
            thread_store,
            prefs_store,
            message_index_store,
        )
        for store in all_stores:
            if store is not None:
                await store.close()
