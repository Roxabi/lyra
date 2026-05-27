### Summary

- P03 carries 7 annotated debt markers (all `DEBT:` tags, zero `TODO/FIXME/HACK/XXX`); the dominant theme is deferred Phase 7 / Slice 7 cleanup with 5 distinct sites anchored to a not-yet-scheduled milestone.
- Zero deprecated stdlib or `asyncio.coroutine` usage; the only magic literal is a 2-character slice tied to a hardcoded emoji prefix (`"⏳ "`).
- ADR-048 does not directly touch P03 (no store imports), but the absence of any store protocol in `core/ports/` confirms the repository-wide migration is still incomplete.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/outbound/emitter.py` | 283 | low | Magic number `2` slices off `"⏳ "` prefix without named constant for prefix length. | Extract `_SPINNER_PREFIX = "⏳ "` and use `len(_SPINNER_PREFIX)`. |
| `src/lyra/outbound/emitter.py` | 337 | medium | `noqa: C901` + `DEBT:wiring-bootstrap-deps` — v1+v2 dispatch ladder complexity. | Split into per-version handlers in S7; link to #1284. |
| `src/lyra/outbound/emitter.py` | 374 | low | `DEBT:defensive-narrow-payloads` — `isinstance` ladder with pyright suppression. | Remove once v1 payload paths are deleted in S7. |
| `src/lyra/outbound/emitter.py` | 384 | low | `DEBT:defensive-narrow-payloads` — same pattern for Reasoning events. | Remove once v1 payload paths are deleted in S7. |
| `src/lyra/outbound/emitter.py` | 402 | medium | `DEBT:boundary-broad-catch` — terminal `except Exception` in `_run_event_loop`. | Unify with `OutboundErrorHandler` or elevate to caller in S7 (#1284). |
| `src/lyra/outbound/emitter.py` | 506 | medium | `DEBT:boundary-broad-catch` — terminal `except Exception` in `run()` peek block. | Same as above; migrate to `guard()` or restructure peek in S7. |
| `src/lyra/outbound/error_handler.py` | 26 | low | Magic number `120` embedded in timeout fallback string. | Replace with named constant `_TIMEOUT_SECONDS = 120`. |
| `src/lyra/outbound/throttle.py` | 15 | low | Transitional re-export note: "deleted at S7" with no tracking issue in comment. | Add `#1284` reference to the S7 deletion comment. |
| `src/lyra/outbound/CLAUDE.md` | 77 | medium | `DEBT:enforcement-deferred` — `tools/check_outbound_str_exc.sh` pre-commit hook scoped out of #1279 with no follow-up issue. | File a debt-tracking issue and add it to the comment. |
| `src/lyra/streaming/CLAUDE.md` | 43-48 | low | Parser Protocol aliases (`feed = parse_line`, `finalize`, `is_done`) deferred to "a follow-up issue" without issue number. | File issue, add number to CLAUDE.md, and link to epic #1277. |

### Metrics

| Metric | Count |
|---|---|
| Files analyzed | 9 (5 outbound + 4 streaming) |
| Total lines of Python source | ~1038 |
| `TODO` / `FIXME` / `HACK` / `XXX` | 0 |
| `DEBT:` annotations | 7 (5 in source, 2 in CLAUDE.md) |
| Deprecated API usages | 0 |
| Magic numbers / strings | 2 (slice `2`, timeout `120`) |
| Named-constant violations | 1 (prefix length) |
| Exemptions touching P03 | 1 (`emitter.py` 548 lines — #1279 Phase 7) |
| Deferred Phase-7 / Slice-7 references | 5 distinct sites + 3 exemption comments |
| ADR-048 store protocol gaps in `core/ports/` | confirmed (0 store protocols for 15 infrastructure store files) |

### Recommendations

1. **File #1284 sub-issues for the two remaining `boundary-broad-catch` sites** (`emitter.py:402` and `:506`). They are currently the only `except Exception` in `lyra.outbound/` outside `OutboundErrorHandler.guard`, and the CLAUDE.md explicitly says they will be unified in S7. Without sub-issues, they risk being forgotten when #1284 planning begins.

2. **Create the missing tracking issue for `tools/check_outbound_str_exc.sh`** (CLAUDE.md line 77). The pre-commit grep for bus-bound `str(exc)` was scoped out of #1279 with no follow-up issue. This is a concrete, small deliverable that can be picked up independently of S7.

3. **Replace magic literals before they propagate.** The `final[2:]` slice and the `120 s` string are trivial fixes. If new platforms adopt similar spinner prefixes or timeout copy, the literals will silently fork.

4. **Add Parser Protocol alias issue** (`streaming/CLAUDE.md` line 43-48). The note says "a follow-up issue will add aliases" but none exists. The work is small (3 property assignments + tests) and blocks runtime conformance checks.

5. **Do NOT remove the `emitter.py` file-size exemption yet.** It is correctly scoped to Phase 7 (#1284) and will resolve naturally when `StreamState` relocates out of `adapters.shared` and `PlatformCallbacks` is absorbed into `OutboundFormatter`. Premature removal would create churn.
