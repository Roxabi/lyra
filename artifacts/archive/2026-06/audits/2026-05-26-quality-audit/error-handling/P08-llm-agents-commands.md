### Summary

- P08 error handling is mostly healthy: no bare `except:` or empty `pass` blocks, and retry/circuit-breaker patterns in `llm/decorators.py` are well-implemented with exponential backoff.
- Two boundary broad-catches (`simple_agent.py:136`, `svc/handlers.py:91`) are annotated with `DEBT:boundary-broad-catch` and use safe-fallback patterns, but `simple_agent.py` silently degrades functionality without notifying the user.
- One clear `str(exc)` leak exists in `pairing/handlers.py:33` where `PairingError` text flows unfiltered into a user-visible `Response`.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/commands/pairing/handlers.py` | 33 | High | `str(exc)` from `PairingError` leaks directly into user-visible `Response` | Map to a domain-safe message or route through `safe_error_response` |
| `src/lyra/agents/simple_agent.py` | 136 | Medium | `except Exception:` broad-catch during `SessionTools` construction silently disables processor pipeline | Narrow to expected exception types or surface degraded-state notice to user |
| `src/lyra/llm/llm_client.py` | 189 | Medium | `resume_and_reset` silently swallows transport `Err` and returns `False` with no log | Log the transport error before returning `False` |
| `src/lyra/commands/svc/handlers.py` | 91 | Low | `except Exception:` boundary catch uses `safe_error_response`, but remains unbounded | Track `DEBT` annotation and narrow to `ServiceControlFailed` + transport/IO errors |
| `src/lyra/agent_cmd/agents/init.py` | 52 | Low | `except Exception` in batch TOML seeding continues loop; acceptable but unbounded | Consider narrowing to `OSError`, `ValidationError`, and TOML decode errors |

### Metrics

| Metric | Count |
|---|---|
| Files scanned | 27 (9 empty `__init__.py`) |
| Meaningful files | 18 |
| Try/except blocks | 18 |
| Bare `except Exception` without re-raise | 3 |
| Empty `pass` in except | 0 |
| `str(exc)` in user-visible output | 1 |
| Missing `raise ... from` (original context lost) | 0 |
| Retry without backoff / circuit breaker | 0 |
| Silent swallowed errors (broad catch + continue without user notice) | 2 |

### Recommendations

1. **Sanitize `pairing/handlers.py` leak** (High) — Replace `return Response(content=str(exc))` with structured error mapping or `safe_error_response` to prevent internal details from reaching users.
2. **Narrow or notify in `simple_agent.py` broad catch** (Medium) — The `SessionTools` catch at line 136 should either narrow to expected initialization exceptions or emit a user-visible notice/warning that processor commands are disabled.
3. **Log silent transport errors in `llm_client.py`** (Medium) — Add `log.warning` before returning `False` on `Err` in `resume_and_reset` so resume failures are visible in domain logs.
4. **Tighten CLI batch catch in `init.py`** (Low) — Replace `except Exception as e` at line 52 with `OSError`, `ValidationError`, and TOML decode exceptions to avoid masking unexpected bugs during seeding.
5. **Resolve `DEBT:boundary-broad-catch` annotations** (Low) — Schedule follow-up to replace the two flagged boundary catches with explicit exception inventories as the stage-axis refactor stabilizes.
