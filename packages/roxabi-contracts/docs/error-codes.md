# WorkerError code registry

> Auto-generated from `roxabi_contracts.errors.KNOWN_CODES`.
> Do not edit by hand — run `uv run python packages/roxabi-contracts/scripts/check_codes_sync.py --write` to regenerate.
> The pre-commit `codes-sync` hook regenerates this file automatically when `errors.py` changes.

## transport.*

| code | retryable | description |
|------|-----------|-------------|
| transport.timeout | true | NATS request timed out before a reply was received. |
| transport.no_responders | true | NATS returned a no-responders status; no subscriber on the subject. |
| transport.parse | false | Inbound NATS payload could not be parsed (malformed JSON or schema mismatch). |
| transport.contract_mismatch | false | CONTRACT_VERSION or schema shape does not match what this consumer expects. |
| transport.slow_consumer | true | NATS slow-consumer detected; message dropped by the broker. |
| transport.error | true | Generic NATS / network transport failure not covered by a more specific code (e.g. connection reset, protocol error). |
| transport.payload_too_large | false | Request payload exceeded the NATS server's max_payload limit. |

## pool.*

| code | retryable | description |
|------|-----------|-------------|
| pool.circuit_open | true | WorkerPoolClient circuit breaker is open; call short-circuited without dispatching to a worker. |
| pool.no_live_workers | true | WorkerPoolClient exhausted its registry without reaching a healthy worker. |

## worker.*

| code | retryable | description |
|------|-----------|-------------|
| worker.crash | true | Worker process raised an unhandled exception. |
| worker.validation | false | Request payload failed domain-level validation inside the worker. |
| worker.internal | true | Worker encountered an internal error not covered by a more specific code. |
| worker.capacity | true | Worker rejected the request because its capacity limit (queue or pool) is exhausted; caller should retry after back-off. |
| worker.busy | true | Worker rejected the request because its concurrency limit is reached. |

## cli.*

| code | retryable | description |
|------|-----------|-------------|
| cli.auth | false | CLI pool authentication failed (invalid or expired credentials). |
| cli.session_lost | true | CLI session was lost and could not be resumed. |
| cli.parse | false | CLI command string could not be parsed. |

## llm.*

| code | retryable | description |
|------|-----------|-------------|
| llm.rate_limit | true | LLM provider returned a rate-limit / quota-exceeded error. |
| llm.context_too_long | false | Input tokens exceed the model's context window. |
| llm.model_unavailable | true | Requested LLM model is temporarily or permanently unavailable. |
| llm.no_responders | true | No LLM worker is subscribed on the expected NATS subject. |
| llm.lifecycle_rejected | false | Lifecycle operation rejected by the worker (unknown model, engine=remote, VRAM budget exceeded, or catalog parse error). |

## voice.*

| code | retryable | description |
|------|-----------|-------------|
| voice.engine_unavailable | true | Voice engine (TTS/STT) is not reachable or has not started. |
| voice.audio_invalid | false | Audio payload is malformed, too short, or in an unsupported format. |

## image.*

| code | retryable | description |
|------|-----------|-------------|
| image.engine_unavailable | true | Image generation engine is not reachable or has not started. |
| image.prompt_rejected | false | Image prompt was rejected by the engine's content policy. |

## stream.*

| code | retryable | description |
|------|-----------|-------------|
| stream.error | false | Unhandled exception during hub-side stream processing (StreamProcessor), or an un-categorized soft error surfaced via SanitizedError.from_message. |

See ADR-066 (absorbed into ADR-049) for design rationale.
