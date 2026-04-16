# Hub Extraction Plan — Implementation Guide

> Created: 2026-04-16
> Parent: `artifacts/analyses/refactoring-strategy-clean-arch.md`
> Status: Ready for execution

---

## Overview

Extract logic from `hub/hub.py` (791 lines) to achieve Clean Architecture compliance and reduce to ~410 lines of pure coordination code.

**Total reduction:** ~380 lines across 3 phases

---

## Phase 1: Identity Resolution

**Risk:** LOW
**Time estimate:** 1-2 hours
**LOC change:** -90 / +100

### Step 1.1: Create `hub/identity_resolver.py`

```python
"""Identity and binding resolution logic.

Extracted from Hub to enforce Clean Architecture:
- Pure functions (no I/O)
- No infrastructure dependencies
- Single responsibility: resolve identity/binding from inbound message
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..message import InboundMessage
from ..trust import TrustLevel
from .hub_protocol import Binding

if TYPE_CHECKING:
    from ..authenticator import Authenticator
    from ..identity import Identity
    from .hub_protocol import RoutingKey


class IdentityResolver:
    """Resolves authenticated identity and binding from inbound messages."""

    def __init__(
        self,
        authenticators: dict[RoutingKey, Authenticator],
        bindings: dict[RoutingKey, Binding],
    ):
        self._authenticators = authenticators
        self._bindings = bindings

    def resolve_identity(self, msg: InboundMessage) -> Identity:
        """Resolve authenticated identity for message sender."""
        # ... logic from Hub.resolve_identity

    def resolve_binding(self, msg: InboundMessage) -> Binding | None:
        """Resolve agent binding for message scope."""
        # ... logic from Hub.resolve_binding

    def _resolve_message_trust(self, msg: InboundMessage) -> InboundMessage:
        """Apply trust level based on authentication."""
        # ... logic from Hub._resolve_message_trust
```

### Step 1.2: Update `hub/hub.py`

**Remove methods:**
- `resolve_identity`
- `resolve_binding`
- `_resolve_message_trust`

**Add delegation:**
```python
def __init__(self, ...):
    # ...
    self._identity_resolver = IdentityResolver(
        authenticators=self._authenticators,
        bindings=self._bindings,
    )

def resolve_identity(self, msg: InboundMessage) -> Identity:
    return self._identity_resolver.resolve_identity(msg)

def resolve_binding(self, msg: InboundMessage) -> Binding | None:
    return self._identity_resolver.resolve_binding(msg)
```

### Step 1.3: Update Callers

| File | Change |
|------|--------|
| `hub/middleware_stages.py` | Import `IdentityResolver`, use via hub or inject directly |
| `bootstrap/bootstrap_wiring.py` | No change — still calls `hub.resolve_identity()` |
| `bootstrap/hub_standalone.py` | No change — still calls `hub.register_binding()` |

### Step 1.4: Update Tests

| Test File | Action |
|-----------|--------|
| `tests/unit/core/test_hub.py` | Verify delegation works |
| `tests/unit/core/hub/test_identity_resolver.py` | **NEW** — unit tests for resolver |

### Step 1.5: Verify

```bash
# Run tests
uv run pytest tests/unit/core/hub/ -v

# Check for circular imports
python -c "from lyra.core.hub import Hub; print('OK')"

# GitNexus detect changes
npx gitnexus detect-changes --scope staged
```

---

## Phase 2: TTS Helpers

**Risk:** MEDIUM
**Time estimate:** 1 hour
**LOC change:** -40 / +50

### Step 2.1: Extend `core/tts_dispatch.py`

**Move methods from Hub:**
- `_resolve_agent_tts(msg)` → `AudioPipeline._resolve_agent_tts()`
- `_tts_language_kwargs(msg)` → `AudioPipeline._tts_language_kwargs()`
- `_resolve_agent_fallback_language(msg)` → `AudioPipeline._resolve_agent_fallback_language()`

**Add to `AudioPipeline`:**
```python
class AudioPipeline:
    # Existing code...

    def resolve_agent_tts(self, msg: InboundMessage) -> AgentTTSConfig | None:
        """Resolve TTS config for the agent handling this message."""
        # ... logic from Hub._resolve_agent_tts

    def tts_language_kwargs(self, msg: InboundMessage) -> dict:
        """Build language kwargs for TTS call."""
        # ... logic from Hub._tts_language_kwargs
```

### Step 2.2: Update `hub/hub.py`

**Remove methods:**
- `_resolve_agent_tts`
- `_tts_language_kwargs`
- `_resolve_agent_fallback_language`

**Update calls:**
```python
# In dispatch_audio*, replace:
tts_config = self._resolve_agent_tts(msg)
# With:
tts_config = self._audio_pipeline.resolve_agent_tts(msg) if self._audio_pipeline else None
```

### Step 2.3: Update Callers

| File | Change |
|------|--------|
| `hub/middleware_stages.py` | Update TTS-related calls |
| `core/tts_dispatch.py` | Integrate new methods |

### Step 2.4: Update Tests

| Test File | Action |
|-----------|--------|
| `tests/unit/core/test_tts_dispatch.py` | Add tests for new methods |
| `tests/unit/core/test_hub.py` | Remove tests for moved methods |

