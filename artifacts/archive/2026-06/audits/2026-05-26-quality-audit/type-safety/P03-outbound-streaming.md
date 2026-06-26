### Summary

- **18 `Any` annotations** across 2 files (all in `outbound/`), concentrated on platform-specific placeholder/message objects and Protocol `render_buttons` return — the only category with material type-safety debt.
- **2 `# pyright: ignore`** comments in `emitter.py`, both `reportUnnecessaryIsInstance` with explicit `DEBT:defensive-narrow-payloads` tags — acceptable defensive code, not regressions.
- **Zero missing return types**, untyped `*args/**kwargs`, or `cast()`/`assert isinstance()` misuse — `streaming/` is fully typed; `outbound/` only gaps are the `Any` blanket on platform handles.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/outbound/formatter.py` | 47 | Medium | `OutboundFormatter.render_buttons(self, buttons: Any) -> Any` — Protocol method completely untyped on both input and output. | Define a `ButtonSpec` TypedDict or dataclass; return `list[dict[str, Any]]` or a platform-specific union instead of bare `Any`. |
| `src/lyra/outbound/emitter.py` | 93, 94, 95 | Medium | `PlatformCallbacks` fields `send_placeholder`, `edit_placeholder_text`, `send_trace_placeholder` use `Any` for the platform message object. | Introduce a `MessageHandle = TypeVar("MessageHandle")` or a lightweight `Protocol` (e.g. `HasMessageId`) so per-platform adapters (Telegram `Message`, Discord `Message`) are typed without `Any`. |
| `src/lyra/outbound/emitter.py` | 166, 247, 340, 407 | Low | `self._trace_obj`, `placeholder_obj` parameters typed as `Any`. | Same as above — once `MessageHandle` TypeVar exists, propagate it through `OutboundEmitter` internals. |
| `src/lyra/outbound/emitter.py` | 374, 384 | Low | `pyright: ignore[reportUnnecessaryIsInstance]` — defensive narrow after prior `if/elif` chains. | Keep as-is (tagged debt). These are guard-rails for protocol evolution; removing them would risk runtime regressions if new `RenderEvent` subtypes are added. |
| `src/lyra/outbound/formatter.py` | 25, 33, 52, 58 | Low | `trace_obj: Any` on no-op defaults and Protocol methods. | Align with `MessageHandle` typing once introduced; low priority since defaults are no-ops and Protocol is structural. |

### Metrics

| Metric | Count | Notes |
|---|---|---|
| Files analyzed | 9 | 5 in `outbound/`, 4 in `streaming/` |
| `typing.Any` annotations | 18 | `emitter.py` 12, `formatter.py` 6 |
| `# type: ignore` / `# pyright: ignore` | 2 | Both `reportUnnecessaryIsInstance` in `emitter.py` |
| Missing return type hints (public) | 0 | All methods/functions annotated |
| Untyped `*args` / `**kwargs` | 0 | None present |
| `cast()` usage | 0 | None present |
| `assert isinstance()` usage | 0 | `assert_never` used correctly for exhaustiveness |
| Protocol definitions | 2 | `OutboundFormatter`, `ThrottleCapability`, `Parser` — all fully typed except `render_buttons` |
| Generic classes | 2 | `EventEmitter[OutT]`, `StateMachine[K, V]` — variance and bounds correctly declared |

### Recommendations (prioritized)

1. **Type `render_buttons` on `OutboundFormatter`** — Medium. Define a `ButtonSpec` dataclass or TypedDict and replace `Any -> Any` with a concrete contract. This is the only public Protocol method without meaningful typing.
2. **Replace `Any` platform message handles with a `MessageHandle` TypeVar or Protocol** — Medium. The `PlatformCallbacks` dataclass and `OutboundEmitter` internals all use `Any` for the opaque platform message object. A `Protocol` with `message_id: int` (or similar common surface) would eliminate 12 `Any` annotations in one change.
3. **Keep `pyright: ignore` defensive `isinstance` guards** — Low. Do not remove; they are tagged `DEBT:defensive-narrow-payloads` and protect against future `RenderEvent` subtype additions. Track removal under a dedicated S7 cleanup issue if desired.
4. **No action needed on `streaming/`** — N/A. `EventEmitter`, `StateMachine`, `Parser` are fully generic and correctly bounded. No regressions from prior audit.
5. **Add `py.typed` marker if not present** — Low. Both `outbound/` and `streaming/` are internal source packages; downstream type-checking benefits from an explicit marker if this code is ever consumed as a library.
