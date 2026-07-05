# Research digest — multi-axis codebase SSoT model (2026-07-04)

> Consolidated from 16 web-research angles + 3 repo-grounding agents (wf_d1b6b726). Feeds the design spec `2026-07-04-multi-axis-ssot-model.md`.

---

# DIGEST — Multi-Axis Codebase-SSoT Model (16 research angles × roxabi-factory grounding)

Feeds 4 independent designers. Axes = [Ontology, Topology, Structure, Behavior, Delta, Procedure]. Strata = [L0 generated/gated · L1 intent/invariants · L2 procedure · L3 residue].

---

## 1. Convergent principles (≥3 independent angles agree)

**P1 — A red build binds an agent; prose in an instructions file does not.** Machine-checkable rules dominate documentation because agents self-correct against a failing gate and ignore advisory text. Encode every machine-decidable rule as a gate whose failure message *is* the doc. [5+ angles: fitness-functions, living-docs, governance-at-scale, code-as-data, module-boundaries]. → https://aipatternbook.com/architecture-fitness-function · https://www.archunit.org/userguide/html/000_Index.html

**P2 — Generate, never hand-maintain, anything a schema-governed extractor can emit; and an ungated generated artifact is *faster* rot, not truth.** "Reference"/inventory/counters/graphs must be machine-produced AND gated; the entire value is freshness enforcement (`continue-on-error = no gate`). [8+ angles: fitness-functions, C4/Structurizr, Diátaxis (reference *ideally* auto-generated), code-as-data, agent-retrieval, living-docs, epistemics, program-comprehension]. → https://diataxis.fr/reference/ · https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/

**P3 — One canonical model (facts), many derived views; stop letting each consumer re-derive the same truth.** Structurizr `model{}`/`views{}`, Kythe hub-and-spoke O(L×C×B)→O(L+C+B), Context Mapper generated context map, reverse-engineered-architecture-as-SSoT all say: facts are extensional and generated once; docs/diagrams/rules are queries over them, never parallel hand-edited copies. This is the structural cure for N-way drift. [5 angles: C4/Structurizr, DDD, code-as-data, epistemics, governance]. → https://docs.structurizr.com/as-code · https://kythe.io/docs/kythe-overview.html

**P4 — Text-as-code is the precondition for agent participation.** Agents generate, diff, and verify text; they cannot author or verify UI-tool/whiteboard state. Any SSoT component an agent must maintain is text-first even if a rendered diagram is the human artifact. This is *the* single most on-point argument for the whole model. [4 angles: C4/Structurizr (explicit quote), code-as-data, agent-retrieval, llmdocs]. → https://docs.structurizr.com/as-code

**P5 — Names are the primary retrieval key; "titre = contrat" is empirically validated, not local dogma.** GrepRAG beats dense+graph RAG on explicit-identifier retrieval (7–15% EM, 100–350× faster); Biggerstaff's concept-assignment gap and Soloway's "rules of discourse" show a misleading name breaks even expert comprehension. Concept names must appear verbatim as code identifiers so lexical retrieval resolves concept→code with zero embedding infra. [6 angles: fitness-functions (ArchUnit naming rules), DDD, agent-retrieval, ADR (title=contract), epistemics (thesaurus), program-comprehension]. → https://arxiv.org/html/2601.23254v1 · https://www.cs.kent.edu/~jmaletic/Prog-Comp/Papers/biggerstaff93.pdf

**P6 — Homonyms are unavoidable and cured ONLY by contextual scoping; a single flat global glossary is wrong by construction.** Vernon: one enterprise-wide ubiquitous language *will* fail. The same slug ("worker", "event", "plane") gets multiple entries keyed by bounded context, plus an explicit homonym/disambiguation table. [3+ angles: DDD, epistemics, ontology-KG]. → https://martinfowler.com/bliki/BoundedContext.html · https://www.oreilly.com/library/view/domain-driven-design-distilled/9780134434964/ch02.html

**P7 — Delta/blast-radius is a *derived view*, not an axis or a stored artifact; baseline legacy debt and block only NEW violations.** FreezingArchRule (commit snapshot, report-only-new, auto-shrink, monotonic decrease), file-incremental fanout, VCS temporal-coupling, and the epistemics angle all converge: "what a change endangers" = transitive closure over Topology(talks-to)+Structure(imports) ∩ Behavior(invariants), computed on demand. Hand-maintaining it guarantees rot. [5 angles: fitness-functions, code-as-data, behavioral-analysis, epistemics, program-comprehension]. → https://www.archunit.org/userguide/html/000_Index.html · https://codescene.com/product/behavioral-code-analysis

