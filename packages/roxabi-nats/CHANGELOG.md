# Changelog — roxabi-nats

All notable changes to the `roxabi-nats` package are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-04-17

### Breaking

- **`NatsAdapterBase.__init__` gains a keyword-only `type_registry` parameter.**
  Adapter subclasses must declare their TYPE_CHECKING-only type hints at construction:
  ```python
  adapter = MyAdapter(
      subject="lyra.inbound.tg.main",
      queue_group="lyra-hub",
      envelope_name="InboundMessage",
      schema_version=1,
      type_registry=[
          ("lyra.core.commands.command_parser", "CommandContext"),
      ],
  )
  ```
  Adapters that do not need TYPE_CHECKING resolution can pass `type_registry=None` (the default).
- **`serialize`, `deserialize`, `deserialize_dict` gain a keyword-only `resolver` parameter.**
  Callers that decode dataclasses carrying TYPE_CHECKING-only annotations MUST pass a configured `TypeHintResolver`. Bare calls default to an empty resolver and will fail to coerce types whose hints are only visible under `TYPE_CHECKING`.
- **`_register_type_checking_import` removed.**
  The process-global `_TYPE_CHECKING_IMPORTS` registry is gone. Consumers must construct a `_TypeHintResolver` and pass it explicitly.
- **`_TYPE_CHECKING_IMPORTS` module-level list removed** from `roxabi_nats._serialize`.

### Added

- **Public `TypeHintResolver`** exported from the package root (`from roxabi_nats import TypeHintResolver`). Wraps the internal `_TypeHintResolver` class; external consumers should use the public alias rather than reaching into `_serialize` / `_resolver`.
- `_TypeHintResolver` class with fail-fast validation. Invalid module paths or missing attributes raise `ValueError` at resolver construction, not at deserialize time. Duplicate `type_name` with different `module_path` also fails loud (was silently last-wins internally during development).
- Internal singleton `_EMPTY_RESOLVER` — immutable null-object used as the default resolver for bare `serialize`/`deserialize` calls. `resolved` is a `MappingProxyType` so attempted mutation raises `TypeError`.
- Resolver instances carry a per-instance monotonic `_uid`; the hint cache pairs `(dc_type, resolver._uid)` so GC'd resolvers cannot poison cache entries of new resolvers that happen to land at the same `id()` address.

### Migration

Before (v0.1.x):

```python
from roxabi_nats._serialize import _register_type_checking_import

_register_type_checking_import(
    "my.package.types", "MyTypeCheckingOnlyType"
)

adapter = MyAdapter(...)
```

After (v0.2.0):

```python
adapter = MyAdapter(
    ...,
    type_registry=[
        ("my.package.types", "MyTypeCheckingOnlyType"),
    ],
)
```

Direct `serialize`/`deserialize` consumers:

```python
# Before
from roxabi_nats._serialize import deserialize
msg = deserialize(data, MyDataclass)

# After — build one resolver at module scope, reuse it
from roxabi_nats import TypeHintResolver
from roxabi_nats._serialize import deserialize

_RESOLVER = TypeHintResolver([
    ("my.package.types", "MyTypeCheckingOnlyType"),
])
msg = deserialize(data, MyDataclass, resolver=_RESOLVER)
```

External `v0.1.x` consumers (voiceCLI, roxabi-vault, imageCLI) that imported the private `_register_type_checking_import` helper will hit `ImportError` on upgrade. Either migrate to `type_registry=` at adapter construction or pin `roxabi-nats = ">=0.1.0,<0.2.0"` until the migration is complete.

## [0.1.0] — 2026-04-14

### Added

- Initial extraction from the Lyra monorepo as a uv workspace subpackage. See `docs/architecture/adr/045-roxabi-nats-sdk-uv-workspace-extraction.mdx`.
- `NatsAdapterBase` — ABC lifecycle host for NATS request-reply adapters.
- `nats_connect` — seed-auth-aware NATS connection helper.
- Internal helpers: `_serialize` (type-aware JSON codec), `_sanitize`, `_validate`, `_version_check`, `_tts_constants`.
- Full test suite covering adapter lifecycle, circuit breaker, readiness probe, serialization round-trip, and version-gate drop handling.
