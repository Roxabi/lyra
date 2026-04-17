# Changelog

All notable changes to this project will be documented in this file.
Entries are generated automatically by `/promote` and committed to staging before the promotion PR.

## [Unreleased]

### Subpackage changes (see subpackage CHANGELOGs for detail)

- `roxabi-nats` — `CONTRACT_VERSION` moved to `roxabi_contracts.envelope` (ADR-049); a compat re-export remains in `roxabi_nats.adapter_base` and the top-level `roxabi_nats` package with a `DeprecationWarning` and is scheduled for removal at `roxabi-nats/v0.3.0` (BREAKING CHANGE). See `packages/roxabi-nats/CHANGELOG.md`.

## [v0.1.0] - 2026-03-06

### Fixed
- fix(ops): make setup.sh idempotent, enable gws check, use bash for agent

### Changed
- docs(roadmap): restructure into phase-based layout with issue tracking
- docs(plan): add implementation plan for #81 roxabi-memory package foundation
- docs(spec): add spec for #9 memory layer Phase 1
- docs(frame): add approved frame for #9 memory layer Phase 1
- chore: remove memory-audit plugin
