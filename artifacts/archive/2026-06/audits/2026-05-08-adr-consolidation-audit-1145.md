# ADR Consolidation Audit — Issue #1145

## Method

Read all 72 ADR files in `docs/architecture/adr/` (ADR-001 through ADR-071, with two files at
the 017 prefix). For each ADR: recorded status (from `## Status` body section), preamble signals
("Superseded by #666" etc.), and explicit body supersession statements. Classified every ADR
into one of: archive candidate, bucket source, canonical target, or live standalone. Counted
totals. Identified new buckets discovered during the read.

All 72 files confirmed present in `meta.json` pages array (including both 017 variants). Source
of status: body `## Status` section, not frontmatter.

---

## NATS Core Bucket → ADR-045

**Canonical target:** ADR-045 (Accepted) — "Extract roxabi-nats SDK as uv workspace subpackage"

**Sources to merge in:**

| ADR | Status | Merge rationale |
|-----|--------|-----------------|
| ADR-037 | Accepted | NatsOutboundListener placement + adapter standalone bootstrap — subsumed by ADR-045 design |
| ADR-040 | Accepted | NATS messaging architecture review (9 findings) — design decisions realized in ADR-045 |
| ADR-047 | Accepted | NATS connector ownership pattern — codifies boundaries established in ADR-045; references ADR-045 as prerequisite |
| ADR-062 | Accepted | NATS ACL inbox case normalization — directly extends ADR-045 SDK contract (`inbox_prefix` convention) |

**Also in this domain (live standalone, do NOT merge — own distinct scope):**

- ADR-035 (NATS subject naming convention — naming-only, orthogonal to SDK)
- ADR-036 (NatsChunkEnvelope streaming chunk protocol — wire protocol, not SDK)
- ADR-046 (nkey provisioning declarative authconf — provisioning tooling, not SDK)
- ADR-051 (per-identity inbox prefix security — ADR-062 supersedes Fix 2; ADR-051 itself remains the security invariant document)
- ADR-052 (registry-authoritative voice routing — routing algorithm, not SDK)
- ADR-064 (request-reply flows derivation — supersedes ADR-062 Fix 2, but is ACL tooling)
- ADR-065 (NATS KV readiness probe — specific pattern, not SDK)

**Bucket total sources:** 4 ADRs merge → 1 canonical; 7 standalone NATS ADRs remain live.

---

## NATS Contracts Bucket → ADR-049

**Canonical target:** ADR-049 (Accepted) — "Extract roxabi-contracts as shared schema package"

**Sources to merge in:**

| ADR | Status | Merge rationale |
|-----|--------|-----------------|
| ADR-044 | Accepted | lyra ↔ voicecli NATS voice contract — the first contract ADR; ADR-049 explicitly incorporates and extends it |
| ADR-050 | Accepted | lyra ↔ imagecli NATS image contract — the second contract ADR; same structural role as ADR-044 |

**ADR-066 placement decision (new ADR, see §Standalone 066–071):**
ADR-066 (Accepted) extends the `roxabi-contracts` wire envelope model with `WorkerError`. It is a
direct extension of ADR-049's additive-only rule. **Recommend merging ADR-066 into ADR-049** as a
"v2 additive extension" appendix section, or treating it as a live satellite to ADR-049 that is
not separately archivable.

**Bucket total sources:** 2 confirmed merge sources (ADR-044, ADR-050); ADR-066 is a judgment call
(see §Standalone 066–071 for full recommendation).

---

## Quadlet Bucket → ADR-055

**Canonical target:** ADR-055 (Accepted) — "Quadlet Ecosystem Conventions"

**Sources to merge in:**

| ADR | Status | Merge rationale |
|-----|--------|-----------------|
| ADR-053 | Accepted | Deployment topology + container hardening — ADR-055 explicitly lists ADR-053 as "Parent ADR"; Decisions 4+5 of ADR-053 already superseded by ADR-054 |
| ADR-054 | Accepted | Quadlet credential-store UID rework — supersedes ADR-053 Decisions 4+5; ADR-055 is the canonical downstream resolution |
| ADR-056 | Accepted | Container publishing workflow pattern — "Parent ADR: ADR-055"; reusable GHA workflow conventions |

