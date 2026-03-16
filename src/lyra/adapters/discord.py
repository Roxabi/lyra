from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import discord

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from lyra.adapters import discord_audio
from lyra.adapters._shared import (
    ATTACHMENT_EXTS_BASE,
    AUDIO_MIME_TYPES,
    DISCORD_MAX_LENGTH,
    push_to_hub_guarded,
    resolve_msg,
)
from lyra.adapters.discord_formatting import (
    _extract_attachments,
    _make_thread_name,
    render_buttons,
    render_text,
)
from lyra.adapters.discord_threads import (
    persist_thread_claim,
    persist_thread_session,
    restore_hot_threads,
    retrieve_thread_session,
)
from lyra.adapters.discord_voice import (
    VoiceAlreadyActiveError,
    VoiceDependencyError,
    VoiceMode,
    VoiceSessionManager,
)
from lyra.core.auth import AuthMiddleware, TrustLevel
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.command_parser import CommandParser
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
    RoutingContext,
)
from lyra.core.messages import MessageManager
from lyra.core.thread_store import ThreadStore

# Discord: same base extensions, no platform-specific additions needed.
_ATTACHMENT_EXTS = ATTACHMENT_EXTS_BASE

log = logging.getLogger(__name__)

_command_parser = CommandParser()

# Sentinel used when no AuthMiddleware is provided — denies all traffic by default.
_DENY_ALL = AuthMiddleware(store=None, role_map={}, default=TrustLevel.BLOCKED)

# Permissive sentinel for use in tests — allows all traffic as PUBLIC.
_ALLOW_ALL = AuthMiddleware(store=None, role_map={}, default=TrustLevel.PUBLIC)


_AUTO_THREAD_TRUE = frozenset({"1", "true", "yes", "on"})


async def _discord_typing_worker(
    resolve_channel: Callable,
    channel_id: int,
) -> None:
    """Hold Discord typing indicator for channel_id until cancelled.

    Sends trigger_typing() every 9 s (Discord expires after ~10 s). Uses a
    manual loop instead of channel.typing() to avoid the built-in 5 s refresh
    which triggers 429s when many conversations run in parallel.
    """
    try:
        # Retry: newly-created threads may not be cached immediately.
        channel = None
        for _attempt in range(3):
            try:
                channel = await resolve_channel(channel_id)
                break
            except Exception:
                if _attempt == 2:
                    raise
                await asyncio.sleep(1.0 * (2**_attempt))
        assert channel is not None  # guaranteed by the loop above
        while True:
            await channel.trigger_typing()
            await asyncio.sleep(9)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.debug("discord typing worker for channel %d: %s", channel_id, exc)


async def _discord_send_with_retry(
    coro_fn: Callable[[], Any],
    *,
    label: str,
    max_attempts: int = 3,
) -> None:
    """Call *coro_fn()* and retry with exponential backoff (1 s, 2 s, 4 s …)."""
    for attempt in range(max_attempts):
        try:
            await coro_fn()
            return
        except Exception:
            if attempt == max_attempts - 1:
                log.exception("%s failed after %d attempts", label, max_attempts)
                return
            delay = 2**attempt  # 1 s, 2 s, 4 s …
            log.warning(
                "%s failed (attempt %d/%d), retrying in %d s",
                label,
                attempt + 1,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)


@dataclass(frozen=True)
class DiscordConfig:
    token: str = field(repr=False)
    auto_thread: bool = True


def load_discord_config() -> DiscordConfig:
    """Load Discord configuration from environment variables.

    Raises SystemExit if DISCORD_TOKEN is absent. Never logs the token.
    """
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: DISCORD_TOKEN")
    auto_thread_str = os.environ.get("DISCORD_AUTO_THREAD", "").strip().lower()
    auto_thread = auto_thread_str in _AUTO_THREAD_TRUE if auto_thread_str else True
    return DiscordConfig(token=token, auto_thread=auto_thread)


