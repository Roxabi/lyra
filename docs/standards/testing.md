---
title: Testing Mechanics — factory
description: Developer-facing testing mechanics for the factory codebase — run commands, tests/ layout, async helpers, patching helpers, and shared fixtures. Rules live in engineering-standards.md.
---

# Testing Mechanics — factory

> Status: LIVING
> Scope: `tests/` — all test files in the factory project
> Source: `tests/conftest.py`

This document is the developer-facing **mechanics** companion to
`docs/architecture/engineering-standards.md` (§ Testing conventions). Read both — the
architecture doc defines the **rules**; this doc defines **how to run tests and where
things live**.

## Rules live in engineering-standards.md

The testing **doctrine** is defined once in
[`engineering-standards.md` § Testing conventions](../architecture/engineering-standards.md#testing-conventions)
— do not restate it here (two copies diverge):

| Rule | Where |
|------|-------|
| Negative-test rule (every guard ships with a failing-when-deleted test) | engineering-standards § Negative-test rule |
| Mock boundaries & coverage (never mock the module under test; patch the dependency's import site; 0% coverage = wrong patch site) | engineering-standards § Mock boundaries & coverage |
| Test taxonomy (trophy: static → unit → integration → e2e; prefer integration over heavily-mocked unit) | engineering-standards § Test taxonomy |

Everything below is factory-specific mechanics: commands, layout, and the shared helpers
in `conftest.py`.

---

## Run commands

```bash
uv run pytest                            # full suite
uv run pytest tests/test_config.py       # single file
uv run pytest -k "TestResolveValue"      # by class/function name
uv run pytest --cov=src/factory tests/   # with coverage
```

---

## File naming and structure

- Test files: `test_{module_under_test}.py`.
- Classes: `Test{Subject}` containing related test methods.
- Methods: `test_{scenario}_{expected_outcome}` or `test_{action}_{condition}`.

Test files live under `tests/`, either at the top level or in subdirectories that mirror
the `src/factory/` package layout (`tests/core/`, `tests/adapters/`, `tests/integration/`,
`tests/e2e/`, `tests/nats/`, … — 30+ subpackages). Pick the subdirectory that matches the
module under test; small cross-cutting suites may stay at the top level.

```
tests/
  conftest.py                          # shared fixtures (fixtures only, no tests)
  test_config.py                       # top-level suite for src/factory/config.py
  core/
    test_config_dataclasses.py         # tests for core config dataclasses
  adapters/ integration/ e2e/ nats/ …  # subpackage-mirrored suites
```

No `__tests__/` directories, no co-located test files inside `src/`.

---

## Config-validation tests (renderer → consumer roundtrip)

When a tool or static file in `deploy/` is consumed by an external binary (nats-server,
podman, systemd, openssl), add a renderer→consumer roundtrip test instead of a plain
integration test. This pattern exercises the actual downstream binary in parse/check mode
on the rendered output and adds structural-invariant assertions beyond exit-code 0. See
[renderer-roundtrip.md](./renderer-roundtrip.md) for the full pattern, required shape, and
reviewer checklist.

---

## Async tests

Use `pytest-asyncio`. Mark async test functions with `@pytest.mark.asyncio` or configure
globally in `pyproject.toml`.

```python
@pytest.mark.asyncio
async def test_store_connect_and_read() -> None:
    store = AgentStore(path=tmp_path / "agents.db")
    await store.connect()
    result = store.get("missing_key")
    assert result is None
    await store.close()
```

Use `yield_once()` (defined in `conftest.py`) instead of `asyncio.sleep(0)` when you need
to yield to the event loop once. Use `_drain(pool, timeout=TIMEOUT_IO)` to wait for a pool
task to complete in tests.

Timeout constants from `conftest.py`:

| Constant | Value | Use case |
|---|---|---|
| `TIMEOUT_FAST` | 0.5 s | In-memory operations |
| `TIMEOUT_IO` | 2.0 s | Single network round-trip |
| `TIMEOUT_SLOW` | 5.0 s | Multi-step coordination, CI variance |

---

## Patching patterns

### Bootstrap / integration tests

Use the shared helpers in `conftest.py` to avoid duplicating bootstrap patches:

```python
# Full bootstrap patch (hub + adapters + auth + NATS)
captured_hubs, fake_auth_store = patch_all(monkeypatch)

# Credential resolution only
patch_bootstrap_common(monkeypatch)

# Credential store only
fake_keyring, fake_cred_store = make_fake_stores(monkeypatch)
```

### Patching NATS

Use `_patch_nats_stubs(monkeypatch)` (from `conftest.py`) to prevent tests from touching a
real NATS server. It patches `ensure_nats`, `acquire_lockfile`, `release_lockfile`,
`NatsBus`, and `JetStreamAuditSink`.

Always set `ROXABI_FACTORY_DIR` to a temp dir in tests that touch the credential store:

```python
monkeypatch.setenv("ROXABI_FACTORY_DIR", tempfile.mkdtemp())
```

---

## Fixtures

Shared fixtures live in `conftest.py`. Prefer fixtures over setup/teardown methods.

Key shared fixtures:

| Fixture | Returns | Use case |
|---|---|---|
| `circuit_registry` | `CircuitRegistry` | Pre-populated with 4 breakers |
| `hub` | `Hub` | Hub wired to `circuit_registry` |

Fixture `_reset_version_check_log_state` is `autouse=True` — it clears the `roxabi_nats`
rate-limit log state before every test to prevent cross-test ordering flakiness.

---

## What NOT to do (mechanics)

- Do NOT use `asyncio.sleep(N)` for event-loop coordination — use `yield_once()` or
  `_drain()` from `conftest.py`.
- Do NOT touch a real NATS server or credential store — use `_patch_nats_stubs()` and set
  `ROXABI_FACTORY_DIR` to a temp dir.
- Do NOT put tests inside `src/` or in `__tests__/` directories.
- Do NOT test implementation details of third-party SDKs (aiogram, discord.py).

---

## AI Quick Reference

ALWAYS use `yield_once()` or `_drain()` instead of `asyncio.sleep(0)` in async tests.

ALWAYS set `ROXABI_FACTORY_DIR` to a temp dir in tests that instantiate credential stores.

ALWAYS use the `conftest.py` bootstrap helpers (`patch_all`, `patch_bootstrap_common`,
`make_fake_stores`, `_patch_nats_stubs`) rather than hand-rolling bootstrap patches.

NEVER use `asyncio.sleep(N)` for coordination — use event-based helpers from `conftest.py`.

Run: `uv run pytest --cov=src/factory tests/`.

For the negative-test rule, mock boundaries, and the test trophy, see
[`engineering-standards.md` § Testing conventions](../architecture/engineering-standards.md#testing-conventions).
