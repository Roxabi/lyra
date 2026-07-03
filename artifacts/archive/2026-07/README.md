# Artifact archive — 2026-07

Graduation wave (#2218): three `artifacts/` files that ADR-091/092/096 wrongly
cited as their **"current truth"** — a contradiction of
[ADR-086](../../../docs/architecture/adr/086-documentation-architecture.mdx)
(`artifacts/` = deltas only) and a blind spot for the doc-drift gates (which
exempt `artifacts/**`). Their invariants were migrated into the
`docs/architecture/observability.md` domain page; the ADR "current truth"
banners now point at that page. These files are preserved as the historical
design/implementation deltas.

| File | Was cited by | Current truth now |
|------|--------------|-------------------|
| `sentinelle-four-planes-spec.mdx` | ADR-091 (current truth), ADR-092/096 (see also) | `docs/architecture/observability.md` § Four observability planes |
| `otel-raw-store-spec.mdx` | ADR-092 § 6 (full implementation spec) | `docs/architecture/observability.md` § Trace engine v1 — otel-raw |
| `ingress-connector-tenant-contract-goal.md` | ADR-096 (current truth) | `docs/architecture/observability.md` § Ingress — connector registry + tenant scoping |

See [`artifacts/README.md`](../../README.md) for the retention policy and the
archive-wave tooling.