**P8 — The SSoT is a ranked, random-access, progressively-disclosed index, not a linear document.** Comprehension is opportunistic altitude-switching (von Mayrhauser); winning agent tools rank-to-budget (aider PageRank + chat-file 50× reranking) and load stratum N+1 only on commitment (Agent Skills 3-tier, Anthropic just-in-time). Every agent is an "as-needed reader": every axis must be retrievable alone, every procedure self-contained. [4+ angles: agent-retrieval, Diátaxis, program-comprehension, llmdocs/MCP]. → https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents · https://www.cs.kent.edu/~jmaletic/cs63902/Papers/ProgramComprehension/von_mayrhauser95.pdf

**P9 — Doc rot is structural (asymmetric feedback loops), so sync must be enforced by commit discipline, not human diligence.** Parnas software-aging: passive docs decay monotonically; code has continuous compiler/test/CI feedback, docs don't. Fix = same-commit binding (spec updates in the identical commit that changes the contract), gated on path-pairs. [4 angles: living-docs, llmdocs (update-trigger tied to PR), behavioral-analysis, fitness-functions (expiring exemptions)]. → https://dl.acm.org/doi/10.1145/3643916.3644399 · https://asdlc.io/patterns/the-spec/

**P10 — Pick ONE primary decomposition axis; project the others as cross-cutting views, never as parallel physical hierarchies (the N×M trap).** Two co-equal hierarchies (by-layer src AND by-feature docs) force each to enumerate the other and drift independently. Store by the correct KOS structure; *index* by intent. [3+ angles: hexclean (axis-of-decomposition), epistemics (faceted post-coordination + orthogonality test), module-boundaries (tag-based over path-based)]. → https://alistair.cockburn.us/hexagonal-architecture · https://berkeley.pressbooks.pub/tdo4p/chapter/faceted-classification/

**P11 — Few owned, always-blocking gates beat a wall of half-enforced checks; exemptions must self-invalidate.** Guidance: start with 3–6 fitness functions, each with owner+baseline+threshold; false-positive fatigue → bypass behavior; `unmatched_ignore_imports_alerting=error` / `expires=YYYY-MM-DD` tokens delete stale waivers. [4 angles: fitness-functions, governance-at-scale, living-docs (start with 1 ADR, iterate 5×), module-boundaries (exemption-creep antipattern)]. → https://continuous-architecture.org/practices/fitness-functions/ · https://import-linter.readthedocs.io/en/v1.12.1/contract_types.html

---

## 2. Best transferable patterns, by axis

### Ontology
- **DDD per-context registry + homonym table** (Fowler/Vernon): key each term by bounded context; ship an explicit homonym table with a one-line disambiguation *rule* per overloaded word. **Apply:** create `docs/architecture/concepts.md` (does not exist today) as a thin pointer/disambiguation index, modeled on the proven `workers-tooling.md` "two admitted altitudes" pattern. → https://martinfowler.com/bliki/BoundedContext.html
- **Schema-as-ontology** (Glean/CodeQL): a concept absent from the schema literally cannot be stored; derive the L1 glossary FROM identifiers, don't hand-type it. **Apply:** generated concept→location index is the highest-leverage L0 Ontology artifact (closes Biggerstaff's gap). → https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/
- **Thesaurus layer** (ISO 25964/SKOS): preferred term + aka/synonyms (USE/UF) + "distinguish from <sibling>" scope note. This is exactly what "how named, how distinguished" *is* — terminological control, a rung *below* full ontology. → https://taxodiary.com/2026/01/taxonomy-thesaurus-or-ontology-clearing-the-confusion-part-two/
- **ArchUnit naming rules** (name predicts package/location): make a mis-named/mis-placed concept a red build. → https://www.archunit.org/userguide/html/000_Index.html