class DiscordAdapter(discord.Client):
    """Discord channel adapter — discord.py v2 Gateway mode.

    Security contract:
    - Never logs the bot token.
    - All inbound messages produce trust='user' via Message.from_adapter().
    - Bot's own messages are silently discarded.
    """

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        hub: "Hub",
        bot_id: str = "main",
        *,
        intents: discord.Intents | None = None,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        auto_thread: bool = True,
        thread_hot_hours: int = 36,
        auth: AuthMiddleware = _DENY_ALL,
        thread_store: ThreadStore | None = None,
    ) -> None:
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
        super().__init__(intents=intents)
        self._hub = hub
        self._bot_id = bot_id
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._auto_thread = auto_thread
        self._thread_hot_hours = thread_hot_hours
        self._auth: AuthMiddleware = auth
        self._max_audio_bytes: int = int(
            os.environ.get("LYRA_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
        )
        self._typing_tasks: dict[int, asyncio.Task[None]] = {}
        self._bot_user: Any = None  # set on on_ready; None until login
        self._mention_re: re.Pattern[str] | None = None  # compiled on on_ready
        self._owned_threads: set[int] = set()  # populated from ThreadStore on on_ready
        self._thread_store: ThreadStore | None = thread_store
        self._thread_sessions: dict[str, tuple[str, str]] = {}
        self._vsm: VoiceSessionManager = VoiceSessionManager()

    def _msg(self, key: str, fallback: str) -> str:
        """Return a localised message string, falling back when no manager."""
        return resolve_msg(
            self._msg_manager, key, platform="discord", fallback=fallback
        )

    def _start_typing(self, send_to_id: int) -> None:
        """Start (or restart) the typing indicator background task for send_to_id."""
        existing = self._typing_tasks.pop(send_to_id, None)
        if existing and not existing.done():
            existing.cancel()
        self._typing_tasks[send_to_id] = asyncio.create_task(
            _discord_typing_worker(self._resolve_channel, send_to_id),
            name=f"typing:discord:{send_to_id}",
        )

    def _cancel_typing(self, send_to_id: int) -> None:
        """Cancel and remove the typing indicator task for send_to_id."""
        task = self._typing_tasks.pop(send_to_id, None)
        if task and not task.done():
            task.cancel()

    async def close(self) -> None:
        """Cancel pending typing tasks and drain voice sessions before closing."""
        tasks = list(self._typing_tasks.values())
        self._typing_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._vsm.leave_all()
        await super().close()

    async def on_ready(self) -> None:
        """Cache bot user and compile mention regex on login."""
        self._bot_user = self.user
        if self.user is not None:
            self._mention_re = re.compile(rf"<@!?{self.user.id}>")
        log.info(
            "Discord bot ready: %s (id=%s)",
            self.user,
            getattr(self.user, "id", "?"),
        )
        if not self.intents.message_content:
            log.warning(
                "message_content intent is disabled — "
                "guild message content will be empty. "
                "Enable 'Message Content Intent' in the Developer Portal."
            )
        # Restore hot threads from ThreadStore on startup.
        if self._thread_store is not None:
            try:
                self._owned_threads = await restore_hot_threads(
                    self._thread_store, self._bot_id, self._thread_hot_hours
                )
            except Exception:
                log.exception("ThreadStore: failed to restore owned threads")

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Invalidate stale voice session when the bot is forcibly disconnected."""
        bot_user = self._bot_user
        if bot_user is None or member.id != bot_user.id or after.channel is not None:
            return
        # member.guild is always set for voice state events (guild-only, no DM voice).
        guild_id = str(member.guild.id)
        self._vsm.invalidate(guild_id)

    async def _reply_safe(self, message: Any, text: str, *, label: str) -> None:
        """Send a reply, logging a warning on failure."""
        try:
            await message.reply(text)
        except Exception as exc:
            log.warning(
                "Failed to send %s reply for message_id=%s: %s",
                label,
                message.id,
                exc,
            )

    async def _handle_leave_command(self, message: Any, guild_id: str) -> None:
        """Execute !leave: disconnect if active, reply with outcome."""
        log.info(
            "voice_cmd cmd=leave user=%s guild=%s",
            getattr(message.author, "id", "?"),
            guild_id,
        )
        if self._vsm.get(guild_id) is None:
            await self._reply_safe(
                message, "I'm not in a voice channel.", label="not-in-channel"
            )
        else:
            await self._vsm.leave(guild_id)
            await self._reply_safe(message, "Left the voice channel.", label="leave")

    async def _handle_join_command(
        self,
        message: Any,
        guild: Any,
        args: str,
        trust: TrustLevel = TrustLevel.TRUSTED,
    ) -> None:
        """Execute !join / !join stay: connect to user's voice channel."""
        voice_state = getattr(message.author, "voice", None)
        if voice_state is None or voice_state.channel is None:
            await self._reply_safe(
                message, "Join a voice channel first.", label="not-in-voice"
            )
            return
        mode = (
            VoiceMode.PERSISTENT
            if args.strip().lower().split()[:1] == ["stay"]
            else VoiceMode.TRANSIENT
        )
        if mode == VoiceMode.PERSISTENT and trust < TrustLevel.TRUSTED:
            await self._reply_safe(
                message,
                "Persistent mode requires elevated permissions.",
                label="persistent-denied",
            )
            mode = VoiceMode.TRANSIENT
        try:
            await self._vsm.join(guild, voice_state.channel, mode)
        except VoiceAlreadyActiveError:
            await self._reply_safe(
                message, "Already in a voice channel.", label="already-active"
            )
        except VoiceDependencyError as exc:
            log.error("Voice dependency error on join: %s", exc)
            await self._reply_safe(
                message, "Voice is not available right now.", label="voice-unavailable"
            )

    async def _handle_voice_command(
        self, message: Any, trust: TrustLevel = TrustLevel.TRUSTED
    ) -> bool:
        """Detect and handle !join / !join stay / !leave voice commands.

        Returns True if a voice command was handled (caller should return early).
        Returns False if the message is not a voice command.
        Both ! and / prefixes are accepted (CommandParser handles both).
        Voice commands are guild-only; callers must not invoke for DMs.
        """
        cmd = _command_parser.parse(message.content.strip())
        if cmd is None or cmd.name not in ("join", "leave"):
            return False
        guild = message.guild
        guild_id = str(guild.id)
        if cmd.name == "leave":
            if trust < TrustLevel.TRUSTED:
                await self._reply_safe(
                    message,
                    "You don't have permission to use this command.",
                    label="leave-denied",
                )
                return True
            await self._handle_leave_command(message, guild_id)
        else:
            await self._handle_join_command(message, guild, cmd.args, trust=trust)
        return True

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
    ) -> InboundAudio:
        """Build an InboundAudio envelope from a Discord audio message."""
        return discord_audio.normalize_audio(
            raw,
            audio_bytes,
            mime_type,
            bot_id=self._bot_id,
            trust_level=trust_level,
        )

    def normalize(
        self,
        raw: Any,
        *,
        thread_id: int | None = None,
        channel_id: int | None = None,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
    ) -> InboundMessage:
        """Convert a discord.py Message (or SimpleNamespace) to InboundMessage."""
        is_mention = self._bot_user is not None and self._bot_user in raw.mentions

        # Strip @mention prefix so content reaches the agent clean
        text = raw.content
        if is_mention:
            if self._mention_re is None and self._bot_user is not None:
                self._mention_re = re.compile(rf"<@!?{self._bot_user.id}>")
        if is_mention and self._mention_re:
            text = self._mention_re.sub("", text).strip()

        # Resolve channel routing (pre-resolved by on_message after thread)
        resolved_channel_id: int = (
            channel_id if channel_id is not None else raw.channel.id
        )
        resolved_thread_id: int | None = thread_id

        is_thread = isinstance(raw.channel, discord.Thread)

        # If no override, check if already in a thread
        if resolved_thread_id is None and is_thread:
            resolved_thread_id = raw.channel.id

        scope_id = (
            f"thread:{resolved_thread_id}"
            if resolved_thread_id
            else f"channel:{resolved_channel_id}"
        )

        # Detect channel type
        channel_type: str = "text"
        if is_thread:
            channel_type = "thread"
        elif isinstance(raw.channel, discord.ForumChannel):
            channel_type = "forum"
        elif isinstance(raw.channel, discord.VoiceChannel):
            channel_type = "voice"

        timestamp = raw.created_at
        user_id = f"dc:user:{raw.author.id}"

        log.debug(
            "Normalizing discord message id=%s from user_id=%s",
            raw.id,
            user_id,
        )

        _display_name = getattr(raw.author, "display_name", None)
        attachments = _extract_attachments(getattr(raw, "attachments", None) or [])
        _reference = getattr(raw, "reference", None)
        reply_to_id: str | None = (
            str(_reference.message_id)
            if _reference is not None and _reference.message_id is not None
            else None
        )
        platform_meta = {
            "guild_id": raw.guild.id if raw.guild else None,
            "channel_id": resolved_channel_id,
            # INVARIANT: always original message id, never thread.id
            "message_id": raw.id,
            "thread_id": resolved_thread_id,
            "channel_type": channel_type,
        }
        routing = RoutingContext(
            platform=Platform.DISCORD.value,
            bot_id=self._bot_id,
            scope_id=scope_id,
            thread_id=(
                str(resolved_thread_id) if resolved_thread_id is not None else None
            ),
            reply_to_message_id=str(raw.id),
            platform_meta=dict(platform_meta),
        )
        return InboundMessage(
            id=(f"discord:{user_id}:{int(timestamp.timestamp())}:{raw.id}"),
            platform=Platform.DISCORD.value,
            bot_id=self._bot_id,
            scope_id=scope_id,
            user_id=user_id,
            user_name=(_display_name if _display_name is not None else raw.author.name),
            is_mention=is_mention,
            text=text,
            text_raw=raw.content,
            attachments=attachments,
            timestamp=timestamp,
            trust="user",
            trust_level=trust_level,
            platform_meta=platform_meta,
            routing=routing,
            reply_to_id=reply_to_id,
        )

    async def on_message(self, message: Any) -> None:  # noqa: C901, PLR0915 — gateway dispatch: each message type branch is independent
        """Handle incoming Gateway message.

        Filters own/bot messages, creates auto-thread before normalization,
        applies backpressure, and enqueues to hub bus.
        """
        # Discard bot messages early — before normalization to avoid waste.
        if message.author.bot:
            return

        # Auth gate — runs before normalize() and before audio handling.
        _raw_uid = str(message.author.id)
        roles = (
            [str(r.id) for r in message.author.roles]
            if hasattr(message.author, "roles")
            else []
        )
        trust = self._auth.check(_raw_uid, roles=roles)
        if trust == TrustLevel.BLOCKED:
            log.info(
                "auth_reject user=%s channel=discord", f"dc:user:{message.author.id}"
            )
            return

        # Audio attachment detection
        audio_attachment = next(
            (
                a
                for a in (getattr(message, "attachments", None) or [])
                if getattr(a, "content_type", "") in AUDIO_MIME_TYPES
            ),
            None,
        )
        if audio_attachment is not None:
            user_id = f"dc:user:{message.author.id}"
            log.info(
                "audio_received",
                extra={
                    "platform": "discord",
                    "user_id": user_id,
                    "message_id": message.id,
                },
            )
            # Pre-download size check (matches Telegram's _download_audio guard)
            att_size = getattr(audio_attachment, "size", None)
            if att_size is None or att_size > self._max_audio_bytes:
                log.warning(
                    "Audio attachment rejected: %d bytes exceeds %d byte limit"
                    " (message_id=%s)",
                    att_size,
                    self._max_audio_bytes,
                    message.id,
                )
                try:
                    await message.reply(
                        self._msg(
                            "audio_too_large",
                            "That audio file is too large to process.",
                        )
                    )
                except Exception:
                    log.warning(
                        "Failed to send audio-too-large reply for message_id=%s",
                        message.id,
                    )
                return

            try:
                audio_bytes = await audio_attachment.read()
            except Exception:
                log.exception(
                    "Failed to download audio attachment for message_id=%s",
                    message.id,
                )
                return

            # Magic-byte check: client-supplied content_type is untrusted.
            if not discord_audio.is_valid_audio_magic(audio_bytes):
                log.warning(
                    "Audio attachment rejected: magic bytes do not match any known"
                    " audio format (message_id=%s)",
                    message.id,
                )
                try:
                    await message.reply(
                        self._msg(
                            "audio_invalid_format",
                            "That file does not appear to be a valid audio file.",
                        )
                    )
                except Exception:
                    log.warning(
                        "Failed to send invalid-format reply for message_id=%s",
                        message.id,
                    )
                return

            # Gate: only process audio in DMs, direct mentions, or owned threads.
            _audio_is_dm = message.guild is None
            _audio_is_thread = isinstance(message.channel, discord.Thread)
            _audio_in_owned_thread = (
                _audio_is_thread and message.channel.id in self._owned_threads
            )
            _audio_is_mention = (
                self._bot_user is not None and self._bot_user in message.mentions
            )
            # Cold-path lazy check (same as text path).
            if (
                not _audio_is_dm
                and not _audio_is_mention
                and not _audio_in_owned_thread
                and _audio_is_thread
                and self._thread_store is not None
            ):
                try:
                    if await self._thread_store.is_owned(
                        str(message.channel.id), self._bot_id
                    ):
                        self._owned_threads.add(message.channel.id)
                        _audio_in_owned_thread = True
                except Exception:
                    log.warning(
                        "ThreadStore: lazy is_owned (audio) failed for thread_id=%s",
                        message.channel.id,
                    )
            if not _audio_is_dm and not _audio_is_mention and not _audio_in_owned_thread:  # noqa: E501
                return

            hub_audio = self.normalize_audio(
                message,
                audio_bytes=audio_bytes,
                mime_type=getattr(audio_attachment, "content_type", "audio/ogg"),
                trust_level=trust,
            )

            async def _send_bp(text: str) -> None:
                await message.reply(text)

            self._start_typing(message.channel.id)
            await push_to_hub_guarded(
                inbound_bus=self._hub.inbound_audio_bus,
                platform=Platform.DISCORD,
                msg=hub_audio,
                circuit_registry=self._circuit_registry,
                on_drop=lambda: self._cancel_typing(message.channel.id),
                send_backpressure=_send_bp,
                get_msg=self._msg,
            )
            return  # audio messages handled separately; skip text path

        # Voice command dispatch — guild-only; runs before mention/DM filter.
        if message.guild is not None:
            if await self._handle_voice_command(message, trust):
                return

        # Pre-detect mention (needed for auto-thread decision)
        _is_mention = self._bot_user is not None and self._bot_user in message.mentions

        # In DMs (no guild), always respond.
        # In servers: only respond when directly mentioned or in an owned thread.
        _is_dm = message.guild is None
        _is_thread = isinstance(message.channel, discord.Thread)
        _in_owned_thread = _is_thread and message.channel.id in self._owned_threads

        # Cold-path lazy check: thread not in hot set, query DB and warm cache on hit.
        if (
            not _is_dm
            and not _is_mention
            and not _in_owned_thread
            and _is_thread
            and self._thread_store is not None
        ):
            try:
                if await self._thread_store.is_owned(
                    str(message.channel.id), self._bot_id
                ):
                    self._owned_threads.add(message.channel.id)
                    _in_owned_thread = True
            except Exception:
                log.warning(
                    "ThreadStore: lazy is_owned check failed for thread_id=%s",
                    message.channel.id,
                )

        if not _is_dm and not _is_mention and not _in_owned_thread:
            return

        # Auto-thread creation BEFORE normalize() (frozen dataclass)
        resolved_thread_id: int | None = None
        resolved_channel_id: int = message.channel.id
        if (
            self._auto_thread
            and _is_mention
            and not isinstance(message.channel, discord.Thread)
            and hasattr(message.channel, "create_thread")
        ):
            try:
                thread = await message.create_thread(
                    name=_make_thread_name(message.content, message.author.display_name)
                )
                resolved_thread_id = thread.id
                self._owned_threads.add(thread.id)
                if self._thread_store is not None:
                    asyncio.ensure_future(
                        persist_thread_claim(
                            self._thread_store,
                            thread_id=thread.id,
                            bot_id=self._bot_id,
                            channel_id=message.channel.id,
                            guild_id=getattr(message.guild, "id", None),
                        )
                    )
            except Exception:
                log.exception(
                    "Failed to create Discord thread for message id=%s",
                    message.id,
                )

        # Claim an existing thread when directly mentioned inside it.
        if _is_mention and isinstance(message.channel, discord.Thread):
            self._owned_threads.add(message.channel.id)
            if self._thread_store is not None:
                asyncio.ensure_future(
                    persist_thread_claim(
                        self._thread_store,
                        thread_id=message.channel.id,
                        bot_id=self._bot_id,
                        channel_id=getattr(
                            message.channel, "parent_id", message.channel.id
                        ),
                        guild_id=getattr(message.guild, "id", None),
                    )
                )

        # Retrieve stored session for existing owned threads (read-side fix).
        # New auto-threads have no prior session; skip get_session() for those.
        _stored_session_id: str | None = None
        _stored_pool_id: str | None = None
        if _in_owned_thread and self._thread_store is not None:
            try:
                _stored_session_id, _stored_pool_id = await retrieve_thread_session(
                    self._thread_store,
                    thread_id=str(message.channel.id),
                    bot_id=self._bot_id,
                    cache=self._thread_sessions,
                )
            except Exception:
                log.exception(
                    "ThreadStore: failed to retrieve session for thread_id=%s",
                    message.channel.id,
                )

        try:
            hub_msg = self.normalize(
                message,
                thread_id=resolved_thread_id,
                channel_id=resolved_channel_id,
                trust_level=trust,
            )
        except Exception:
            log.exception("Failed to normalize discord message id=%s", message.id)
            return

        # Inject stored session + persistence callback into platform_meta.
        _meta_updates: dict = {}
        if _stored_session_id is not None:
            _meta_updates["thread_session_id"] = _stored_session_id
        _has_thread_id = hub_msg.platform_meta.get("thread_id") is not None
        if _has_thread_id and self._thread_store is not None:
            _ts, _bid, _cache = self._thread_store, self._bot_id, self._thread_sessions

            async def _session_update_fn(
                msg: InboundMessage, session_id: str, pool_id: str
            ) -> None:
                await persist_thread_session(
                    _ts, msg, session_id, pool_id, _bid, _cache
                )

            _meta_updates["_session_update_fn"] = _session_update_fn
        if _meta_updates:
            hub_msg = dataclasses.replace(
                hub_msg,
                platform_meta={**hub_msg.platform_meta, **_meta_updates},
            )

        log.info(
            "message_received",
            extra={
                "platform": "discord",
                "user_id": hub_msg.user_id,
                "scope_id": hub_msg.scope_id,
                "msg_id": hub_msg.id,
            },
        )

        send_to_id: int = (
            resolved_thread_id
            if resolved_thread_id is not None
            else resolved_channel_id
        )
        self._start_typing(send_to_id)
        await self._push_to_hub(
            hub_msg,
            source_message=message,
            on_drop=lambda: self._cancel_typing(send_to_id),
        )

    async def _push_to_hub(
        self,
        hub_msg: InboundMessage,
        source_message: Any = None,
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        """Put hub_msg on the inbound bus with circuit-open and backpressure guards.

        on_drop is called before early return in both circuit-open and QueueFull
        cases. Always returns normally.
        """

        async def _send_bp(text: str) -> None:
            if source_message is not None:
                await source_message.reply(text)

        await push_to_hub_guarded(
            inbound_bus=self._hub.inbound_bus,
            platform=Platform.DISCORD,
            msg=hub_msg,
            circuit_registry=self._circuit_registry,
            on_drop=on_drop,
            send_backpressure=_send_bp,
            get_msg=self._msg,
        )

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable:
        """Get channel from cache or fetch from network."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return cast(discord.abc.Messageable, channel)

    async def send(  # noqa: C901 — attachment loop adds branches
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Send response back to Discord."""
        if original_msg.platform != Platform.DISCORD.value:
            log.error(
                "send() called with non-discord message id=%s",
                original_msg.id,
            )
            return

        channel_id: int | None = original_msg.platform_meta.get("channel_id")
        if channel_id is None:
            raise ValueError(
                "platform_meta missing required key 'channel_id' for send()"
            )
        thread_id: int | None = original_msg.platform_meta.get("thread_id")
        send_to_id: int = thread_id if thread_id is not None else channel_id
        messageable = await self._resolve_channel(send_to_id)

        text = outbound.to_text()
        chunks = render_text(text, DISCORD_MAX_LENGTH)
        view = render_buttons(outbound.buttons)
        last_idx = len(chunks) - 1

        # Skip reply-to in threads — thread context makes it redundant.
        reply_msg_id: int | None = original_msg.platform_meta.get("message_id")
        should_reply = reply_msg_id is not None and thread_id is None
        for i, chunk in enumerate(chunks):
            chunk_view = view if (i == last_idx and view is not None) else None
            if should_reply:
                msg_obj = messageable.get_partial_message(reply_msg_id)  # type: ignore[attr-defined]
                if chunk_view is not None:
                    sent = await msg_obj.reply(chunk, view=chunk_view)
                else:
                    sent = await msg_obj.reply(chunk)
            else:
                if chunk_view is not None:
                    sent = await messageable.send(chunk, view=chunk_view)
                else:
                    sent = await messageable.send(chunk)
            if i == last_idx:
                outbound.metadata["reply_message_id"] = sent.id
        self._cancel_typing(send_to_id)
        log.debug(
            "stored reply_message_id=%s for msg_id=%s",
            outbound.metadata.get("reply_message_id"),
            original_msg.id,
        )

    async def send_streaming(  # noqa: C901, PLR0915 — streaming protocol: edit/chunk/finalize branches are inherently sequential
        self,
        original_msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response with edit-in-place, debounced at ~1s."""
        if original_msg.platform != Platform.DISCORD.value:
            log.error(
                "send_streaming() called with non-discord message id=%s",
                original_msg.id,
            )
            return

        channel_id: int | None = original_msg.platform_meta.get("channel_id")
        if channel_id is None:
            raise ValueError(
                "platform_meta missing required key 'channel_id' for send_streaming()"
            )
        thread_id: int | None = original_msg.platform_meta.get("thread_id")
        send_to_id: int = thread_id if thread_id is not None else channel_id
        messageable = await self._resolve_channel(send_to_id)

        parts: list[str] = []

        # Send placeholder
        _placeholder_text = self._msg("stream_placeholder", "\u2026")
        reply_msg_id: int | None = original_msg.platform_meta.get("message_id")
        should_reply = reply_msg_id is not None and thread_id is None
        try:
            if should_reply:
                msg_obj = messageable.get_partial_message(reply_msg_id)  # type: ignore[attr-defined]
                placeholder = await msg_obj.reply(_placeholder_text)
            else:
                placeholder = await messageable.send(_placeholder_text)
            if outbound is not None:
                outbound.metadata["reply_message_id"] = placeholder.id
        except Exception:
            log.exception("Failed to send placeholder — falling back to non-streaming")
            async for chunk in chunks:
                parts.append(chunk)
            fallback_content = "".join(parts) or _placeholder_text
            fallback_outbound = OutboundMessage.from_text(fallback_content)
            await self.send(original_msg, fallback_outbound)
            if outbound is not None:
                outbound.metadata["reply_message_id"] = fallback_outbound.metadata.get(
                    "reply_message_id"
                )
            return

        last_edit = time.monotonic()
        stream_error: Exception | None = None
        try:
            async for chunk in chunks:
                parts.append(chunk)
                now = time.monotonic()
                if now - last_edit >= 1.0:
                    accumulated = "".join(parts)
                    await placeholder.edit(content=accumulated[:DISCORD_MAX_LENGTH])
                    last_edit = now
        except Exception as exc:
            stream_error = exc
            log.exception("Stream interrupted")

        accumulated = "".join(parts)
        if stream_error is not None:
            if accumulated:
                accumulated += self._msg(
                    "stream_interrupted", " [response interrupted]"
                )
            else:
                accumulated = self._msg("generic", GENERIC_ERROR_REPLY)

        # Final edit (always runs): edit placeholder with first chunk, send overflow.
        if accumulated:
            final_chunks = render_text(accumulated, DISCORD_MAX_LENGTH)
            await _discord_send_with_retry(
                lambda: placeholder.edit(content=final_chunks[0]),
                label="Final edit",
            )
            for extra_chunk in final_chunks[1:]:
                await _discord_send_with_retry(
                    lambda c=extra_chunk: messageable.send(c),
                    label="Overflow chunk",
                )

        # Cancel typing after final content is confirmed (streaming done).
        self._cancel_typing(send_to_id)

        # Re-raise stream error so OutboundDispatcher can record CB failure
        if stream_error is not None:
            raise stream_error

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Send an OutboundAudio envelope as a Discord voice message."""
        await discord_audio.render_audio(
            msg,
            inbound,
            bot_id=self._bot_id,
            resolve_channel=self._resolve_channel,
            http=self.http,
        )

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Send an OutboundAttachment envelope as a Discord file attachment."""
        await discord_audio.render_attachment(
            msg,
            inbound,
            resolve_channel=self._resolve_channel,
            attachment_exts=_ATTACHMENT_EXTS,
        )

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Buffer streamed audio chunks and send as a single Discord file attachment."""
        await discord_audio.render_audio_stream(chunks, inbound, self.render_audio)

    async def render_voice_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Route TTS stream to the active Discord voice session for this guild."""
        await discord_audio.render_voice_stream(chunks, inbound, self._vsm)
