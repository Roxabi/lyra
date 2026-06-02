<!--
PR template — quality-hygiene checklist (epic #1662, issue #1656).
Tick each box or strike it through with a one-line reason if N/A.
The matching CI gates will block the merge if a box is silently skipped.
-->

## Summary

<!-- What changed and why. Link the issue: Closes #NNNN -->

## Quality checklist

- [ ] **Debt** — no `DEBT:` marker added without an open issue link (gate: `check_debt_expiry`)
- [ ] **Test sleep** — no `sleep()` in `tests/` without an event-based / sync-window comment (gate: `check_test_sleep`)
- [ ] **Hardcoded constants** — no new numeric literal in `src/factory/core/` without a `Config`/`Protocol` reference (gate: `check_hardcoded_constants`)
- [ ] **Broad except** — no new `except Exception` without an inline boundary justification (axial + security review auto-labelled)
- [ ] **Adapter copy-paste** — no Telegram/Discord logic duplicated without a shared helper
- [ ] **Axial review** — `dev-core:axial-adr-review` passed if this PR crosses a top-level `factory.*` layer boundary

## Notes

<!-- Risks, follow-ups, deferred items (file siblings under the parent epic). -->