### Topology
- **C4 Context+Container = topology, as a generated model** (Structurizr): nodes=deployable units, edges=runtime comms, generated from real deploy manifests. **Apply:** `CURRENT.generated.md` already does this (best-gated axis); reframe explicitly as a **context map** and add typed edges. → https://c4model.com/
- **DDD Context Map with typed relationship edges** (Context Mapper CML): each edge carries direction+power semantics (Shared Kernel / Published Language / Conformist / ACL). One typed declaration answers Topology (they talk) + Structure (who conforms) + Delta (who breaks). **Apply:** `roxabi-contracts` = Published Language; adapters = ACL; encode direction, not bare arrows. → https://contextmapper.org/docs/context-map/
- **Split containment from communication** (mereology): "runs-where" (process⊑host⊑cluster) is transitive containment; "talks-to" is an *intransitive* directed graph. Declare each edge's transitivity so gates/agents never chain talks-to through contains-in. → https://en.wikipedia.org/wiki/Mereology

### Structure
- **import-linter layers/forbidden/independence** — already the `import_layers` gate (`.importlinter`, 15 contracts). Reads indirect/laundered imports grep misses. **Limitation to fix:** module-level, repo-wide, not symbol-level or diff-scoped. → https://import-linter.readthedocs.io/en/v1.12.1/contract_types.html
- **Hexagonal inside/outside as the load-bearing axis** (Cockburn): the real asymmetry is inside vs outside (core never imports adapter), NOT left/right or technology-layer. Four independently-invented architectures (Hexagonal/Clean/Onion/DDD) converge on this one dependency-direction axis — evidence it's fundamental. The AGENTS.md axial-review trigger (`inbound/+adapters/`, `core/+infrastructure/`) *is* this line as a CI trigger. → https://alistair.cockburn.us/hexagonal-architecture
- **Tag-based constraints over path-based** (Nx/Bazel `package_group`): tags survive reorganizations; transitive-visibility catches A→B→C chains lint misses. → https://nx.dev/docs/features/enforce-module-boundaries · https://bazel.build/concepts/visibility
- **FreezingArchRule baseline** to adopt a stricter rule on legacy code without a big-bang cleanup. → https://www.archunit.org/userguide/html/000_Index.html

### Behavior
- **A fitness function is an executable invariant; rule text = invariant statement AND enforcement** (ArchUnit) — collapses L1-intent and L0-enforcement into one artifact that cannot drift apart. **Apply:** grounding shows invariants #3/#4/#5/#6 (agent=stateless singleton; adapters send trust=PUBLIC; memory query includes user_id; bounded Queue maxsize=100) are prose-only. Convert to gates where a proxy exists.
- **Invariants-as-queries / variant analysis** (CodeQL): "this must never happen" encoded once, run everywhere; a non-empty result gates merge. **Apply:** AST/grep checks — dashboard routes must carry `Depends(require_operator)` (closes the IDOR/auth P0s), memory-query call sites must pass `user_id`. → https://codeql.github.com/docs/codeql-overview/about-codeql/
- **Executable-spec/Gherkin wired to a real test runner** = drift alarm (red test = "doc now wrong"). Where an invariant *can* be a failing test, it belongs in code (pytest/`str_exc_bus_bound`), not a Markdown paragraph. Decorative-Gherkin-written-after-code is *worse* than an admittedly-manual doc (false confidence). → https://asdlc.io/patterns/the-spec/
- **Diátaxis-split each invariant internally**: a short reference-style invariant statement (gated/testable) + a *linked* explanation of why — one paragraph must not do both jobs. → https://diataxis.fr/reference/
- Where an invariant is genuinely not machine-decidable, record it as a **Manual fitness function with a named owner** — never pretend prose enforces it.

### Delta
- **FreezingArchRule / baseline** = the reference Delta gate: committed violation snapshot, block only new, auto-shrink, monotonic decrease → free debt burndown. Run it ON the change branch (not a sibling) or fresh additions are falsely grandfathered. → https://www.archunit.org/userguide/html/000_Index.html
- **File-incremental fanout / reverse-dependency query** (Glean/stack-graphs): scope re-verification to the change's fanout, not the whole repo. **Apply:** the grounding's recommended Tier-2 diff→symbol→pytest impact check (extend `CodeInventory`), emitting WARN/BLOCK when a changed symbol has no touching test. This is the axis with *zero* machine verdict today (only the `axial-review` label + human review). → https://github.blog/open-source/introducing-stack-graphs/
- **VCS temporal coupling + hotspots** (CodeScene/Code Maat): files that change together expose *unexpected* coupling static analysis can't see; tie gate strictness to hotspot status. Keep the change-coupling graph as a generated L0 Delta signal, distinct from narrative changelog (L3). → https://codescene.com/product/behavioral-code-analysis
- **ADR supersede/link graph** (adr-tools `-s`/`-l`) = a decision dependency graph; editing an upstream decision's premise tells you which downstream ADR consequences are endangered. → https://github.com/npryce/adr-tools

