# Error-Handling Audit — P09 (boundary / tools / monitoring / obs / blobstore)

Date: 2026-05-27
Scope: `src/lyra/integrations/**/*.py`, `src/lyra/tools/**/*.py`, `src/lyra/monitoring/**/*.py`, `src/lyra/obs/**/*.py`, `src/lyra/blobstore/**/*.py`
Context: Epic #1277 stage-axis refactor active. Prior audit (2026-05-18) covered hexagonal conformance / duplication / dead-code — not re-reported unless regressed.

---

### Summary

- **16 broad `except Exception` (or `except:`) blocks** across 12 files; the 5 in `monitoring/` leak `str(exc)` into operator-facing Telegram alerts, violating the #1212 cascade pattern.
- **6 integration boundary translators** (`audio`, `supervisor`, `systemctl`, `vault_cli`, `web_intel`) remap `FileNotFoundError` (and `json.JSONDecodeError` in `web_intel`) to domain exceptions **without `raise ... from e`**, dropping the original traceback context.
- **blobstore handlers** contain 3 spec-approved broad catches (`# noqa: BLE001`) mapping unhandled exceptions to HTTP 500; `_log.exception` partially mitigates the missing `from e`.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|--------------|
| `src/lyra/monitoring/checks.py` | 86 | **High** | `check_http_health` catches broad `Exception` and puts `str(exc)` into `CheckResult.detail`, which flows to Telegram alerts (#1212 leak). | Narrow to `httpx.HTTPError` + `OSError`; map to safe, opaque detail strings. |
| `src/lyra/monitoring/checks_varz.py` | 65 | **Medium** | `check_nats_varz` catches broad `Exception` and puts `str(exc)` into `CheckResult.detail`. | Narrow to `httpx.HTTPError`, `json.JSONDecodeError`, `OSError`; use safe detail strings. |
| `src/lyra/monitoring/__main__.py` | 50 | **Medium** | `escalate_to_llm` wrapped in broad `except Exception`, swallowing all LLM/CLI failures into log-only fallback. | Narrow to `RuntimeError`, `asyncio.TimeoutError`; let unexpected exceptions propagate to cron error channel. |
| `src/lyra/monitoring/__main__.py` | 56, 67 | **Medium** | Telegram raw-alert and diagnosis-alert delivery use broad `except Exception`, swallowing transport errors silently. | Narrow to `httpx.HTTPError`, `RuntimeError` (specific to Telegram Bot API failures). |
| `src/lyra/integrations/audio.py` | 58 | **Medium** | `FileNotFoundError` remapped to `AudioConversionFailed("not_available")` without `from e`. | Add `from exc` to preserve original traceback. |
| `src/lyra/integrations/supervisor.py` | 88 | **Medium** | `FileNotFoundError` remapped to `ServiceControlFailed("not_available")` without `from e`. | Add `from exc` to preserve original traceback. |
| `src/lyra/integrations/systemctl.py` | 100 | **Medium** | `FileNotFoundError` remapped to `ServiceControlFailed("not_available")` without `from e`. | Add `from exc` to preserve original traceback. |
| `src/lyra/integrations/vault_cli.py` | 92 | **Medium** | `FileNotFoundError` remapped to `VaultWriteFailed("not_available")` without `from e`. | Add `from exc` to preserve original traceback. |
| `src/lyra/integrations/web_intel.py` | 79 | **Medium** | `FileNotFoundError` remapped to `ScrapeFailed("not_available")` without `from e`. | Add `from exc` to preserve original traceback. |
| `src/lyra/integrations/web_intel.py` | 84 | **Medium** | `json.JSONDecodeError` / `UnicodeDecodeError` caught as `exc` but `ScrapeFailed` is raised without `from exc`. | Add `from exc` to preserve parse-failure context. |
| `src/lyra/monitoring/checks_log.py` | 49 | **Low** | `check_nats_log_errors` puts `str(exc)` into `detail` for `subprocess.CalledProcessError` / `TimeoutExpired` / `FileNotFoundError`. | Replace `str(exc)` with operator-safe message; log raw exception separately. |
| `src/lyra/monitoring/checks_log.py` | 99 | **Low** | `check_hub_dict_stream_gen_timeout` puts `str(exc)` into `detail` for subprocess errors. | Same as above. |
| `src/lyra/monitoring/checks.py` | 44 | **Low** | `check_process` puts `str(exc)` into `detail` for `subprocess.TimeoutExpired` / `FileNotFoundError`. | Replace `str(exc)` with safe message; log raw exception. |
| `src/lyra/blobstore/_handlers.py` | 121 | **Low** | `handle_put` broad `except Exception` → HTTP 500; no `from e`, but `_log.exception` mitigates. | Documented by spec; acceptable if `_log.exception` is guaranteed. |
| `src/lyra/blobstore/_handlers.py` | 148 | **Low** | `handle_get` broad `except Exception` → HTTP 500; no `from e`, but `_log.exception` mitigates. | Same. |
| `src/lyra/blobstore/_handlers.py` | 289 | **Low** | `handle_delete` broad `except Exception` → HTTP 500; no `from e`, but `_log.exception` mitigates. | Same. |
| `src/lyra/integrations/vault_cli.py` | 97-111 | **Low** | `VaultCli.search` intentionally swallows `FileNotFoundError` and `TimeoutError`, returning graceful strings. | By design per docstring; no action needed. |
| `src/lyra/tools/gh_token/dispenser.py` | 182 | **Low** | `writer.wait_closed()` wrapped in `except Exception: pass` (cleanup boundary). | Documented with `DEBT:boundary-broad-catch`; acceptable. |
| `src/lyra/tools/gh_token/helper.py` | 158 | **Low** | `TokenCache.read` broad `except Exception` swallows all read errors to `None`. | Documented cold-cache fallback; acceptable. |
| `src/lyra/blobstore/auth.py` | 55 | **Low** | `_emit_unauthorized_audit` broad `except Exception: pass`. | Best-effort audit pattern; acceptable. |
| `src/lyra/blobstore/_handlers.py` | 66 | **Low** | `_emit_audit` broad `except Exception: pass`. | Best-effort audit pattern; acceptable. |

---

### Metrics

| Category | Count | Files | Note |
|----------|-------|-------|------|
| `except Exception` (or `except:`) without re-raise | 16 | 12 | Includes spec-approved blobstore handlers |
| Missing `raise ... from e` in boundary translation | 6 | 5 | All in `src/lyra/integrations/` |
| `str(exc)` leaks into CheckResult.detail | 5 | 3 | `monitoring/checks.py`, `checks_log.py`, `checks_varz.py` |
| Intentionally swallowed exceptions (empty `pass`) | 3 | 3 | `dispenser.py`, `auth.py`, `_handlers.py` audit helpers |
| Retry logic present (with or without backoff) | 0 | — | No retry loops found in this partition |

---

### Recommendations (prioritized, max 5)

1. **Add `from exc` to all integration boundary exception translations** (`audio.py`, `supervisor.py`, `systemctl.py`, `vault_cli.py`, `web_intel.py`). Mechanical fix across 6 catch blocks; preserves original `FileNotFoundError` / `JSONDecodeError` traceback context for operators.

2. **Narrow monitoring broad catches and sanitize `str(exc)` leaks.** Replace `except Exception` in `checks.py:86` and `checks_varz.py:65` with specific types (`httpx.HTTPError`, `OSError`, `json.JSONDecodeError`) and map to opaque detail strings (e.g., `detail="health endpoint unreachable"`). Prevents #1212-style internal exception text from reaching Telegram alerts.

3. **Tighten `monitoring/__main__.py` fallback exception scopes.** Catch only the exception types each layer is designed to handle (`RuntimeError`, `httpx.HTTPError`, `asyncio.TimeoutError`) instead of broad `Exception`, so unexpected bugs (e.g., `ValueError` in `_parse_diagnosis`) propagate and surface in cron logs rather than being silently swallowed.

4. **Clarify or remove the `retries` field in `MintError`.** `tools/gh_token/helper.py:mint()` always passes `retries=0` and never retries transient GitHub API failures. Either add bounded retry with exponential backoff, or remove the field to avoid misleading API consumers.

5. **Accept/document existing swallowed-exception patterns in blobstore audit helpers and gh_token dispenser cleanup.** These are already annotated with `DEBT` comments or spec justification (`best-effort audit must not break request`). No code change required; confirm they remain intentional.