### Step 2.5: Verify

```bash
uv run pytest tests/unit/core/test_tts_dispatch.py -v
python -c "from lyra.core.tts_dispatch import AudioPipeline; print('OK')"
```

---

## Phase 3: Outbound Routing

**Risk:** MEDIUM-HIGH
**Time estimate:** 2-3 hours
**LOC change:** -250 / +270

### Step 3.1: Extend `hub/outbound_dispatcher.py`

**Move methods from Hub:**
- `_route_outbound`
- `dispatch_response`
- `dispatch_streaming`
- `dispatch_attachment`
- `dispatch_audio`
- `dispatch_audio_stream`
- `dispatch_voice_stream`

**New dispatcher structure:**
```python
class OutboundDispatcher:
    """Handles all outbound message routing and dispatch."""

    def __init__(
        self,
        adapters: dict[tuple[Platform, str], ChannelAdapter],
        event_bus: PipelineEventBus | None = None,
    ):
        self._adapters = adapters
        self._event_bus = event_bus

    async def dispatch_response(
        self, msg: InboundMessage, response: Response
    ) -> None: ...

    async def dispatch_streaming(
        self, msg: InboundMessage, events: AsyncIterator[RenderEvent]
    ) -> None: ...

    async def dispatch_attachment(
        self, msg: InboundMessage, attachment: OutboundAttachment
    ) -> None: ...

    async def dispatch_audio(
        self, msg: InboundMessage, audio: OutboundAudio
    ) -> None: ...

    async def _route_outbound(
        self, msg: InboundMessage
    ) -> ChannelAdapter | None: ...
```

### Step 3.2: Update `hub/hub.py`

**Remove methods:**
- All `dispatch_*` methods
- `_route_outbound`

**Add delegation:**
```python
async def dispatch_response(self, msg: InboundMessage, response: Response) -> None:
    await self._dispatcher.dispatch_response(msg, response)

async def dispatch_streaming(self, msg: InboundMessage, events) -> None:
    await self._dispatcher.dispatch_streaming(msg, events)

# ... etc for other dispatch methods
```

### Step 3.3: Update Callers

| File | Change |
|------|--------|
| `hub/middleware_stages.py` | Use dispatcher directly or via hub delegation |
| `hub/middleware_stt.py` | Update `dispatch_response` calls |
| `core/tts_dispatch.py` | Use dispatcher for audio dispatch |
| `bootstrap/hub_standalone.py` | Wire dispatcher with dispatch methods |

### Step 3.4: Update Tests

| Test File | Action |
|-----------|--------|
| `tests/unit/core/hub/test_outbound_dispatcher.py` | Add dispatch method tests |
| `tests/unit/core/test_hub.py` | Remove dispatch tests, verify delegation |

### Step 3.5: Verify

```bash
uv run pytest tests/unit/core/hub/ -v
uv run pytest tests/integration/ -v -k dispatch
python -c "from lyra.core.hub.outbound_dispatcher import OutboundDispatcher; print('OK')"
```

---

## File Summary

### New Files

| File | Phase | Lines |
|------|-------|-------|
| `hub/identity_resolver.py` | 1 | ~100 |

### Modified Files

| File | Phase | Lines Before | Lines After |
|------|-------|--------------|-------------|
| `hub/hub.py` | 1, 2, 3 | 791 | ~410 |
| `core/tts_dispatch.py` | 2 | 242 | ~290 |
| `hub/outbound_dispatcher.py` | 3 | 224 | ~490 |
| `hub/middleware_stages.py` | 1, 2, 3 | varies | minimal updates |

---

## Rollback Plan

Each phase is independently revertible:

```bash
# Rollback Phase N
git revert HEAD~N
```

**Phase 1:** Safe rollback — no structural changes to Hub interface
**Phase 2:** Safe rollback — TTS helpers are internal
**Phase 3:** Careful rollback — dispatcher interface changes

---

## Checklist

### Pre-execution

- [ ] Create feature branch: `feat/753-hub-extraction`
- [ ] Refresh GitNexus index: `npx gitnexus analyze`
- [ ] Run full test suite: `uv run pytest`

### Phase 1

- [ ] Create `hub/identity_resolver.py`
- [ ] Update `hub/hub.py` to delegate
- [ ] Update callers
- [ ] Add unit tests
- [ ] Run tests, verify green
- [ ] Commit: `refactor(753): extract IdentityResolver from Hub`

### Phase 2

- [ ] Extend `core/tts_dispatch.py`
- [ ] Update `hub/hub.py` to delegate
- [ ] Update callers
- [ ] Add unit tests
- [ ] Run tests, verify green
- [ ] Commit: `refactor(753): move TTS helpers to AudioPipeline`

### Phase 3

- [ ] Extend `hub/outbound_dispatcher.py`
- [ ] Update `hub/hub.py` to delegate
- [ ] Update callers
- [ ] Migrate tests
- [ ] Run tests, verify green
- [ ] Commit: `refactor(753): consolidate dispatch methods in OutboundDispatcher`

### Post-execution

- [ ] Verify Hub line count < 500
- [ ] Run GitNexus `detect_changes`
- [ ] Run full integration tests
- [ ] Update CLAUDE.md with new module references
