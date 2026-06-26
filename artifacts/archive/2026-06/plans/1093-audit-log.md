# #1093 — Fake/Mock/Stub Audit Log

Scope: `grep -rn "fake|mock|stub" src/ scripts/ --include="*.py"` (7 hits across 7 files).
Date: 2026-05-31

## Audit table

| symbol/term | file:line | kind | in prod-importable path? (Y/N) | note |
|---|---|---|---|---|
| `fake Co-Authored-By` | `src/lyra/core/cli/cli_pool_worker.py:118` | domain term | N | In a docstring — describes a security concern about crafted git trailer strings. No import, no test-double. |
| `fake_clock.now` | `src/lyra/tools/gh_token/rate_limit.py:36` | domain term | N | In a docstring — documents injection point for `clock` constructor parameter. No import, no test-double present in this file. |
| `tests can stub all I/O` | `src/lyra/tools/gh_token/dispenser.py:51` | domain term | N | In a docstring — describes constructor injection pattern. No import, no test-double present. |
| `Attribute stubs below` | `src/lyra/core/session_lifecycle.py:28` | domain term | N | In a docstring — "stubs" used in the sense of Protocol/mixin attribute declarations satisfied by `AgentBase.__init__`. Not a test-double. |
| `inject a mock or an in-process ASGI-backed store` | `src/lyra/infrastructure/blobstore_adapter.py:33` | domain term | N | In a docstring — describes testability of the adapter via constructor injection. No mock import. |
| `audit sink is a logger-only stub` | `src/lyra/blobstore/serve.py:122` | domain term | N | In an inline comment — "stub" refers to the degraded `BlobAuditSink()` instance (no NATS). This is a legitimate domain fallback, not a test-double. The class is in prod code and is the real degraded-mode implementation. |
| `fake nkeys` | `scripts/gen_nkeys.py:200` | domain term | N | In a CLI `--help` string for `--template-only` flag. Describes that the flag renders auth.conf without real nkey generation (uses placeholder values, no test import). |

## Summary

**Prod-path test-doubles found: 0.**

All 7 hits are documentation (docstrings, comments, help strings) describing design intent
or injection patterns using the words "fake", "mock", or "stub" as natural-language terms.
None of them import or instantiate a test-double class from `tests/`.

## Retired quality-debt entry

The `scripts._modes -> tests.fakes.nkey_provider` exemption in `.importlinter`
(`ignore_imports` under `[importlinter:contract:tests-fakes-isolation]`) was the only
production→test-double import path tracked in this project. It is **RETIRED** in this PR
(#1093, N6).

The runtime guard it protected (`NKEY_PROVIDER=fake` env-branch in `scripts/_modes.py::_get_provider`)
is also deleted (N2). The structural replacement — `import-linter` forbidding all `tests.*`
imports from `lyra` and `scripts` with no exemption — is verified by `tests/scripts/test_fake_isolation.py`.
