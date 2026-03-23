# src/lyra/core/pool/ — Agent Pool Primitives

## Purpose

Agent pool primitives — the pool lifecycle, per-message processing, and session
observation. A `Pool` is one-per-conversation-scope: it serialises turns, debounces
rapid messages, and holds the SDK history deque.

## Files

| File | Responsibility |
|------|---------------|
| `pool.py` | `Pool` class + `PoolContext` protocol (the test seam for decoupling Pool from Hub) |
| `pool_observer.py` | `PoolObserver` — session observation, message indexing, pool-level events |
| `pool_processor.py` | `PoolProcessor` — per-message agent dispatch; submit message → call agent → dispatch reply |

## Note: pool_manager.py is in hub/ (not here)

`pool_manager.py` bridges hub and pool lifecycles and imports `Hub` at runtime.
Placing it in `pool/` would create a cross-package circular import. It lives in
`hub/` where it is architecturally owned. See `hub/CLAUDE.md` for details.

## Import pattern

```python
# Subpackage re-exports (preferred)
from lyra.core.pool import Pool, PoolProcessor

# Direct module imports
from lyra.core.pool.pool import Pool, PoolContext
from lyra.core.pool.pool_processor import PoolProcessor
from lyra.core.pool.pool_observer import PoolObserver
```

## Gotchas

- `Pool` never knows which platform it came from — routing is resolved by `PoolManager`
  (in `hub/`) before the Pool is touched.
- `PoolContext` is the test seam: inject a mock implementing `PoolContext` to unit-test
  `Pool` without pulling in the full `Hub`.
- Do NOT make `Pool` depend on `Hub` directly — use `PoolContext` instead.
- Pool IDs are always produced by `RoutingKey.to_pool_id()` (from `hub/hub_protocol.py`).
  Never build them with string formatting.