**ADR-068 placement decision (new ADR, see §Standalone 066–071):**
ADR-068 (Accepted) is a narrow operational decision about `:z` label policy on bind-mount volumes.
It references the Quadlet deployment context but is scoped to a single deployment detail. **Recommend
merging into ADR-055** under an "Volume Policy" subsection.

**Bucket total sources:** 3 confirmed merge sources (ADR-053, ADR-054, ADR-056); ADR-068 is a
judgment call (see §Standalone 066–071).

---

## Architecture Bucket → ADR-059

**Canonical target:** ADR-059 (Accepted) — "Hexagonal / Clean Architecture Canonical Model"

**Sources to merge in:**

| ADR | Status | Merge rationale |
|-----|--------|-----------------|
| ADR-048 | Accepted | Introduce lyra.infrastructure layer — established the layer; ADR-059 generalizes and cites ADR-048 as a founding precedent |
| ADR-060 | Accepted | CLI protocol circular import resolution — a P0 remediation of a specific ADR-059 violation; small scoped ADR |
| ADR-061 | Accepted | importlinter independence contract fix — directly implements ADR-059 P0/P1 migration items |

**Bucket total sources:** 3 confirmed merge sources (ADR-048, ADR-060, ADR-061).

---

## ADR-017 Collision

Two files share the `017-` numeric prefix:

| File | Title | Status | Preamble signal |
|------|-------|--------|-----------------|
| `017-coupling-hotspots-and-decoupling-strategy.mdx` | Coupling hotspots and decoupling strategy | Accepted | "Superseded by #666" |
| `017-srp-violations-and-remediation-strategy.mdx` | SRP violations and remediation strategy | Accepted | "Superseded by #666" |

Both carry the `#666` preamble (AnthropicSdkDriver removed, Lyra is CLI-only) and are strong
archive candidates independently of the collision.

**Resolution:** Both files are archive candidates. When archiving:
- Keep `017-coupling-hotspots-and-decoupling-strategy.mdx` as the authoritative `017-*` filename
  (it came first in the naming sequence and matches the ADR content that originally occupied the
  slot before the collision was introduced)
- Renumber `017-srp-violations-and-remediation-strategy.mdx` to `072-srp-violations-and-remediation-strategy.mdx`
  (next available number as of this audit), or simply archive it without renumbering since it is
  also being archived

**Wave 2 action:** archive both; remove both slugs from `meta.json` and add (if archived-but-referenced)
a supersession note pointing to ADR-059 which covers architectural remediation as canon.

---

## Standalone ADR-066 through ADR-071 Placements

### ADR-066 — Unified WorkerError envelope (Accepted)
**Topic:** Adds `WorkerError` field to 5 NATS reply envelopes in `roxabi-contracts`.
**Placement:** Extends ADR-049 (roxabi-contracts). Explicitly states "Extends ADR-006 ... Builds on ADR-049".
**Recommendation:** **Merge into NATS Contracts bucket → ADR-049** as an additive-envelope appendix.
This is the strongest thematic fit — it is a wire-contract extension to the same package ADR-049
governs, using the same additive-only rules ADR-049 defines.

### ADR-067 — BlobStore abstraction (Accepted)
**Topic:** New `BlobStore` interface + SHA-256-addressed flat-FS + SQLite implementation; new
`packages/roxabi-blobs/` workspace member.
**Placement:** Standalone. Does not belong to any of the 4 spec'd consolidation buckets. New
architectural domain (binary asset storage, new workspace package).
**Recommendation:** **Live standalone ADR-067.** No merge target. Introduces a fourth workspace
subpackage (`roxabi-blobs`) alongside `roxabi-nats` and `roxabi-contracts` — this is an
architectural peer, not a subset of an existing bucket.

