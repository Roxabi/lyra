# Tech Debt Analysis: LLM, Agents, Misc

**Date:** 2026-04-22
**Scope:** `src/lyra/llm/**/*.py`, `src/lyra/agents/**/*.py`, `src/lyra/*.py`, `src/lyra/config/**/*.py`, `src/lyra/monitoring/**/*.py`, `src/lyra/stt/**/*.py`
**Total Lines Analyzed:** 2,205

---

### Summary

The analyzed areas show a clean codebase with no TODOs or FIXMEs. Primary technical debt consists of type: ignore pragmas (37 instances), broad Exception handlers (30+), and deprecated patterns (2). Code quality is generally high with intentional noqa comments documenting design decisions.

---

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `cli.py` | 14, 291 | Deprecated `lyra-agent` alias | Low | Document removal timeline in README; consider warning expiration |
| `cli_setup.py` | 75 | Deprecated `[plugins].enabled` key warning | Low | Track migration progress; remove warning once migration complete |
| `bootstrap/factory/voice_overlay.py` | 18-25 | Deprecated env var pattern with DeprecationWarning | Low | Already handled; track for removal in future version |
| `llm/drivers/sdk.py` | 85 | Magic number `_MAX_TURNS_SAFETY = 1000` | Low | Extract to config constant |
| `llm/drivers/sdk.py` | 73 | Magic number `max_tokens=4096` hardcoded | Medium | Consider making configurable via ModelConfig |
| `llm/drivers/nats_driver.py` | 32 | Magic number `HB_TTL = 30.0` | Low | Already documented; consider config exposure |
| `llm/drivers/nats_driver.py` | 209 | Magic number `maxsize=512` for queue | Low | Document rationale or extract to config |
| `llm/smart_routing.py` | 59-82 | Magic thresholds (3, 2, 100, 20 words) | Low | Document rationale; consider config if tuning needed |
| `agents/anthropic_agent.py` | 43 | Broad `except Exception` at line 111, 201 | Medium | Narrow to specific exceptions where possible |
| `agents/simple_agent.py` | 130 | Broad `except Exception` | Medium | Narrow to specific exceptions where possible |
| `monitoring/escalation.py` | 17 | Magic number `TELEGRAM_MAX_LEN = 4000` | Low | Already documented with rationale |
| `monitoring/escalation.py` | 88 | Magic number `timeout=30` | Low | Extract to config constant |
| `monitoring/checks.py` | 34, 53 | Magic number `timeout=5` (hardcoded) | Low | Extract to config constant |
| `monitoring/checks.py` | 204 | Magic number threshold `120` for reaper sweep | Low | Document rationale |
| `stt/__init__.py` | 91-96, 157 | Multiple `type: ignore[import-missing]` | Medium | Add voiceCLI to type checking path or create stubs |
| `stt/__init__.py` | 153 | Missing annotation for `socket_path` (noqa: ANN001) | Low | Add type annotation |
| `config.py` | 177 | Legacy backward-compat code | Low | Track for future removal |
| `llm/base.py` | 48-51 | Protocol stream method is "duck-typed optional" | Info | Document migration path when all drivers implement |

---

### Metrics

| Metric | Count |
|--------|-------|
| TODOs | 0 |
| FIXMEs | 0 |
| HACK comments | 0 |
| XXX comments | 0 |
| BUG comments | 0 |
| `type: ignore` pragmas | 37 |
| `noqa:` pragmas | 35+ |
| Broad `except Exception:` | 30+ |
| Deprecated patterns | 3 |
| Magic numbers (timeout/threshold) | 15+ |

---

### Recommendations

1. **Priority 1 - Type Safety (Medium)**
   - Create stub files for `voiceCLI` package to eliminate `type: ignore[import-missing]` in `stt/__init__.py`
   - This improves IDE support and catches type errors at development time

2. **Priority 2 - Exception Handling (Medium)**
   - Review broad `except Exception:` blocks in `agents/` and narrow to specific exceptions
   - Especially `simple_agent.py:130` (session tools construction) and `anthropic_agent.py:111, 201`

3. **Priority 3 - Configuration Extraction (Low)**
   - Extract hardcoded magic numbers to config:
     - `llm/drivers/sdk.py:73` - `max_tokens=4096` → make configurable
     - `monitoring/escalation.py:88` - `timeout=30` → `MonitoringConfig`
     - `monitoring/checks.py:34,53` - `timeout=5` → `MonitoringConfig`

4. **Priority 4 - Deprecation Tracking (Low)**
   - Document removal timeline for deprecated `lyra-agent` alias (cli.py:14)
   - Track `[plugins].enabled` → `[commands].enabled` migration progress

5. **Priority 5 - Code Documentation (Info)**
   - Add docstrings explaining magic thresholds in `llm/smart_routing.py`
   - Consider adding `# Rationale:` comments for intentional magic numbers

---

### Notes

- The codebase follows good practices with intentional `noqa:` comments that document *why* certain rules are bypassed (e.g., `# noqa: PLR0913 — DI constructor, each arg is a required dependency`)
- No dead code or unreachable branches detected in analyzed files
- Commented-out code blocks are minimal and intentional (documentation examples)
- Naming patterns are consistent across all analyzed modules
