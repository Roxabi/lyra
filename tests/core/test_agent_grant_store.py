"""AgentGrantStore — fail-safe authorization, write-through cache, persistence.

ADR-090 slice 1 (Foundation). Exercises the agent-scoped grant matrix:
deny-by-default, user/role grants, hot-edit without restart, warm-from-DB on
reconnect, and capability isolation (``admin`` never implies ``use``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.core.auth import Capability, Principal, PrincipalKind
from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore
from tests.core.conftest import make_agent_grant_store

_AGENT = "lyra_default"
_USER = Principal(kind=PrincipalKind.USER, id="tg:user:7377831990")
_ROLE = Principal(kind=PrincipalKind.ROLE, id="dc:role:42")


def test_principal_rejects_empty_id() -> None:
    """A Principal with an empty id is rejected at construction (fail-safe)."""
    with pytest.raises(ValueError, match="non-empty"):
        Principal(kind=PrincipalKind.USER, id="")


class TestAgentGrantStoreAuthorize:
    """authorize() — fail-safe deny plus user/role grant matching."""

    async def test_unknown_agent_denies(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            decision = store.authorize(agent_name="ghost", user_id=_USER.id)
            assert not decision
            assert not decision.allowed
            assert "ghost" in decision.reason
        finally:
            await store.close()

    async def test_known_agent_unknown_principal_denies(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            decision = store.authorize(agent_name=_AGENT, user_id="tg:user:999")
            assert not decision.allowed
        finally:
            await store.close()

    async def test_grant_user_then_authorize_allows(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            decision = store.authorize(agent_name=_AGENT, user_id=_USER.id)
            assert decision.allowed
            assert _USER.id in decision.reason
        finally:
            await store.close()

    async def test_role_grant_allows_when_role_present(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _ROLE, granted_by="op", source="cli")
            # The user themselves has no grant — only their role does.
            decision = store.authorize(
                agent_name=_AGENT, user_id="dc:user:1", roles=[_ROLE.id]
            )
            assert decision.allowed
        finally:
            await store.close()

    async def test_allows_when_any_of_multiple_roles_granted(
        self, tmp_path: Path
    ) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _ROLE, granted_by="op", source="cli")
            decision = store.authorize(
                agent_name=_AGENT,
                user_id="dc:user:1",
                roles=["dc:role:1", "dc:role:2", _ROLE.id],
            )
            assert decision.allowed
        finally:
            await store.close()

    async def test_grants_are_agent_scoped(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            # Same principal, a different agent → still denied (per-tenant matrix).
            decision = store.authorize(agent_name="other_agent", user_id=_USER.id)
            assert not decision.allowed
        finally:
            await store.close()

    async def test_admin_grant_does_not_satisfy_use(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(
                _AGENT,
                _USER,
                capability=Capability.ADMIN,
                granted_by="op",
                source="cli",
            )
            # authorize() checks USE only — an admin grant must not imply use (§1).
            decision = store.authorize(agent_name=_AGENT, user_id=_USER.id)
            assert not decision.allowed
        finally:
            await store.close()

    async def test_user_id_does_not_match_role_grant(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _ROLE, granted_by="op", source="cli")
            # Matching is kind-aware: a ROLE grant must not authorize a user
            # whose id happens to equal the role id passed as user_id.
            decision = store.authorize(agent_name=_AGENT, user_id=_ROLE.id)
            assert not decision.allowed
        finally:
            await store.close()

    async def test_role_does_not_match_user_grant(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            # Symmetric: a USER grant must not be satisfied by a matching role id.
            decision = store.authorize(
                agent_name=_AGENT, user_id="dc:user:0", roles=[_USER.id]
            )
            assert not decision.allowed
        finally:
            await store.close()

    async def test_authorize_before_connect_denies(self, tmp_path: Path) -> None:
        # authorize() is a pure cache read — before connect() the cache is empty,
        # so it must deny rather than raise. Locks in the fail-safe contract.
        store = AgentGrantStore(db_path=str(tmp_path / "auth.db"))
        assert not store.authorize(agent_name=_AGENT, user_id=_USER.id).allowed


class TestAgentGrantStoreWrites:
    """grant() / revoke() — write-through cache, idempotency, persistence."""

    async def test_grant_takes_effect_without_reconnect(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            assert not store.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            # Hot-edit: no reconnect, the cache reflects the new grant immediately.
            assert store.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
        finally:
            await store.close()

    async def test_revoke_then_authorize_denies(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            assert store.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
            existed = await store.revoke(_AGENT, _USER)
            assert existed is True
            assert not store.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
        finally:
            await store.close()

    async def test_revoke_missing_returns_false(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            assert await store.revoke(_AGENT, _USER) is False
        finally:
            await store.close()

    async def test_grant_is_idempotent_and_updates_source(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            first = await store.grant(_AGENT, _USER, granted_by="op1", source="cli")
            updated = await store.grant(
                _AGENT, _USER, granted_by="op2", source="config"
            )
            grants = store.list_grants(_AGENT)
            assert len(grants) == 1  # ON CONFLICT updates in place, no duplicate
            assert updated.granted_by == "op2"
            assert updated.source == "config"
            # ON CONFLICT omits created_at from the UPDATE → original is preserved.
            assert updated.created_at == first.created_at
        finally:
            await store.close()

    async def test_user_and_role_with_same_id_coexist(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            # principal_kind is part of the UNIQUE key, so a USER and a ROLE that
            # share an id string are distinct rows (no silent kind clobber).
            shared = "shared:42"
            await store.grant(
                _AGENT,
                Principal(PrincipalKind.USER, shared),
                granted_by="op",
                source="cli",
            )
            await store.grant(
                _AGENT,
                Principal(PrincipalKind.ROLE, shared),
                granted_by="op",
                source="cli",
            )
            grants = store.list_grants(_AGENT)
            assert len(grants) == 2
            assert {g.principal.kind for g in grants} == {
                PrincipalKind.USER,
                PrincipalKind.ROLE,
            }
        finally:
            await store.close()

    async def test_revoke_is_capability_scoped(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            # Revoking a different capability must not touch the USE grant.
            assert (
                await store.revoke(_AGENT, _USER, capability=Capability.ADMIN) is False
            )
            assert store.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
            assert await store.revoke(_AGENT, _USER, capability=Capability.USE) is True
            assert not store.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
        finally:
            await store.close()

    async def test_grant_raises_if_row_missing_after_reload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            # Force the cache-consistency guard: a reload that drops the row makes
            # the post-write lookup fail, proving the RuntimeError is a real guard.
            async def _noop(_agent: str) -> None:
                return None

            monkeypatch.setattr(store, "_reload_agent", _noop)
            with pytest.raises(RuntimeError, match="missing after write"):
                await store.grant(_AGENT, _USER, granted_by="op", source="cli")
        finally:
            await store.close()

    async def test_grant_rejects_empty_agent_name(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            with pytest.raises(ValueError, match="agent_name"):
                await store.grant("", _USER, granted_by="op", source="cli")
        finally:
            await store.close()

    async def test_grant_rejects_empty_audit_fields(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            with pytest.raises(ValueError, match="granted_by and source"):
                await store.grant(_AGENT, _USER, granted_by="", source="cli")
            with pytest.raises(ValueError, match="granted_by and source"):
                await store.grant(_AGENT, _USER, granted_by="op", source="")
        finally:
            await store.close()

    async def test_revoke_rejects_empty_agent_name(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            with pytest.raises(ValueError, match="agent_name"):
                await store.revoke("", _USER)
        finally:
            await store.close()

    async def test_writes_require_connect(self, tmp_path: Path) -> None:
        store = AgentGrantStore(db_path=str(tmp_path / "auth.db"))
        with pytest.raises(RuntimeError, match="connect"):
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")

    async def test_write_after_close_raises(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        await store.close()
        # Both write verbs share the _require_db() guard — assert each explicitly.
        with pytest.raises(RuntimeError, match="connect"):
            await store.revoke(_AGENT, _USER)
        with pytest.raises(RuntimeError, match="connect"):
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")

    async def test_double_close_is_safe(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        await store.close()
        await store.close()  # idempotent — must not raise

    async def test_grant_returns_persisted_grant(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            grant = await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            assert grant.agent_name == _AGENT
            assert grant.principal == _USER
            assert grant.capability is Capability.USE
            assert grant.created_at.tzinfo is not None  # UTC-aware
        finally:
            await store.close()

    async def test_grants_persist_across_reconnect(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
        finally:
            await store.close()
        # Reopen the same auth.db file. Construct manually to assert the cache is
        # empty before connect() — the final assertions can only pass if connect()
        # truly warms from disk (not a leaked handle).
        reopened = AgentGrantStore(db_path=str(tmp_path / "auth.db"))
        assert reopened._cache == {}
        await reopened.connect()
        try:
            assert reopened.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
            assert len(reopened.list_grants(_AGENT)) == 1
        finally:
            await reopened.close()


class TestAgentGrantStoreListing:
    """list_grants() — synchronous cache view for the operator CLI."""

    async def test_list_grants_empty_for_unknown_agent(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            assert store.list_grants("nobody") == ()
        finally:
            await store.close()

    async def test_list_grants_returns_all_for_agent(self, tmp_path: Path) -> None:
        store = await make_agent_grant_store(tmp_path)
        try:
            await store.grant(_AGENT, _USER, granted_by="op", source="cli")
            await store.grant(_AGENT, _ROLE, granted_by="op", source="cli")
            grants = store.list_grants(_AGENT)
            assert len(grants) == 2
            assert {g.principal for g in grants} == {_USER, _ROLE}
        finally:
            await store.close()


class TestAgentGrantStoreCohabitation:
    """ADR-090 §3 — agent_grants shares auth.db with AuthStore's grants table."""

    async def test_cohabits_one_auth_db_with_auth_store(self, tmp_path: Path) -> None:
        # Sequential opens (one connection at a time) keep this focused on the §3
        # invariant — schema cohabitation in one file — not on multi-connection WAL
        # write concurrency, which is a shared SqliteStore-base concern.
        from factory.core.auth import TrustLevel
        from factory.infrastructure.stores.identity.auth_store import AuthStore

        db = str(tmp_path / "auth.db")

        grant_store = AgentGrantStore(db_path=db)
        await grant_store.connect()
        try:
            await grant_store.grant(_AGENT, _USER, granted_by="op", source="cli")
        finally:
            await grant_store.close()

        # AuthStore opens the SAME file: its `grants` DDL coexists with agent_grants.
        auth_store = AuthStore(db_path=db)
        await auth_store.connect()
        try:
            await auth_store.upsert(
                "tg:user:1", TrustLevel.OWNER, None, "config", "config.toml"
            )
            assert auth_store.check("tg:user:1") == TrustLevel.OWNER
        finally:
            await auth_store.close()

        # The agent grant survived AuthStore's cohabitation untouched.
        reopened = AgentGrantStore(db_path=db)
        await reopened.connect()
        try:
            assert reopened.authorize(agent_name=_AGENT, user_id=_USER.id).allowed
            assert len(reopened.list_grants(_AGENT)) == 1
        finally:
            await reopened.close()