### ADR-068 — SELinux :z label policy (Accepted)
**Topic:** How to handle `:z` relabelling directive on AppArmor-only hosts for JetStream volume.
**Placement:** Quadlet deployment domain. Narrow operational decision; explicitly cites Quadlet units.
**Recommendation:** **Merge into Quadlet bucket → ADR-055**, under a "Volume Security Labels" or
"Bind-Mount Policy" subsection. The decision is too narrow to stand as a live ADR — it is a single
Quadlet deployment detail.

### ADR-069 — provision.sh warn_subid_overlap defensive posture (Accepted)
**Topic:** Bash-layer defensive fixes in `deploy/provision.sh` for `/etc/subuid` handling.
**Placement:** None of the 4 buckets. Provisioning script correctness.
**Recommendation:** **Live standalone ADR-069.** Narrow scope, but the ADR itself notes "No ADR
would normally be warranted for whitespace. This record exists because F2 and F5 together
establish a durable contract rule." The durable contract rule (advisory-path functions must
distinguish missing vs unreadable) justifies standalone status even though the scope is small.

### ADR-070 — RenderEvent v2 AG-UI modeling (Accepted)
**Topic:** Extends ADR-032's `LlmEvent → StreamProcessor → RenderEvent` pipeline with 4 new event
families (Run lifecycle, Text triplet, ToolCall split, Reasoning typed). Explicitly states it
"extends ADR-032" and "does not replace."
**Placement:** Core streaming pipeline domain. Extends (does not supersede) ADR-032. ADR-032 itself
was previously an archive candidate due to the "#666 preamble" — but ADR-032 is cited in ADR-070
as normative for "the CLI driver path", meaning the hexagonal pipeline contract portion of ADR-032
should be retained, not archived, even though the AnthropicSdkDriver content is stale.
**Recommendation:** **Live standalone ADR-070.** Keep ADR-032 partially live (remove the
archived-SDK content or add a partial-supersession banner; retain the hexagonal pipeline contract).
ADR-070 is a major architectural ADR covering a multi-slice epic and should remain distinct.

### ADR-071 — CLI pool Claude OAuth token mechanism (Accepted, 2026-05-07)
**Topic:** `type=env` Podman secret exception for `CLAUDE_CODE_OAUTH_TOKEN` injection into
`lyra-clipool`. Includes verification outcome for Podman bugs #28075 and #23788.
**Placement:** Quadlet deployment domain. Contains credential-delivery operational detail that
complements ADR-054. However, it is distinctly clipool-specific (not general Quadlet conventions).
**Recommendation:** **Live standalone ADR-071.** The verification recipe and threat-model scope
make it a reference document in its own right. Cross-reference from ADR-054 and ADR-055 is
sufficient; no merge required.

---

## meta.json Layout

**Current `meta.json`:** 72 slugs in `pages` array, flat (no sections).

**Recommendation for Wave 2:** keep flat ordering after archive operations. Fumadocs renders
`meta.json` pages in declared order; nested grouping is supported but not required. After
archiving, the remaining ~30–35 live ADRs need no reordering — numeric order is sufficient for
navigability. Do NOT introduce thematic grouping in `meta.json` in Wave 2; defer to a later
cosmetic pass if desired.

**What Wave 2 must do to `meta.json`:**
1. Remove all archived ADR slugs from the `pages` array
2. Remove both `017-*` slugs; add the renumbered replacement if 017-srp is kept rather than archived
3. Verify the merged canonical ADRs (045, 049, 055, 059) remain in the `pages` array (they stay live)
4. Add slugs for any new canonical ADRs if the consolidation creates them as new files

---

## Additional Buckets Discovered

Three implicit thematic clusters emerged that were not part of the original 4-bucket spec:

### NATS Security Cluster (no merge recommended — 4 live ADRs)
ADR-046, ADR-051, ADR-062, ADR-064 all address NATS ACL / nkey security. ADR-064 supersedes
ADR-062 Fix 2. These form a coherent security-posture narrative but each addresses a distinct
mechanism. No single canonical target exists; recommend leaving all 4 live and cross-referencing.

