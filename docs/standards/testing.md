---
title: Testing Standards — Lyra
description: Mandatory testing rules for the Lyra codebase — negative-test rule, coverage strategy, pytest conventions, and async patterns.
---

# Testing Standards — Lyra

> Status: LIVING
> Scope: `tests/` — all test files in the Lyra project
> Source: `docs/architecture/testing-conventions.md`, `tests/conftest.py`

This document is the developer-facing companion to `docs/architecture/testing-conventions.md`. Read both — the architecture doc defines the rules; this doc defines the mechanics.

---

## Run commands

```bash
uv run pytest                          # full suite
uv run pytest tests/test_config.py     # single file
uv run pytest -k "TestResolveValue"    # by class/function name
uv run pytest --cov=src/lyra tests/   # with coverage
```

---

## Negative-Test Rule (MANDATORY)

**Every guard must ship with a negative test.** A guard is any `if`/`elif`, `else` that encodes an error path, `None`-check, filter expression, Protocol method implementation, or exception catch that alters control flow.

A test is a negative test if and only if it **fails** when the guard is deleted. If deleting the guard makes the test pass, it is not a negative test.

```python
# Guard under test
def process(value: str | None) -> str:
    if value is None:          # guard
        raise ValueError("value required")
    return value.upper()

# Negative test — fails if the guard is deleted
def test_process_raises_on_none():
    with pytest.raises(ValueError, match="value required"):
        process(None)
```

Anti-patterns that do NOT satisfy this rule:

| Anti-pattern | Why it fails |
|---|---|
| `warnings.simplefilter("ignore")` on the warning the test asserts | Guard deletion → test still passes |
| Assert only happy path, never the branch condition | Guard deletion → test still passes |
| Protocol method presence not verified against the interface | Method removal → test still passes |

This rule is enforced at code review by `dev-core:tester` and is a **merge blocker**.

---

## Test Trophy (priority order)

```
1. Static  — type checker (pyright) + linter (ruff)  [automatic]
2. Unit    — pure functions, utilities, type guards
3. Integration  ← largest layer — real modules wired together
4. E2E     — critical journeys only
```

Prefer integration tests over unit tests with heavy mocks. Import and call real source functions — never mock the module under test.

---

## Coverage Rules

```bash
uv run pytest --cov=src/lyra tests/    # must show > 0% on the module under test
```

If coverage shows 0% on a module you intended to test, you are patching the wrong import site. Patch at the import site of the **dependency**, never the module under test.

```python
# Correct: patch where the dependency is imported
monkeypatch.setattr(stores_mod, "AuthStore", lambda **kw: fake_auth_store)

# Wrong: patch the source module
monkeypatch.setattr("lyra.infrastructure.stores.auth_store.AuthStore", ...)  # may silently miss
```

---

## File Naming and Structure

- Test files: `test_{module_under_test}.py` in `tests/` (flat, no subdirs).
- Classes: `Test{Subject}` containing related test methods.
- Methods: `test_{scenario}_{expected_outcome}` or `test_{action}_{condition}`.

```
tests/
  conftest.py                          # shared fixtures (fixtures only, no tests)
  test_config.py                       # tests for src/lyra/config.py
  test_monitoring_checks_primitives.py # tests for monitoring/checks.py primitives
```

No `__tests__/` directories, no co-located test files inside `src/`.

---

## Async Tests

Use `pytest-asyncio`. Mark async test functions with `@pytest.mark.asyncio` or configure globally in `pyproject.toml`.

```python
@pytest.mark.asyncio
async def test_store_connect_and_read() -> None:
    store = AgentStore(path=tmp_path / "agents.db")
    await store.connect()
    result = store.get("missing_key")
    assert result is None
    await store.close()
```

Use `yield_once()` (defined in `conftest.py`) instead of `asyncio.sleep(0)` when you need to yield to the event loop once. Use `_drain(pool, timeout=TIMEOUT_IO)` to wait for a pool task to complete in tests.

Timeout constants from `conftest.py`:

| Constant | Value | Use case |
|---|---|---|
| `TIMEOUT_FAST` | 0.5 s | In-memory operations |
| `TIMEOUT_IO` | 2.0 s | Single network round-trip |
| `TIMEOUT_SLOW` | 5.0 s | Multi-step coordination, CI variance |

---

## Patching Patterns

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

Use `_patch_nats_stubs(monkeypatch)` (from `conftest.py`) to prevent tests from touching a real NATS server. It patches `ensure_nats`, `acquire_lockfile`, `release_lockfile`, `NatsBus`, and `JetStreamAuditSink`.

Always set `LYRA_VAULT_DIR` to a temp dir in tests that touch the credential store:

```python
monkeypatch.setenv("LYRA_VAULT_DIR", tempfile.mkdtemp())
```

---

## Fixtures

Shared fixtures live in `conftest.py`. Prefer fixtures over setup/teardown methods.

Key shared fixtures:

| Fixture | Returns | Use case |
|---|---|---|
| `circuit_registry` | `CircuitRegistry` | Pre-populated with 4 breakers |
| `hub` | `Hub` | Hub wired to `circuit_registry` |
| `patch_agent_store` | `MagicMock` | Patched `AgentStore` in `__main__` |

Fixture `_reset_version_check_log_state` is `autouse=True` — it clears the `roxabi_nats` rate-limit log state before every test to prevent cross-test ordering flakiness.

---

## What NOT to Test

- Do NOT test implementation details of third-party SDKs (aiogram, discord.py).
- Do NOT assert on log output unless it is part of a guard (the negative test rule applies).
- Do NOT write tests that pass when the function they test is commented out.
- Do NOT use `asyncio.sleep(N)` for event-loop coordination — use `yield_once()` or `_drain()`.

---

## AI Quick Reference

ALWAYS write a negative test for every guard in new code.

ALWAYS patch at the dependency's import site, not the source module.

ALWAYS use `yield_once()` or `_drain()` instead of `asyncio.sleep(0)` in async tests.

ALWAYS set `LYRA_VAULT_DIR` to a temp dir in tests that instantiate credential stores.

NEVER mock the module under test — only mock its dependencies.

NEVER write a test that passes when the guarded branch is deleted.

NEVER use `asyncio.sleep(N)` for coordination — use event-based helpers from `conftest.py`.

PREFER integration tests (real modules wired) over unit tests with heavy mocks.

PREFER `pytest.raises(ExceptionType, match="expected message")` over bare `pytest.raises(ExceptionType)`.

Run: `uv run pytest --cov=src/lyra tests/` — 0% coverage on the target module means wrong patch site.
