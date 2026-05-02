# Testing Conventions — Roxabi Standard

> **Status: REFERENCE**
> Scope: All Roxabi projects (lyra, voiceCLI, imageCLI, 2ndBrain, roxabi-plugins)
> Purpose: Define mandatory testing rules enforced at code review

---

## Negative-Test Rule (MANDATORY)

**Every guard must ship with a negative test.**

A *negative test* is one that **fails** when the guarded branch is removed, the filter is bypassed, or the Protocol method is deleted.

### What counts as a guard

- `if` / `elif` / `else` branching on a condition
- `None`-checks and early-return guards
- Filter expressions (`filter()`, list comprehensions with conditions)
- Protocol / ABC method implementations
- Exception catches that alter control flow

### Failure condition

A test is **not** a negative test if it passes when the guard is deleted. Examples of tautological tests that do NOT satisfy this rule:

| Anti-pattern | Why it fails |
|---|---|
| `warnings.simplefilter("ignore")` on a warning the test asserts | Guard deletion → test still passes |
| Assert only the happy path, never the branch condition | Guard deletion → test still passes |
| Shim re-exports tested at import time only | Shim returning stale data → test still passes |
| Protocol method presence not verified against the interface | Method removal → test still passes |

### Correct pattern

```python
# Guard under test
def process(value: str | None) -> str:
    if value is None:          # <-- guard
        raise ValueError("value required")
    return value.upper()

# Positive test (happy path)
def test_process_returns_upper():
    assert process("hello") == "HELLO"

# Negative test — fails if the guard is deleted
def test_process_raises_on_none():
    with pytest.raises(ValueError, match="value required"):
        process(None)
```

If `if value is None: raise ValueError(...)` is removed, `test_process_raises_on_none` fails. That is the signal the rule requires.

### Enforcement

The `dev-core:tester` agent flags missing negative tests as `issue:` (≥90% confidence) during code review. This is a **merge blocker**.

---

## Coverage Rules

- Import + call real source functions — never mock the module under test
- `vi.mock('./sut')` → 0% real coverage; banned
- Integration tests (real modules wired) > unit tests with heavy mocks
- Verify coverage: `{commands.test} --coverage <file>` — 0% → wrong mocking

---

## Test Trophy (priority order)

1. **Static** — type checker + linter (automatic)
2. **Unit** — pure functions, utilities, type guards
3. **Integration** (largest layer) — real modules wired together
4. **E2E** — critical journeys only