### Streaming Pipeline Cluster (no merge recommended — 2 live ADRs)
ADR-032 (hexagonal streaming — partially stale SDK content) and ADR-070 (RenderEvent v2 extension).
ADR-070 extends ADR-032; they form a linear evolution. Recommend a partial-supersession banner on
ADR-032 (referencing ADR-070 for the v2 extension) rather than a merge. Both must remain live.

### Error Handling Cluster (no merge recommended — 3 live ADRs)
ADR-006 (hub run-loop error gap), ADR-058 (typed error boundary — Proposed), ADR-066 (WorkerError
envelope — extends ADR-006). If ADR-066 is merged into ADR-049 (NATS Contracts bucket), the
remaining two (ADR-006, ADR-058) are thematically related but cover different layers. No merge target.

---

## Archive Candidates — Full List

All 13 strong archive candidates:

| ADR | Title | Reason |
|-----|-------|--------|
| ADR-012 | AnthropicAgent streaming dispatch | "Superseded by #666" preamble; SDK removed |
| ADR-016 | LLM Provider Protocol | "Superseded by #666" preamble; AnthropicSdkDriver removed |
| ADR-017a | Coupling hotspots and decoupling strategy | "Superseded by #666" preamble + 017 collision |
| ADR-017b | SRP violations and remediation strategy | "Superseded by #666" preamble + 017 collision |
| ADR-018 | Intermediate turns, typing indicator | "Superseded by #666" preamble; SDK-related |
| ADR-021 | Hub embedded per adapter | Body explicitly: "Superseded by ADR-035, ADR-037, ADR-040" |
| ADR-025 | Simplification audit | "Superseded by #666" preamble; completed work |
| ADR-028 | Token-level streaming path shape | "Superseded by #666" preamble + ADR-070 §References notes ADR-028 §Edge-Case Contract 3 superseded by Slice 3; partial supersession only — see note below |
| ADR-032 | LlmEvent → StreamProcessor → RenderEvent | "Superseded by #666" preamble for SDK content; BUT hexagonal pipeline contract portion cited as normative by ADR-070 — partial supersession only |
| ADR-034 | Brand asset pipeline | Body explicitly: "Superseded by ADR-042" |
| ADR-041 | Hub-and-spoke supervisor pattern | Body explicitly: "Superseded by ADR-047" |

**Note on ADR-028:** ADR-070 supersedes only one of ADR-028's four edge-case contracts (Edge-Case
Contract 3 — discarding `input_json_delta`). The remaining three contracts (cancel-in-flight,
session ID propagation, `--include-partial-messages` toggle) remain normative. Recommend a
partial-supersession banner rather than full archive. Mark with `superseded_by: "ADR-070 (partial — Edge-Case Contract 3 only)"`.

**Note on ADR-032:** Same situation. Mark with `superseded_by: "ADR-070 (partial — SDK streaming content; hexagonal pipeline contract remains in force)"`.

**Confirmed full archives (no partial rescue needed):** ADR-012, ADR-016, ADR-017a, ADR-017b,
ADR-018, ADR-021, ADR-025, ADR-034, ADR-041 = **9 full archives**.

**Partial supersession banners needed:** ADR-028, ADR-032 = **2 banners, not full archives**.

---

## Final Live Count Estimate

Starting from 72 total ADRs:

| Operation | Count change |
|-----------|-------------|
| Full archives (9 ADRs removed) | −9 |
| Bucket merge sources folded into canonical (ADR-037, ADR-040, ADR-047, ADR-062 → ADR-045) | −4 |
| Bucket merge sources folded into canonical (ADR-044, ADR-050 → ADR-049; ADR-066 → ADR-049) | −3 |
| Bucket merge sources folded into canonical (ADR-053, ADR-054, ADR-056, ADR-068 → ADR-055) | −4 |
| Bucket merge sources folded into canonical (ADR-048, ADR-060, ADR-061 → ADR-059) | −3 |
| **Total removed** | **−23** |
| Remaining live | **49** |

