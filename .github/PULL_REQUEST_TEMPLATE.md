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

## RenderEvent changes (if applicable)

Tick this section when adding/modifying a `RenderEvent` subtype, the
`StreamProcessor` emission path, or any wire boundary in the hub↔adapter
streaming pipeline. Otherwise leave blank.

- [ ] `RenderEvent` union in `src/lyra/core/messaging/render_events.py` updated
- [ ] `NatsRenderEventCodec.encode` branch added (forced by `assert_never`)
- [ ] `NatsRenderEventCodec.decode` branch added with matching `event_type` string + schema-version check
- [ ] Codec round-trip test in `tests/nats/test_render_event_codec.py` for every new subtype
- [ ] `_shared_streaming_emitter.py` dispatch updated (consumer side)
- [ ] Wire-format docstring in `NatsRenderEventCodec` lists the new `event_type`