### Procedure
- **Agent Skills 3-tier progressive disclosure** (SKILL.md): metadata line in the index → full runbook on match → scripts pulled only at the executing step. Steal verbatim for L2. → https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- **Link out, don't inline** (Diátaxis): a procedure REFUSES rationale (link to L1); an intent doc REFUSES exact commands (link to L2). Lint-able boundary rule. → https://diataxis.fr/
- **Self-contained/local procedures** (program-comprehension): a procedure with non-local preconditions ("first read X") is a delocalized plan that fails silently for the as-needed reader/agent. → https://www.cs.kent.edu/~jmaletic/cs63902/Papers/ProgramComprehension/von_mayrhauser95.pdf
- **Runtime-sourced over hand-typed** (living-docs): prefer a generated command table / `--help` over a hand-maintained step list. **Apply:** grounding — Procedure is ungated by design (~20 high-confidence drifts in the 2026-07-03 audit); add an operator-path CI smoke (`factory bot init`), THEN remove the doc_drift onboarding exemption.

---

## 3. Rigor corrections (epistemics angle vs the project's own vocabulary)

1. **"Ontology axis" is misnamed — it is a controlled vocabulary / thesaurus, not an ontology.** In formal KO, `ontology = taxonomy + axioms`. "How named / how distinguished" is *terminological control* (a level below ontology). Rename it the **Glossary/Terminology axis**. → https://taxodiary.com/2026/01/taxonomy-thesaurus-or-ontology-clearing-the-confusion-part-two/

2. **The sharpest finding: the "Behavior axis" (invariants) IS the ontology's axiom layer.** The invariants are exactly what upgrades a taxonomy into an ontology. So Ontology and Behavior are **one ontology at two expressiveness levels, not two orthogonal axes** — separating them lets a concept exist with no axioms and an invariant float with no concept. Bind every invariant to the glossary term it constrains.

3. **"6 orthogonal axes" fails the orthogonality test.** (a) **Delta is a derived view**, computable from Topology+Structure ∩ Behavior — a facet computable from other facets is a query, not a dimension; demote it to a computed lens/tool. (b) Ontology↔Behavior are correlated (same thing, two altitudes). → https://berkeley.pressbooks.pub/tdo4p/chapter/faceted-classification/