**49 is above the SC-5 target of 25–35.** Closing the gap requires additional discretionary
archives beyond the 9 confirmed above. The following are the next strongest candidates:

| ADR | Basis for additional archive consideration |
|-----|------------------------------------------|
| ADR-007 | ModelConfig mismatch silent-ignore — Phase 1 only; Phase 2 (respawn) described but may be complete |
| ADR-019 | Multi-bot resource sharing (Proposed) — scope may be absorbed by ADR-045/047 satellite pattern |
| ADR-020 | CLI entry point dispatch (Proposed) — very narrow; completed |
| ADR-027 | 300-line cap refactoring plan — plan is likely fully executed; completed work |
| ADR-033 | PuLID Flux2 face-lock — imageCLI/brand concern, not Lyra core |
| ADR-043 | roxabi-autodeploy per-project manifests (Proposed) — ops tooling not yet built |
| ADR-069 | provision.sh warn_subid_overlap — extremely narrow operational detail |

Archiving the 7 above: 49 − 7 = **42**, still above 35. Further discretionary review needed in
Wave 2 to reach 25–35. The count target may require archiving some "Proposed" status ADRs that
address features not yet implemented, and some "Accepted" ADRs whose decisions are fully absorbed
into larger canonical documents.

**Conservative estimate after confirmed operations:** 49 live ADRs.
**Optimistic estimate after additional discretionary archives:** ~35–40 live ADRs.
**Reaching SC-5 (25–35):** requires Wave 2 to make discretionary archive decisions on an additional
~7–15 ADRs beyond the confirmed 23 removals.

---

## Wave 2 Hand-off

### Confirmed operations for Wave 2 to execute

**Archives (remove from meta.json, add superseded_by frontmatter, move to archive/)**:
ADR-012, ADR-016, ADR-017a (coupling-hotspots), ADR-017b (srp-violations), ADR-018, ADR-021,
ADR-025, ADR-034, ADR-041

**Partial supersession banners (add to frontmatter; keep in meta.json, keep live)**:
ADR-028, ADR-032

**Merges (content from source ADRs absorbed into canonical; then archive sources)**:

NATS Core → ADR-045:
- Merge ADR-037, ADR-040, ADR-047, ADR-062

NATS Contracts → ADR-049:
- Merge ADR-044, ADR-050, ADR-066 (recommended; confirm before merging ADR-066)

Quadlet → ADR-055:
- Merge ADR-053, ADR-054, ADR-056, ADR-068 (recommended; confirm before merging ADR-068)

Architecture → ADR-059:
- Merge ADR-048, ADR-060, ADR-061

**meta.json:** Remove archived slugs; retain all canonical targets + live standalones.

### ADR-017 renumber

- `017-coupling-hotspots-and-decoupling-strategy.mdx` → archive (full)
- `017-srp-violations-and-remediation-strategy.mdx` → archive (full, no renumber needed since archiving)
- If one must be kept live (not anticipated), the srp file should become `072-srp-violations-...`

### Open judgment calls for Wave 2

1. **ADR-066 merge vs standalone**: Merging into ADR-049 is thematically correct; standalone is
   also acceptable. Confirm with lead before Wave 2 merge step.
2. **ADR-068 merge vs standalone**: Merging into ADR-055 is recommended but the decision is minor
   enough it could stay standalone without editorial loss.
3. **Reaching SC-5**: The confirmed operations leave 49 live ADRs. Wave 2 must make discretionary
   archive decisions to reach 25–35. Prioritize Proposed-status ADRs with no active implementation
   (ADR-019, ADR-020, ADR-023, ADR-043, ADR-058) plus completed-plan ADRs (ADR-027).
4. **ADR-032 vs full archive**: If the hexagonal pipeline documentation in ADR-032 is fully
   superseded by ADR-070's Decision + References sections, ADR-032 may be fully archived with a
   `superseded_by: ADR-070` frontmatter. Current recommendation is partial banner; confirm in Wave 2.
