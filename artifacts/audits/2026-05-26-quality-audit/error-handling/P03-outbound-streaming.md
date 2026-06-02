### Summary

- P03 is clean on all five focus areas: no bare `except:`, no empty `pass` in `except`, no `str(exc)` in user-visible messages, no retry logic, and no missing `raise ... from e` (no exception wrapping occurs in this partition).
- One minor log-side `repr(exc)` leak at `emitter.py:465` bypasses the outbound SanitizedError boundary.
- The two `# DEBT:boundary-broad-catch` sites in `OutboundEmitter` are acknowledged debt (S7 migration) with no regression since the 2026-05-18 audit.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/outbound/emitter.py` | 465 | Low | `log.warning(..., stream_error=%r)` renders `repr(exc)` into logs, which includes the exception message and may leak hostnames, URLs, or token fragments. Bypasses the SanitizedError discipline enforced elsewhere in the package. | Replace `%r` with `type(self._st.stream_error).__name__` or move full traceback to a separate `exc_info=True` debug line; align with #1212 discipline. |

### Metrics

| Metric | Count |
|--------|-------|
| Files analyzed | 9 (5 outbound + 4 streaming) |
| `try/except` blocks | 3 |
| `except Exception:` sites | 3 (2 acknowledged debt in `emitter`, 1 intentional in `guard`) |
| `pass` in `except` blocks | 0 |
| `str(exc)` in user-visible paths | 0 |
| `raise ... from e` opportunities | 0 (no wrapping in partition) |
| Retry / circuit-breaker logic | 0 (CB lives in `OutboundDispatcher` per design) |
| Active findings | 1 |

### Recommendations

1. **Add pre-commit hook for outbound `str(exc)` / `repr(exc)`** — implement the deferred `tools/check_outbound_str_exc.sh` scoped in `src/lyra/outbound/CLAUDE.md` to catch log-side leaks like line 465.
2. **Complete S7 boundary-catch migration** — replace the two `# DEBT:boundary-broad-catch` sites in `OutboundEmitter` with typed or narrow catches as planned; confirm the `guard` single-catch contract survives the refactor.
3. **Audit fatal vs non-fatal `Err` handling** — the 6 `if Err: pass` best-effort sites in `emitter.py` are documented, but verify after S7 that no newly-fatal path should stop the stream instead of continuing.
