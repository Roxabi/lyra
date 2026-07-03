# Changelog — roxabi-nats

All notable changes to the `roxabi-nats` package are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

`pyproject.toml` is at `0.4.2`, but the matching `roxabi-nats/v0.4.2` tag has
**not** been cut — an external `uv` pin referencing `roxabi-nats/v0.4.2` will
fail to resolve until the tag is pushed. Cutting the tag is a release action on
`staging` — see [Pin doctrine](../../docs/architecture/contracts.md#pin-doctrine-external-consumers).

### Deprecated (still shipped)

- `roxabi_nats.adapter_base.CONTRACT_VERSION` and top-level
  `roxabi_nats.CONTRACT_VERSION` remain compat re-exports (lazy
  `DeprecationWarning`); the canonical home is `roxabi_contracts.envelope`.
  Removal was originally scheduled for `v0.3.0` (ADR-045 / ADR-049) but was
  **never executed** — the re-exports still ship as of `0.4.2`. Removal is
  deferred and unscheduled; whichever release finally cuts it MUST carry a
  `BREAKING CHANGE:` trailer.


## [0.4.2] (2026-06-03) — tag pending

### Changed

- Part of the `lyra.* → factory.*` NATS wire-namespace rename ([#1670](https://github.com/Roxabi/roxabi-factory/issues/1670)): readiness-announce subject strings updated to the `factory.*` namespace.


## [0.4.1] (2026-05-20)

### Features

- Add a `wait_ready` opt-out for worker-class adapters so heartbeat-driven workers can skip the startup readiness gate ([#1147](https://github.com/Roxabi/roxabi-factory/issues/1147)).


## [0.4.0] (2026-05-19)

### Breaking

- Rename `NatsDriverBase._stream_gen` → `_dict_stream_gen` ([#1254](https://github.com/Roxabi/roxabi-factory/issues/1254) / [#1258](https://github.com/Roxabi/roxabi-factory/issues/1258)). Private `_`-prefixed rename; per the versioning policy this does not force a major bump, but external callers reaching into the private name must update.


## [0.3.0] (2026-04-27) — no tag cut

### Changed

- CliPool extracted into a dedicated NATS container (ADR-054) ([#946](https://github.com/Roxabi/roxabi-factory/issues/946)); driver-side plumbing moved with it. The `roxabi-nats/v0.3.0` tag was never pushed (the released tag chain is `v0.2.1 → v0.4.0 → v0.4.1`), and the `CONTRACT_VERSION` compat-re-export removal that earlier notes scheduled "at v0.3.0" did **not** land here — see [Unreleased].


## [0.2.1](https://github.com/Roxabi/lyra/compare/roxabi-nats/v0.2.0...roxabi-nats/v0.2.1) (2026-04-22)

### Bug Fixes

- **nats:** thread `inbox_prefix` through `NatsAdapterBase` for voiceCLI satellites ([ead4fec](https://github.com/Roxabi/lyra/commit/ead4fec12246d387adcded91b6a4a8adb1efe311)).


## [0.2.0] — 2026-04-17

### Features

- **voice:** load-aware routing for multi-GPU STT/TTS workers ([#732](https://github.com/Roxabi/lyra/issues/732)) ([b928bb0](https://github.com/Roxabi/lyra/commit/b928bb0685ba78d04e2a4a62760d39d507e67ab9)).

### Breaking

- **`NatsAdapterBase.__init__` gains a keyword-only `type_registry` parameter.**
  Adapter subclasses must declare their TYPE_CHECKING-only type hints at construction:
  ```python
  adapter = MyAdapter(
      subject="factory.inbound.tg.main",
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