4. **The axes are not the same KIND of thing** (Baker's shapes-vs-types error): they mix relation-graphs (Topology, Structure), axioms (Behavior), a derived view (Delta), a controlled vocabulary (Ontology), and a document genre keyed to reader-intent (Procedure). They are **excellent as retrieval intents, wrong as storage buckets.** The real 2-D grid is **(retrieval-intent × altitude)**, not (axis × stratum) treated as peer classifications. → https://everypageispageone.com/2012/07/28/the-tyranny-of-the-terrible-troika-rethinking-concept-task-and-reference/

5. **"Topology" conflates two relation types with different algebra:** containment/location ("runs-where", mereological, transitive) vs communication ("talks-to", graph, *intransitive*). Bundling them invites the intransitive-chaining bug ("the musician's hand is part of the orchestra" fallacy) in deployment/ACL reasoning. → https://eng.libretexts.org/Bookshelves/Computer_Science/Programming_and_Computation_Fundamentals/An_Introduction_to_Ontology_Engineering_(Keet)/07:_Top-Down_Ontology_Development/7.03:_Part-Whole_Relations

6. **"Structure = who may import whom" is a poset PLUS a deontic layer, spanning two strata and two epistemic kinds.** The import graph is an extensional fact (L0, generated); the "MAY" is an intensional invariant (L1, `.importlinter`). The single word hides that split — the tooling already separates them; the model's vocabulary should too.

7. **The project's own "axial" is a genuine homonym** (see §4): stage/platform decomposition axis (ADR-073 family) vs the hexagonal layer-boundary CI trigger (`axial-adr-review` label). These are *orthogonal* senses.

8. **The 4 strata are the better-behaved facet** — they *are* orthogonal to intent and encode the extensional→intensional→procedural→provenance gradient (L0=term-list/inventory rung, L1=ontology+axioms, L2=DITA task genre, L3=provenance). Adopt "weakest sufficient KOS structure per altitude": don't over-model L0 or L3 as an ontology.

9. **Don't let folksonomy masquerade as ontology.** GitHub `dev-core:*` labels are flat, uncontrolled, post-hoc tags — right for triage, wrong as the SSoT for import structure or invariants.

---

## 4. Homonym / concept-registry evidence (from grounding, grep-verified)

**Meta-finding:** `docs/architecture/concepts.md` does not exist; the pain is not "nobody explained it" but "the explanation lives in one domain page and isn't discoverable from the sibling page that reuses the word." The source analysis contained **at least one fabricated cross-reference** — re-verify every row with grep-and-cite before shipping, because a registry that enshrines a false homonym is worse than none.

| Term | Verdict | Sense A → canonical | Sense B → canonical | Disambiguation rule |
|---|---|---|---|---|
| **plane** | CONFIRMED (3 senses) | 3 NATS transport planes (Messages/Persistence/Typing) → messaging.md, ADR-076 | 4 observability signal-kind planes ①-④ → observability.md, ADR-091 | numbered ①–④ = observability; unnumbered = transport wire. 3rd sense: generic "control-plane" = the dashboard BFF/auth surface, never a NATS/obs plane. ADR-076 and ADR-091 do NOT cross-reference each other. |
| **event** | CONFIRMED (2) | external wire subject `factory.event.*` → infrastructure/events, ingress/ | in-process `RenderEvent` primitive → core/messaging/render_events.py, ADR-100 | capitalized `RenderEvent` = internal; subject literal `factory.event.*` = external |
| **axial/axis** | CONFIRMED but re-scoped | stage-vs-platform decomposition axis → ADR-073 (ADR-079 *applies* it to audio; NOT a 2nd sense) | hexagonal layer-boundary CI trigger (`axial-adr-review` label) → AGENTS.md §Axial Review, axial-review.yml | "axis of decomposition" = ADR-073 family; the GH label = layer-crossing gate, unrelated axis. **The analysis's claimed ADR-073-vs-ADR-079 homonym is wrong.** |
| **worker** | CONFIRMED, self-documented | compute-on-engine consumer → workers-tooling.md | `WorkerRegistry` class (legacy misnomer, actually a heartbeat/provider registry) → src/factory/nats/worker_registry.py | treat WorkerRegistry as known naming debt, not a 2nd valid concept. Already flagged inline in workers-tooling.md — gap is only the transversal pointer. |
| **turn** | CONFIRMED, self-documented | conversation/session unit → job-model.md ("a job is not a turn") | deprecated turn-granularity job-id in ADR-084, superseded by job_id=run per #1794 | any "turn" in an ADR predating #1794 is stale terminology |
| **satellite** | **REFUTED — drop it** | provider deployed as self-hosted NATS process w/ heartbeat (⊂ provider) → workers-tooling.md §Rule2 | — | Single-sense term. The claimed ADR-091/host-sensor second sense returns **zero grep hits**. Do NOT add as a homonym (optionally add as a clarifying non-example). |
| **job** | no confirmed 2nd sense | dispatched unit of work → job-model.md | — | needs a follow-up grep before adding |
| **provider** | (add for clarity) | tool-backing umbrella (⊇ satellite) → workers-tooling.md | — | provider = umbrella; satellite = strict subset (self-hosted+heartbeat), not a synonym |

**Verdict:** 4 of 6 claimed terms (plane, event, axial, worker/turn) hold up with file:line evidence; for worker/turn/axial the local domain pages *already* disambiguate inline — the real gap is a **cross-page index**, not zero documentation. Registry should be a **thin pointer table** (term | sense | canonical doc | one-line rule) per the ADR-086 "intent lives in domain pages, this is just an index" doctrine — do not duplicate the definitions.

---

## 5. Enforcement menu (every machine-verifiable idea → axis it protects)

Legend: **[E]** exists in roxabi-factory today · **[N]** new idea from research/grounding.

| Axis | Mechanism | Type | Source |
|---|---|---|---|
| Topology | `architecture_snapshot`, `quadlet_manifest_install`, `volumes_table`, `acl_grants`, `acl_specs_drift`, `acl_authconf_drift`, `acl_matrix_retired`, `request_reply_flows`, `inbox_prefix`, `subject_literals`, `secrets_drift`, `quadlet_component_source` **[E]** | gate + generated artifact (`CURRENT.generated.md`) | grounding |
| Topology | Context Mapper CML validation (context named in edge must be declared) + generate diagram from model **[N]** | gate | https://contextmapper.org/docs/context-map/ |
| Topology | typed-edge transitivity check (block chaining talks-to through contains-in) **[N]** | gate | mereology (Keet) |
| Structure | `import_layers` (`.importlinter`, 15 contracts) **[E]** | gate | grounding |
| Structure | FreezingArchRule baseline / monotonic-decrease assertion for adopting stricter rules **[N]** | gate | https://www.archunit.org/userguide/html/000_Index.html |
| Structure | `unmatched_ignore_imports_alerting=error` / expiring-exemption tokens **[N]** (repo already has `expires=` for file_exemptions **[E]**) | gate | https://import-linter.readthedocs.io/en/v1.12.1/contract_types.html |
| Structure | dependency-cruiser `no-circular` / forbidden-required; Nx tag conformance; Bazel transitive-visibility **[N]** (JS/monorepo side) | gate | https://github.com/sverweij/dependency-cruiser · https://nx.dev/docs/features/enforce-module-boundaries |
| Structure/Topology | OPA/Rego governance-as-code over reverse-engineered architecture **[N]** | gate | https://medium.com/agoda-engineering/governance-as-code-an-innovative-approach-to-software-architecture-verification-d93f95443662 |
| Ontology | `doc_drift` (symbol resolution) + `doc_semantic_drift` (LLM) **[E]** — blind to concept-sense/stale counts | gate (two-tier) | grounding |
| Ontology | ArchUnit-style naming rule (concept name predicts module/location) **[N]** | gate | https://www.archunit.org/userguide/html/000_Index.html |
| Ontology | living-glossary extraction from identifiers + gate; concept→location index **[N]** | generated artifact | https://www.infoq.com/articles/book-review-living-documentation/ · https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/ |
| Ontology/L0 | reference-mirrors-structure gate (doc heading tree matches `src/factory/*` / `packages/*`) **[N]** | gate | https://diataxis.fr/reference/ |
| Behavior | `file_length` (≤300), `str_exc_bus_bound`, `hardcoded_constants` **[E, partial]** | gate | grounding |
| Behavior | invariants-as-CodeQL-queries / variant analysis **[N]** | gate | https://codeql.github.com/docs/codeql-overview/about-codeql/ |
| Behavior | AST/grep: dashboard routes carry `Depends(require_operator)`; memory queries pass `user_id` **[N]** (closes P0 IDOR/auth) | gate | grounding |
| Behavior | executable Gherkin wired to test runner (red test = drift alarm) **[N]** | gate | https://asdlc.io/patterns/the-spec/ |
| Delta | `axial-review.yml` label + per-gate `files:` filters + `doc_drift_bundle` fresh-deletion catch **[E]** — NO machine verdict | label + human review | grounding |
| Delta | Tier-2 diff→symbol→pytest impact check (`tested_by` edges, WARN/BLOCK on untested changed symbol) **[N]** | gate | https://github.blog/open-source/introducing-stack-graphs/ + grounding |
| Delta | same-commit path-pair gate (src change under path P requires paired doc/ADR update) **[N]** | gate | https://asdlc.io/patterns/the-spec/ |
| Delta | VCS temporal-coupling/hotspot report → tie gate strictness to hotspot status **[N]** | generated artifact | https://github.com/adamtornhill/code-maat |
| Strata | `check_doc_stratification.py`: domain page missing `## Invariants` → WARN; hardcoded counter outside L0 → FAIL; oversized `## Current state` → FAIL; "current truth" pointing at `artifacts/` → FAIL **[N]** | gate | grounding |
| Strata | section-contract gate: ADR-archive table shape (`ADR\|Title\|Status`) — the one mechanically-checkable slot **[N]**; scope to `docs/architecture/*.md` only, exempt AGENTS.md/runbooks, grandfather mid-migration pages (`security-routing.md`) via `expires=` file | gate | grounding |
| Procedure | none today **[E: gap]**; add operator-path CI smoke (`factory bot init`), then drop onboarding doc_drift exemption **[N]** | gate | grounding |
| All (DX) | Danger.js inline PR comments; severity tiering (error/warn/info) for staged rollout **[N]** | reporting/rollout | https://danger.systems/js/ |

---

## 6. Tensions & open questions for the designers

**T1 — Simplicity vs coverage vs enforceability is the master tension, and it is anti-correlated with value.** Grounding measures it directly: *rot is anti-correlated with gate coverage.* The best-gated axes (Topology, Structure) are the least rot-prone; the least-gatable axes (Ontology-sense, semantic Behavior, Delta, Procedure) are the most valuable and the most rotten. Designers must decide, per axis, whether to (a) accept "Manual fitness function with named owner", (b) invest in expensive LLM-semantic checks (too costly per-commit, gate-only), or (c) leave ungated and accept rot.

**T2 — The "3–6 owned gates" guidance vs the repo's ~40+ gates.** The fitness-function literature says few-always-blocking beats a wall; roxabi-factory already runs a large gate suite. Open: is this suite past the healthy threshold (fatigue/bypass risk), or are these genuinely owned and blocking? The two-tier deterministic+semantic split (P2/§5) and severity tiering are the reconciliation levers.

**T3 — Store-by-KOS-kind + index-by-intent, vs keep 6 axes as physical buckets.** The epistemics correction (§3.4) says the 6 axes are retrieval intents, not storage kinds, and using them as parallel physical hierarchies reproduces the N×M trap (P10). But the whole grounding *materializes by axis*. Designers must choose the primary physical axis (domain/feature pages) and demote the other five to generated/derived views — or knowingly accept N×M.

**T4 — Delta: derive-on-demand (consensus) vs the repo's zero machine verdict.** P7 says never store it; but building diff→symbol→test impact analysis is real work explicitly scoped OUT of spec 1532 (Tier-2). Is Delta worth the tooling cost, or does the `axial-review` label + human review remain the pragmatic ceiling?

**T5 — Ontology-Lite vs formal ontology.** Every ontology-KG source says full OWL/RDF never pays off below cross-org supply-chain scale; the upgrade trigger is precise and testable ("you are hand-caching a table of inferred/transitive-closure facts"). A single codebase almost never hits it. Open: how much typed-relation structure (SPDX-style lineage cluster for ADR-supersedes? narrow calls/imports/dataflow edge set?) before it's over-engineered?

**T6 — Homonym registry: verification cost vs shipping.** The source analysis fabricated the `satellite` homonym. A registry is warranted (§4) but MUST be re-verified row-by-row with grep-and-cite; a false homonym becomes a new stale SSoT. Also decide thin-pointer-table vs full-glossary (consensus: thin, per ADR-086).

**T7 — The "Current state" fourre-tout is semantic, not structural.** The real defect (an invariant like "SQL isolation rule (non-negotiable)" living under `## Current state`) cannot be caught by a heading presence/absence lint. Options: human-reviewed rule, a `**INVARIANT**` tagging convention (doesn't exist), or move all invariant-shaped subsections into `## Key invariants`. No purely-mechanical enforcement exists.

**T8 — Text-as-code confidence: mature lineage vs RAD-AI's dual-layer.** RAD-AI (arXiv 2603.28735) directly targets this exact problem (human-narrative L1 + schema-validated machine L0 + cross-layer consistency checks) but is a single 2026 preprint with no adoption. The C4/arc42/ADR/import-linter lineage is the higher-confidence backbone; treat RAD-AI as a direction for the L0↔L1 binding problem, not a proven pattern.

**T9 — Grep-first vs embeddings.** Consensus (P5) is grep/name-first, embeddings last (Cody *retired* them at enterprise scale). But ~28% of retrieval (implicit dependencies: inheritance, call chains) still needs semantic/graph retrieval. Designers must set where that line falls for this repo, and whether any embedding index is worth its build+maintenance+exfiltration cost.

**T10 — Section-contract cannot be repo-wide.** AGENTS.md is a deliberately different genre ("invariants, not inventory"); runbooks and `CURRENT.generated.md` have lighter doctrines. Any mandatory contract must scope to `docs/architecture/*.md` and carry a grandfather/exemption mechanism (mirroring `file_exemptions.txt`), or it red-lights every mid-migration page on day one.
