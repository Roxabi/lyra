## Summary

<!-- What does this PR do? Link the issue: Closes #N -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring
- [ ] Documentation
- [ ] CI / Infrastructure

## Checklist

- [ ] Tests pass
- [ ] Lint passes
- [ ] Docs updated if needed
- [ ] Commits follow Conventional Commits format

## Quality hygiene (epic #1662)

<!-- Tick each box, or strike it through with a one-line reason if N/A.
     The matching CI gate blocks the merge if a box is silently skipped. -->

- [ ] **Debt** — no `DEBT:` marker added without an open issue link (gate: `check_debt_expiry`)
- [ ] **Test sleep** — no `sleep()` in `tests/` without an event-based / sync-window comment (gate: `check_test_sleep`)
- [ ] **Hardcoded constants** — no new numeric literal in `src/factory/core/` without a `Config`/`Protocol` reference (gate: `check_hardcoded_constants`)
- [ ] **Broad except** — no new `except Exception` without an inline boundary justification (axial + security review auto-labelled)
- [ ] **Adapter copy-paste** — no Telegram/Discord logic duplicated without a shared helper
- [ ] **Axial review** — `dev-core:axial-adr-review` passed if this PR crosses a top-level `factory.*` layer boundary

## RenderEvent changes (if applicable)

Tick this section when adding/modifying a `RenderEvent` subtype, the
`StreamProcessor` emission path, or any wire boundary in the hub↔adapter
streaming pipeline. Otherwise leave blank.

- [ ] `RenderEvent` union in `src/factory/core/messaging/render_events.py` updated
- [ ] `NatsRenderEventCodec.encode` branch added (forced by `assert_never`)
- [ ] `NatsRenderEventCodec.decode` branch added with matching `event_type` string + schema-version check
- [ ] Codec round-trip test in `tests/nats/test_render_event_codec.py` for every new subtype
- [ ] Consumer-side dispatch updated in `src/factory/outbound/emitter.py` (`OutboundEmitter`)
- [ ] Wire-format docstring in `NatsRenderEventCodec` lists the new `event_type`
