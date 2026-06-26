## B8-10 Reconciliation

`test_soft_error_emits_finished_not_error` (L14, deleted) asserted:

```python
assert isinstance(result[-1], RunFinishedRenderEvent)
assert result[-1].outcome == "success"
assert not any(isinstance(e, RunErrorRenderEvent) for e in result)
```

for an input of `ResultLlmEvent(is_error=True, ...)`.

`StreamProcessor` lines 391–401 currently does the opposite — when
`_result_is_error=True`, it emits `RunErrorRenderEvent` as the terminal event.
B8-10 (`test_is_error_propagated_to_text_render_event` at line 756,
`test_is_error_run_error_carries_error_text` at line 789, both active) asserts
this behavior.

**Decision: delete `test_soft_error_emits_finished_not_error`.** Grounds:

1. The live `StreamProcessor` already implements the B8-10 contract; restoring
   the skipped test would either (a) require changing production behavior away
   from a contract that is already shipped and asserted, or (b) coexist with
   B8-10 as a contradictory assertion against the same input.
2. The "soft error → RunFinished + TextRenderEvent.is_error=True" surface from
   v1 belongs to the v1 wire that #1192 S3 removed. v2 carries error semantics
   on the terminal `RunErrorRenderEvent.message` plus per-block
   `TextEndRenderEvent.is_error` / `TextChunkRenderEvent.is_error`. The skipped
   test asserts both a removed type (`TextRenderEvent`) and a removed contract.
3. B8-10's coverage of the is_error path is already present and active.

## Deletion rationale

- **L14 `test_soft_error_emits_finished_not_error`**: contradicts active B8-10
  tests (`test_is_error_propagated_to_text_render_event` +
  `test_is_error_run_error_carries_error_text`) which assert `RunErrorRenderEvent`
  for `is_error=True`; the skipped test asserted `RunFinishedRenderEvent` for the
  same input — mutually exclusive contracts, B8-10 wins.
- **L16 `test_dual_emit_v1_text_alongside_reasoning_delta`**: asserts v1
  `TextRenderEvent` dual-emit alongside `ReasoningDeltaRenderEvent` — v1
  `TextRenderEvent` removed in #1192 S3; no dual-emit path exists in production.
- **L17 `test_text_end_before_v1_fallback_on_truncation`**: asserts `TextEnd`
  ordering relative to v1 fallback `TextRenderEvent` — v1 fallback truncation
  path removed in #1192 S3; no v1 fallback to order against.
- **L18 `test_v1_parity_against_baseline_fixture`**: compares live output to a
  v1 baseline fixture filtered to `TextRenderEvent`/`ToolSummaryRenderEvent` —
  v1 wire removed in #1192 S3; no v1 parity target exists.
