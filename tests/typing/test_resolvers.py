"""AC8 verification — resolvers are module-level functions (import-only test)."""

from factory.transport.work_scope import WorkScope


def test_discord_resolver_is_module_level() -> None:
    from factory.adapters.discord.adapter import _discord_scope_resolver

    assert callable(_discord_scope_resolver)
    assert (
        _discord_scope_resolver(
            WorkScope(platform="discord", bot_id="x", scope_id=12345, trace_id="t")
        )
        == 12345
    )


def test_telegram_resolver_is_module_level() -> None:
    from factory.adapters.telegram.telegram import _telegram_scope_resolver

    assert callable(_telegram_scope_resolver)
    # Group/supergroup chat: negative chat_id (-100xxx).
    assert (
        _telegram_scope_resolver(
            WorkScope(platform="telegram", bot_id="x", scope_id=-100123, trace_id="t")
        )
        == -100123
    )
    # Private chat: positive chat_id.
    assert (
        _telegram_scope_resolver(
            WorkScope(platform="telegram", bot_id="x", scope_id=12345, trace_id="t")
        )
        == 12345
    )
