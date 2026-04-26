"""NatsBus — Bus[T] implementation over NATS pub/sub.

Each registered (platform, bot_id) pair maps to one NATS subscription.
Inbound messages are deserialized from JSON and placed into a staging queue.

Usage::

    nc = await nats.connect("nats://localhost:4222")
    bus: Bus[InboundMessage] = NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)
    bus.register(Platform.TELEGRAM)
    await bus.start()
    # ...
    await bus.stop()
    await nc.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Generic, TypeVar

from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

from lyra.core.messaging.message import (
    SCHEMA_VERSION_INBOUND_MESSAGE,
    InboundMessage,
    Platform,
)
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats import TypeHintResolver
from roxabi_nats._serialize import deserialize_dict, serialize
from roxabi_nats._validate import validate_nats_token
from roxabi_nats._version_check import check_schema_version

log = logging.getLogger(__name__)

T = TypeVar("T")

_ENVELOPE_VERSIONS: dict[type, tuple[str, int]] = {
    InboundMessage: ("InboundMessage", SCHEMA_VERSION_INBOUND_MESSAGE),
}


class NatsBus(Generic[T]):
    """Bus[T] implementation backed by NATS pub/sub.

    Caller manages the NATS connection; NatsBus only manages subscriptions.
    Registrations survive ``stop()`` — safe to restart without re-registering.

    Args:
        nc: Already-connected NATS client.
        bot_id: Default bot id for ``register()`` when no explicit one given.
        item_type: Concrete type for deserialization.
        subject_prefix: NATS subject prefix (default: ``"lyra.inbound"``).
        publish_only: If True, ``start()`` is no-op and ``get()`` raises.
    """

    def __init__(  # noqa: PLR0913
        self,
        nc: NATS,
        bot_id: str,
        item_type: type[T],
        subject_prefix: str = "lyra.inbound",
        *,
        staging_maxsize: int = 500,
        queue_group: str = "",
        publish_only: bool = False,
        resolver: TypeHintResolver = TYPE_REGISTRY_RESOLVER,
    ) -> None:
        validate_nats_token(subject_prefix, kind="subject_prefix")
        validate_nats_token(queue_group, kind="queue_group", allow_empty=True)
        self._nc = nc
        self._bot_id = bot_id
        self._item_type = item_type
        self._subject_prefix = subject_prefix
        self._queue_group = queue_group
        self._publish_only = publish_only
        self._resolver = resolver
        self._started = False
        self._registrations: set[tuple[Platform, str]] = set()
        self._subscriptions: dict[tuple[Platform, str], Subscription] = {}
        self._staging: asyncio.Queue[T] = asyncio.Queue(maxsize=staging_maxsize)
        self._version_mismatch_drops: dict[str, int] = {}

    # -- Registration --

    def register(
        self, platform: Platform, maxsize: int = 100, bot_id: str | None = None
    ) -> None:
        """Record *(platform, bot_id)* for subscription setup (idempotent)."""
        del maxsize  # NATS has no local buffer — Bus protocol slot
        if self._started:
            raise RuntimeError(f"Cannot register {platform!r} after start().")
        resolved_bid = bot_id or self._bot_id
        validate_nats_token(resolved_bid, kind="bot_id")
        self._registrations.add((platform, resolved_bid))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create one NATS subscription per registered (platform, bot_id) pair.

        Subject pattern: ``{subject_prefix}.{platform.value}.{bot_id}``.
        No-op if zero registrations or ``publish_only=True``.

        Raises:
            RuntimeError: If already started (double-start) — call stop() first.
        """
        if self._started:
            raise RuntimeError("NatsBus.start() called on an already-started bus.")
        self._started = True
        if self._publish_only:
            return
        for platform, bid in self._registrations:
            await self._make_handler(platform, bid)

    async def stop(self) -> None:
        """Unsubscribe all active NATS subscriptions.

        Registered (platform, bot_id) pairs are preserved so a subsequent
        ``start()`` succeeds. Publish-only buses have empty ``_subscriptions``
        (``start()`` returned early before populating it), so the loop below
        is a natural no-op — do not add teardown that bypasses this invariant.
        """
        for sub in self._subscriptions.values():
            try:
                await sub.unsubscribe()
            except Exception:
                log.exception("NatsBus: error unsubscribing")
        self._subscriptions.clear()
        self._started = False

    # ------------------------------------------------------------------
    # Message I/O
    # ------------------------------------------------------------------

    async def put(self, platform: Platform, item: T) -> None:
        """Serialize *item* and publish it to the platform's NATS subject.

        Uses the first matching registration's bot_id for the subject.

        Raises:
            KeyError: If *platform* has not been registered.
        """
        registered_platforms = frozenset(p for p, _ in self._registrations)
        if platform not in registered_platforms:
            raise KeyError(
                f"Platform {platform!r} is not registered — call register() first."
            )
        bid = next((b for p, b in self._registrations if p == platform), self._bot_id)
        subject = f"{self._subject_prefix}.{platform.value}.{bid}"
        payload = serialize(item)
        await self._nc.publish(subject, payload)

    async def get(self) -> T:
        """Wait for and return the next item from the staging queue."""
        if self._publish_only:
            raise RuntimeError("NatsBus.get(): publish-only bus never consumes")
        return await self._staging.get()

    def task_done(self) -> None:
        """No-op — NatsBus does not use ``asyncio.Queue.task_done()`` semantics."""
        return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def qsize(self, platform: Platform) -> int:
        """Always returns 0 — NatsBus has no per-platform local buffer."""
        del platform  # Bus protocol slot; NatsBus keeps no local buffer
        return 0

    def staging_qsize(self) -> int:
        """Return the number of items currently waiting in the staging queue."""
        return self._staging.qsize()

    def inject(self, item: T) -> None:
        """Public injection API for compat shims (bypasses NATS deserialize)."""
        try:
            self._staging.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("inject: staging queue full — item dropped")

    def registered_platforms(self) -> frozenset[Platform]:
        """Return the set of currently registered platforms."""
        return frozenset(p for p, _ in self._registrations)

    @property
    def subscription_count(self) -> int:
        """Return the number of active NATS subscriptions."""
        return len(self._subscriptions)

    def version_mismatch_count(self, envelope_name: str) -> int:
        """Cumulative drops for *envelope_name* — summed across all check kinds."""
        prefix = f"{envelope_name}:"
        return sum(
            count
            for key, count in self._version_mismatch_drops.items()
            if key.startswith(prefix)
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _make_handler(self, platform: Platform, bot_id: str) -> None:
        """Create NATS subscription for *(platform, bot_id)* and wire the handler."""
        subject = f"{self._subject_prefix}.{platform.value}.{bot_id}"

        async def handler(msg: Msg) -> None:
            await self._handle_nats_message(msg, platform, bot_id, subject)

        sub = await self._nc.subscribe(subject, queue=self._queue_group, cb=handler)
        self._subscriptions[(platform, bot_id)] = sub
        log.debug(
            "NatsBus subscribed: subject=%s bot_id=%s queue_group=%r",
            subject,
            bot_id,
            self._queue_group,
        )

    async def _handle_nats_message(
        self, msg: Msg, platform: Platform, bot_id: str, subject: str
    ) -> None:
        """Process a single NATS message and enqueue it."""
        try:
            payload = json.loads(msg.data.decode("utf-8"))
        except Exception:
            log.exception(
                "NatsBus: failed to parse JSON on platform=%s bot_id=%s",
                platform.value,
                bot_id,
            )
            return

        envelope_name, expected = _ENVELOPE_VERSIONS[self._item_type]
        if not check_schema_version(
            payload,
            envelope_name=envelope_name,
            expected=expected,
            subject=subject,
            counter=self._version_mismatch_drops,
        ):
            return  # helper already logged + incremented counter

        try:
            item = deserialize_dict(payload, self._item_type, resolver=self._resolver)
            self._staging.put_nowait(item)
        except asyncio.QueueFull:
            log.warning(
                "NatsBus staging queue full — dropping message on"
                " platform=%s bot_id=%s",
                platform.value,
                bot_id,
            )
        except Exception:
            log.exception(
                "NatsBus: failed to deserialize message on platform=%s bot_id=%s",
                platform.value,
                bot_id,
            )
