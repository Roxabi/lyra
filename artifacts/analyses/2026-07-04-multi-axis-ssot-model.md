# AI-compatible multi-axis codebase-SSoT model — design spec (v2, critique-hardened)

> **Status/home:** design spec, not living SSoT. Lives at `artifacts/analyses/2026-07-04-multi-axis-ssot-model.md` under the artifacts TTL. Durable output = the **ladder** (→ `docs/ARCHITECTURE.md`) + one **ADR** (the P0-behavior gate decision); the rest expires. This header pre-empts the meta-rot attack: the doc that prescribes homes states its own.
> **North star:** fewest orthogonal *enforceable* pieces; where two mechanisms overlap, cut one; a piece earns its slot only if a machine can catch its violation **or** a named human owns it — never prose pretending to enforce.

---

## 1. TL;DR (the model in one breath)

- One bounded-context corpus, decomposed by **altitude** (L0 generated/gated → L1 intent/invariants → L2 procedure → L3 residue) × **bounded context** (L1 only).
- The **6 axes are retrieval *intents* (query columns), never storage folders** — collapsing them into physical hierarchies *is* the N×M drift trap.
- **One entry point:** a reader-question **ladder** materialized into the existing `ARCHITECTURE.md` (zero new file); each question → one hop → one home.
- **Drift = a second home appearing for a question the ladder already answers.** Gate it where a machine can decide; assign a named human where it can't — stated, not faked.
- **Net new machine = 1 BLOCK gate** (closes live IDOR/auth holes) + a few **WARN checks folded into existing oracles** (`doc_drift`, `debt_expiry`). No parallel gate stack.
- **Ships in lots; the model exists after Lot 1** (ladder only, no tooling). Everything past that degrades gracefully.

---

## 2. The problem, one level up (generalized)

A codebase's "truth" is interrogated along several **independent intents**, each a *different epistemic kind* with a *different natural freshness*:

| Intent | Question | Epistemic kind | Natural freshness |
|---|---|---|---|
| Glossary | what is X / which sense? | controlled vocabulary | slow |
| Topology | runs-where / talks-to? | mereology + graph | generated-fresh |
| Structure | who may/does import whom? | poset + deontic rule | generated + slow |
| Behavior | what must stay true? | axioms | law (durable) |
| Delta | what does my change endanger? | derived query | on-demand |
| Procedure | how do I do X now? | document genre | per-change |

**Why current mixing fails (two universal defects):**

1. **The catch-all slot.** One heading (`## Current state`, `Overview`, `Notes`) stacks a generated inventory + a durable invariant + an impl note + a history fragment at one altitude. Now the slot has **no single decidable property** → ungatable; and every consumer re-derives the same facts → **N docs drift against M code facts independently** (the **N×M trap**).
2. **Intent-as-folder.** Building the 6 intents as 6 physical hierarchies yields two co-equal trees (by-layer `src/` **and** by-axis docs) each forced to enumerate the other → each drifts alone.

**The generalized cure (orientation, not more docs):** pick **one** physical decomposition; project every other intent as an **index or a query**; pin each fact to the **altitude whose freshness discipline a machine can enforce**. Then "reduce drift" becomes a gate property, not a diligence hope.

---

## 3. The model — axis × stratum grid (ONE home · form · enforcement per cell)

> **Defend (Lens-1 attack 4): this grid is a design-time orientation sketch, NOT a committed living doc.** The SSoT for exact gate names is `stack.yml`; the SSoT for topology/structure facts is `CURRENT.generated.md`. The grid binds *intent → altitude → home-kind*, which is stable; it deliberately does **not** hand-maintain the 48 gate strings (that would rebuild the N×M trap it warns against). Regenerate from `stack.yml` if a durable mapping is ever wanted. `—` = **no home by design** (query, discovery, or covered by a neighbour). `[E]` today · `[N]` new · `[N-def]` deferred.

| Stratum ↓ / Axis → | **Glossary** | **Topology** | **Structure** | **Behavior** | **Delta** | **Procedure** |
|---|---|---|---|---|---|---|
| **L0** generated/gated (extensional facts) | `grep` name-as-key + `doc_drift` anchors `[E·gate]` | `CURRENT.generated` context map + typed edges `[E·gate-fam]` | import **graph** = *does-import* fact `[E·snapshot]` | the gate **script** = the enforced invariant `[E·BLOCK]` | — *never stored (P7)* | generated `--help`/cmd tables `[N-def·gen]` |
| **L1** intent/invariants (intensional) | owning page + inline "distinguish-from" note `[E→N·WARN]` | page `## Scope` names the context `[E·—]` | `.importlinter` = *may-import* **rule** (15 contracts) `[E·import_layers]` | `## Key invariants`, tagged `[gate:x]`/`[law]`/`[owner:@x expires=]` `[E→N·WARN]` | — | rationale → links out to L2 `[—]` |
| **L2** procedure (exact commands) | — | `runbooks/` deploy topology `[E·smoke]` | — | — | same-PR **fan-out grep** + axial-review label `[N·WARN + E·label]` | `docs/runbooks/` self-contained, smoke-tested `[E→N·smoke]` |
| **L3** residue/history (provenance) | ADR archive: term lineage (e.g. `turn` deprecation) `[E]` | ADR archive: why this topology `[E]` | ADR archive `[E]` | ADR archive `[E]` | changelog + ADR-supersede graph `[E]` | ADR archive `[E]` |

**The load-bearing collapses (why most cells are empty by design):**
- **Glossary ≡ Behavior at two altitudes.** A concept lives at L1 (owning page); its *axioms* live at L1 `## Key invariants` and are *enforced* at L0 (the gate). Same spine — every invariant **names** the term it constrains → no term is axiom-less, no invariant floats term-less.
- **Structure = one word, two epistemic kinds.** *does-import* (L0 fact, generated) vs *may-import* (L1 rule, `.importlinter`). Two homes because two questions.
- **Topology = two edge algebras.** containment (`process ⊑ container ⊑ host ⊑ cluster`, **transitive**) vs `talks-to` (directed, **intransitive**). Split in the generated model so gates/agents never chain `talks-to` through `contains-in` (the "musician's hand is part of the orchestra" fallacy).
- **Delta has no cell in any stratum.** It is a *query* over Topology(`talks-to`)+Structure(imports) ∩ Behavior(invariants). You cannot rot a file that does not exist.

---

## 4. The 6 axes, each with its correct knowledge-organization type

Per the epistemics correction: `ontology = taxonomy + axioms`. Most of these axes are *below* ontology or are not classifications at all.

| Axis (as named) | Correct KO type | Answers | Canonical home | Why not its name |
|---|---|---|---|---|
| **Glossary** *(ex-"Ontology")* | **controlled vocabulary / thesaurus** (terminological control — preferred term + "distinguish from sibling") | what is X / which sense | owning page + `grep` name-as-key | it has no axioms of its own; the axioms are the Behavior axis |
| **Topology** | **mereology** (containment, transitive) **+ directed graph** (`talks-to`, intransitive) | runs-where / talks-to | `CURRENT.generated` context map | two relation types with different algebra hide in one word |
| **Structure** | **poset** (layer order) **+ deontic layer** (MAY) | who imports / may import whom | import graph (L0) + `.importlinter` (L1) | the "MAY" is intensional law; the graph is extensional fact — two strata |
| **Behavior** | **axioms** (the layer that upgrades the glossary's taxonomy into an ontology) | what must always stay true | `## Key invariants` + the enforcing gate | not orthogonal to Glossary — it *is* its axiom layer |
| **Delta** | **derived view** (not a dimension) | what does my change endanger | none — a query/tool | fails the orthogonality test: computable from Topology+Structure ∩ Behavior |
| **Procedure** | **document genre** keyed to reader-intent (DITA task) | how do I do X right now | `docs/runbooks/` (no non-local preconditions) | it is a genre, not a classification of the code |

**Meta-correction (Baker's shapes-vs-types error):** these mix relation-graphs, axioms, a derived view, a vocabulary, and a genre. They are **excellent as retrieval intents, wrong as storage buckets.** The real grid is **(intent × altitude)**, not (axis × stratum) as peer classifications. Adopt the *weakest sufficient KO structure per altitude* — do not over-model L0 or L3 as an ontology.

---

## 5. The section contract — slot menu + placement (optional-by-need)

> **Fix (Lens-1 attacks 1, 6, 9):** the `## Current state` **ban is dropped** — grep proves **8 of 16** pages use it healthily, including the model's own cited exemplar `engineering-standards.md:22`. The defect is **mis-altituded content**, not a heading string. Slots are **optional-by-need**, not six mandatory slots (six mandatory = a renamed fourre-tout + empty-slot ceremony). We **ratify the existing `## Key invariants`** heading (8 pages use it); we do **not** invent `## Invariants` or a `## Concepts` slot (grep: exists nowhere). Gate **ordering + the one decidable slot shape**, never presence-of-all.

**Slot menu (drawn from a fixed vocabulary, each pinned to one altitude, present only when the page needs it):**

```
## Scope          (L1)  REQUIRED, 1 line = the bounded context this page is sole home for.
                        ← this line IS the retrieval key the ladder points at.
## Current state  (L0-ptr + L1)  NOT banned. Foreign-altitude facts are POINTERS
                        (→ CURRENT.generated §X · → quadlet.toml · → grep <sym>),
                        never inventories/counters copied by hand.
## Key invariants (L1)  present iff the page owns invariants. one row each:
                        statement | constrains <term> | ONE of  [gate:<name>] · [law] · [owner:@x expires=YYYY-MM-DD]
## See also       (L1)  cross-links; homonyms carry "distinguish from <sibling> → <page>" (both directions).
## Procedures     (L2)  POINTER ONLY → runbooks/<x>. no inline exact shell.
## ADR archive    (L3)  EXACT shape: ADR | Title | Status. the one mechanically-checked slot.
```

**Decidable → gate. Not decidable → human (named).**

| Rule | Verdict | Why |
|---|---|---|
| `## ADR archive` present → columns match `ADR \| Title \| Status` | **FAIL** | shape is mechanical; title = the contract |
| "current-truth" pointer into `artifacts/` | **FAIL** | 3 active ADRs violate this today; artifacts are L3-residue |
| slot appears out of altitude order | **WARN** | ordering is mechanical |
| exact shell block outside `## Procedures`/runbooks | **WARN** | shell-detection is mechanical |
| ~~counter outside L0 → FAIL~~ | **DELETED** | not machine-decidable — no lint separates a rotting `13/16 keep` from a stable `port 18091` / `ADR-073` / `≤350 lines` (Lens-1 attack 6). Human/`doc_drift` concern. |

**Before → after (the fourre-tout, re-pinned):**

```
BEFORE  (one slot, four altitudes, ungatable)
## Current state
The system has 7 adapters (telegram, discord, web, clipool, omp, turn-writer, ingress).
Adapters MUST send trust=PUBLIC; the hub re-authenticates every inbound.   ← invariant buried
Deploy with: podman generate systemd ... && systemctl --user start factory-hub  ← L2 shell
We moved off per-turn job-ids in #1794 because steering broke.               ← L3 history
```
```
AFTER  (each fact at its altitude; page now gatable)
## Scope
Inbound platform adapters and the hub's re-auth boundary.
## Current state
Adapter set → CURRENT.generated §topology · quadlet.toml. (no hand-typed count)
## Key invariants
Adapters send trust=PUBLIC; hub re-authenticates every inbound | constrains adapter | [law]
## Procedures
Deploy → runbooks/install.md
## ADR archive
ADR-084 | Job-id is run-granular, not turn-granular | Superseded-by #1794
```

**Honest limit (T7):** a semantically mis-filed invariant (e.g. an isolation rule written as prose under `## Current state`) escapes heading lint. Mitigation: the invariant register scans **only** `## Key invariants`, so a hidden invariant is *invisible*, not *falsely-passed*. Residual human review, stated.

**Scope (T10):** this contract applies to `docs/architecture/*.md` **only**. `AGENTS.md`/`CLAUDE.md` are a deliberately different genre ("invariants, not inventory") — see §9 for their explicit placement. Runbooks / `CURRENT.generated` have lighter doctrines.

---

## 6. Concept registry — the homonym killer (no separate file)

> **Fix (Lens-1 attack 3, reconciles the ratified `¬index séparé`/`titre = contrat` doctrine):** **no `docs/architecture/concepts.md`.** A separate router *is* the "second home for term routing" the ADR-consolidation doctrine forbids, and it rots (its oracle proves an anchor exists, not that a sense is right). Instead:
> 1. **Inline** disambiguation lives in the owning page next to the term's definition + a bidirectional `## See also` "distinguish from" link.
> 2. **Discoverability** = `grep` name-as-key (slug = verbatim code identifier) + a **prospective homonym WARN gate** — the sole-home property enforced *forward*, not just declared rows validated.

**Name-as-key (P5, GrepRAG, "titre = contrat"):** a concept's canonical slug MUST appear verbatim as a code identifier → `name → code` resolves by `grep`, zero embedding infra. Legacy misnomers carry an explicit **known-naming-debt** note rather than a forced rename (e.g. `WorkerRegistry` = actually a heartbeat/provider registry, `src/factory/nats/worker_registry.py`).

**Seed = ONLY the 4 grep-verified homonyms** (the source analysis *fabricated* `satellite` — a false row is a new stale SSoT → **dropped**; grep confirms `satellite` is single-sense). These are notes to **write into the owning pages**, not a central table:

| term | senses → canonical page | disambiguation rule (inline note) |
|---|---|---|
| `plane` | 3 transport planes (`messaging.md`, ADR-076) · 4 observability signal planes ①–④ (`observability.md`, ADR-091) · "control-plane" = dashboard BFF | numbered ①–④ = observability; unnumbered = transport wire; "control-plane" = auth surface |
| `event` | wire subject `factory.event.*` (`infrastructure/events`) · in-proc `RenderEvent` (`core/messaging/render_events.py`, ADR-100) | Capitalized `RenderEvent` = internal; subject literal = external |
| `axial` | decomposition axis (ADR-073) · `axial-adr-review` layer-boundary CI label (`AGENTS.md §Axial Review`) | "axis of decomposition" = ADR-073; the GH label = layer-crossing gate |
| `worker`/`turn` | compute consumer (`workers-tooling.md`) vs `WorkerRegistry` heartbeat class · conversation unit (`job-model.md`) vs deprecated turn-granular job-id (ADR-084, superseded #1794) | `WorkerRegistry` = known-naming-debt, not a 2nd valid concept; any pre-#1794 "turn" is stale |

**Prospective homonym gate (Lens-2 attack 5):** extract the defined-term set across all `docs/architecture/*.md` (the `## Scope`/definition headers); a term "owned" by ≥2 pages **without** a bidirectional "distinguish from" cross-link → **WARN**. This catches a *new* homonym at creation, not only after a human files it. **Folds into `doc_drift`** (already the anchor-resolution engine) — see §8 for the honest count consequence.

**Deferred (T5):** the generated `concept→location index` (Glean-style, closes Biggerstaff's concept-assignment gap fully). Inline notes + grep name-as-key are sufficient for v1; build the index only if hand-caching pain appears.

---

## 7. The retrieval ladder — the single entry index (into `ARCHITECTURE.md`)

> **Fix (Lens-1 simplification):** materialized into the **existing** `docs/ARCHITECTURE.md` routing hub — **no new `RETRIEVAL.md`**. ADR-086 §47-54 *specified* this ladder and never shipped it; this lands it. An agent has an *intent* even without the vocabulary → the ladder keys on the question. Adds **row 0** (global-vs-local routing, Lens-2 attack 3) and a **write-path** note (Lens-2 attack 8).

| # | Reader question | One hop → | Axis · stratum |
|---|---|---|---|
| **0** | Is this rule **repo-specific or org-wide**? | org-wide → `~/projects/ssot/*.ssot.md`; repo-local → `docs/architecture/*.md`. A local page instantiating a global rule **names the governing shard** (grep-findable both ways). | routing |
| 1 | What is X / which sense of the word? | `grep <name>` → owning page; homonym → follow `## See also` "distinguish from" | Glossary · L1 |
| 2 | What runs where / who talks to whom? | `CURRENT.generated.md` (typed context map) | Topology · L0 |
| 3 | Who **MAY** import whom? / who **DOES**? | `.importlinter` (rule) · `CURRENT.generated` import graph (fact) | Structure · L1/L0 |
| 4 | What must always stay true here? | page `## Key invariants` → follow `[gate:<name>]` to the enforcing gate | Behavior · L1→L0 |
| 5 | Where is the code for Y? | `ccc` (semantic) / `grep` (exact) — **discovery, no normative home** | — |
| 6 | Why is it built this way? | page `## ADR archive` | Provenance · L3 |
| 7 | What does my change endanger? | **run** the same-PR fan-out check (WARN) + axial-review label; symptom→fix via `docs/runbooks/README.md` index | Delta · derived |
| 8 | How do I do X right now? | `docs/runbooks/<x>` (self-contained, smoke-tested); index at `runbooks/README.md` | Procedure · L2 |

**Write-path (authoring ladder) — where new knowledge goes:**

| Authoring intent | Home | New-page rule |
|---|---|---|
| new concept | owning page, slug = a real code identifier | — |
| new invariant | that page's `## Key invariants`, tagged, naming its term | — |
| new decision | ADR archive row (`titre = contrat`) + redirect banner → owning page | — |
| new **bounded context** | a **new** `docs/architecture/<x>.md` **iff** no existing `## Scope` line covers it (1-sentence collision check against every Scope line); else extend the covering page | `axial-adr-review` label is the forcing function on layer-crossing PRs |

**Honest:** two questions resolve to a **tool/query, not a document** (5 discovery, 7 Delta). The ladder says so rather than pretending a stored artifact is truth.

---

## 8. Enforcement menu — machine-checkable vs deliberately not

> **Fix (Lens-1 attacks 5, 7):** strict budget. **Exactly ONE new BLOCK gate** (the P0-behavior AST gate — it earns its slot by closing a *live security hole*, not doc hygiene). Everything else ships **WARN** (no CI-red failure mode, no pre-commit latency if CI/advisory-only) or **folds into an existing oracle**. A new BLOCK gate must close a real defect or retire an older gate. The ~48 existing gates are **reframed** as the axes' L0/L1 enforcement — none retired, but **no new parallel BLOCK stack**.

| Axis | Mechanism | Status | Verdict | Note |
|---|---|---|---|---|
| **Topology** | `architecture_snapshot`, `quadlet_manifest_install`, `volumes_table`, `acl_grants`, `acl_specs_drift`, `acl_authconf_drift`, `acl_matrix_retired`, `request_reply_flows`, `inbox_prefix`, `subject_literals`, `secrets_drift`, `quadlet_component_source` | `[E]` | **BLOCK** | healthiest axis; keep blocking (no `continue-on-error`) |
| Topology | typed-edge transitivity split (containment vs `talks-to`) in the generated model | `[N]` | gen | small; kills intransitive-chaining in ACL reasoning |
| **Structure** | `import_layers` (`.importlinter`, 15 contracts) — reads laundered/indirect imports grep misses | `[E]` | **BLOCK** | |
| Structure | `unmatched_ignore_imports_alerting=error` + `expires=` on every waiver; FreezingArchRule baseline (monotonic decrease), run ON the change branch | `[N]` | **BLOCK** | reuses the repo's `expires=` idiom; retires nothing but hardens an existing gate |
| **Glossary** | `doc_drift` (anchor resolution) extended to the **prospective-homonym** check + name-as-key identifier existence | `[E→N]` | WARN | oracle catches dead links/dup-ownership, **not** wrong senses (honest limit) |
| **Behavior** | `file_length`, `str_exc_bus_bound`, `hardcoded_constants` | `[E]` | **BLOCK** | machine-decidable invariants already gated |
| Behavior | **P0 AST gate (one script):** dashboard routes carry `Depends(require_operator)`; memory queries pass `user_id` | `[N]` | **BLOCK** | **the one new BLOCK** — closes the 2 measured gate-blind IDOR/auth P0s |
| Behavior | invariant-tag **register** (advisory): a `## Key invariants` row *should* carry `[gate:x]` · `[law]` · `[owner:@x expires=]` | `[N]` | **WARN** | see reconciliation below; never a hard gate |
| **Delta** | `axial-review.yml` label + per-gate `files:` filters + `doc_drift_bundle` fresh-deletion catch | `[E]` | label + human | interim ceiling for layer-crossing |
| Delta | **same-PR fan-out grep** (Delta MVP): changed ACL key / host role / quadlet unit name → grep a small sibling-consumer manifest → WARN if a known consumer wasn't touched | `[N]` | **WARN** | targets the repo's **#1 recorded failure class** (7 MEMORY fan-out incidents) — cheap, ships early |
| Delta | full `factory-verify-change` (diff→symbol→`tested_by`) + same-commit path-pair gate | `[N-def]` | WARN | out of #1532 scope; highest cost, last |
| **Procedure** | operator-path CI smoke (`factory bot init` + `bot secret install` + boot) | `[N]` | gate | **no exemption to drop** — the onboarding `doc_drift` exemption was already removed by #2201 |
| **Strata** | ADR-archive-shape + artifacts-pointer FAIL; ordering/shell WARN (see §5) | `[N]` | mixed | scoped `docs/architecture/*.md`; `expires=` grandfather for mid-migration pages |
| **Cross-cut** | `artifacts/analyses/*.md` TTL | `[N]` | WARN | **folded into `debt_expiry`**, not a new script |

**Deliberately NOT machine-checkable (human-owned, named not papered):**
- concept **sense** correctness (oracle proves an anchor exists, not that the disambiguation is right).
- semantically **mis-filed** invariant (prose under the wrong slot).
- **counter staleness** (rotting `13/16` vs stable `port 18091`).
- Delta **beyond** the fan-out heuristic (full transitive test-impact = deferred).

**`expires=` reconciliation (Lens-2 missing):** one token, one owner — **`debt_expiry`**. A time-boxed invariant `[owner:@x expires=]` *is literally a `debt_expiry` row*. Permanent laws use **`[law]`** and are **never** expiry-scanned (this is the fix to Lens-1 attack 5: forcing `expires=` on `core never imports adapter` would delete a permanent truth on date-passage and chill authors into writing fewer invariants — a gate that shrinks the invariant corpus is anti-drift-resistance).

**Honest new-machine count (Lens-2 attack 7):** **1 BLOCK** (P0 AST) · **4 WARN/fold** (fan-out, operator-smoke, prospective-homonym→`doc_drift`, artifacts-TTL→`debt_expiry`) · **1 advisory** (invariant-tag register). The prospective-homonym check **widens `doc_drift`'s scope** (stated, not hidden) rather than adding a `check_concepts_router.py` — that is the true accounting; if kept separate it is a 4th micro-gate.

---

## 9. The one foundational decision — the axis of decomposition

> **Decision:** decompose the SSoT along **altitude** (vertical: L0→L3) × **bounded context** (horizontal, L1). Every other view — the 6 axes — is a **projected index, generated artifact, or derived query**, never a parallel physical hierarchy.

This is upstream of everything. Get it wrong and you rebuild the **N×M trap**: two co-equal hierarchies (by-layer `src/` **and** by-axis docs) each enumerating and drifting against the other — the audit's measured state (mis-stated counters `9/7/10/16` vs real `19/13/13/19/36`).

**Why bounded-context for L1 (not by-axis, not by-`src/`-layer):**
- The load-bearing structural asymmetry is hexagonal **inside/outside** (core never imports adapter — Cockburn), already the repo's `axial-review.yml` trigger. Docs decompose by the *domain* those layers serve → a page = one context-map projection.
- The healthy zone the audit found (`docs/architecture/` 13/16 keep, ≤350 lines, AGENTS.md network ~95% exact) **already is** bounded-context pages + sole-home AGENTS.md. **Ratify the winner; don't invent one.**

**Two-tier, so the trap can't recur one level up (Lens-2 attack 3):**
- **org-wide** invariants → `~/projects/ssot/*.ssot.md` (e.g. the ADR-consolidation doctrine this spec obeys).
- **repo-local** → `docs/architecture/*.md`.
- Arbitration = **ladder row 0** + the convention that a local page instantiating a global rule **names the governing shard** (grep-findable both directions). Two L1 homes *with a routing rule* is not the N×M trap; two *without* one is.

**`AGENTS.md`/`CLAUDE.md` = an explicit 5th genre class (Lens-2 attack 2), not silently outside every gate:**
- It holds real Behavior invariants (mandatory Axial Review; the `except Exception` labeling rule).
- Placement: its own **`## Invariants`-equivalent block**, scanned by the *advisory* register with the **same tag vocabulary** (`[gate]`/`[law]`/`[owner…]`), lighter enforcement (WARN). It is named in the grid as a genre, not exempted into invisibility.

**Disambiguating the repo's own `axial` homonym:** "axis of decomposition" here = *this* meta-choice (altitude × context). Unrelated to ADR-073's stage-vs-platform axis and unrelated to the `axial-adr-review` CI label (a layer-crossing gate). Both are homonym rows (§6).

**Consequence:** each axis collapses to exactly one of {node-type, edge-type, axiom-on-a-node, genre-on-a-node, derived query} → the 6×4 grid is **mostly empty by design**. A cell gets a home only when that intent needs a durable one.

---

## 10. What this SIMPLIFIES vs today + phased adoption

**Simplification ledger (overlaps cut / defects removed):**

| Overlap / defect found | Cut / merge |
|---|---|
| `RETRIEVAL.md` new file vs the ladder | **fold ladder into `ARCHITECTURE.md`** — zero new file |
| `concepts.md` separate index (violates `¬index séparé`) | **CUT** — inline "distinguish from" notes + grep + prospective WARN |
| `## Current state` heading ban | **DROP** — grep-refuted (8/16 incl. exemplar use it healthily); gate content-placement, not a heading name |
| invent `## Invariants` | **RATIFY** existing `## Key invariants` (8 pages) |
| counter-outside-L0 → FAIL | **DELETE** — not machine-decidable |
| `check_invariant_register.py` as hard gate + concept-binding + forced `expires=` | **→ advisory WARN + `[law]` marker + reuse `debt_expiry`** (one token, one owner) |
| 6 mandatory slots | **optional-by-need**; gate ordering + ADR-archive shape only |
| section-contract gate + stratification gate | **one** advisory check; FAIL only on ADR-archive shape + artifacts-pointer |
| Delta stored blast-radius doc / temporal-coupling report | **CUT** — Delta is a derived query; no stored artifact |
| Delta heavy `factory-verify-change` as day-one | **replace** with cheap same-PR fan-out-grep WARN (targets 7 recorded incidents); heavy tool deferred |
| Danger.js severity tooling | **CUT** — reuse `expires=` + WARN→FAIL burndown |
| `satellite` homonym row | **DROP** — grep-refuted |
| 6 axes as 6 physical folders | **CUT** — intents, one physical hierarchy |
| Lot 5 "drop onboarding `doc_drift` exemption" | **DELETE step** — already removed by #2201; keep the smoke only |
| artifacts one-time sweep | **fold into `debt_expiry`** recurring scan |

**Result: 1 new BLOCK gate + 4 WARN/folds + 1 advisory + 1 doc (ladder) + 1 smoke** — all reusing existing `doc_drift`/`debt_expiry`/`expires=` infra. Inside the repo's tolerance, not a parallel stack.

**Adoption path (independent, shippable lots; degrades gracefully):**

- **Lot 0 — TRUTH FIRST, *per-page* (dissolves the "never completes" objection):** fix a page's confirmed drifts **before** restructuring *that page* — not a monolithic global blocker. Incident runbooks first (`nkey-rotation`, `nats-identity-lifecycle` step 7, `nats-acl-postmortem-remaining`). Owner = operator; interleaved with Lot 4 per page. Restructuring on false docs only relocates the lies.
- **Lot 1 — materialize the ladder** (rows 0–8) into `ARCHITECTURE.md`. **No tooling. The model *exists* after this lot — you may stop here** and still have a coherent SSoT.
- **Lot 2 — Delta MVP:** the same-PR **fan-out-grep WARN** gate. Targets the repo's #1 recorded failure class. Cheap; no new schema.
- **Lot 3 — the one BLOCK gate:** the 2 P0 AST checks. Closes the measured IDOR/auth P0s — the model's proof it shuts a *real* hole.
- **Lot 4 — homonyms + slots:** write the 4 inline disambiguation notes; ratify `## Key invariants`/`## Current state`; ship prospective-homonym WARN (into `doc_drift`) + ADR-archive-shape check. Convert pages **one at a time, invariant-by-invariant** (13 "obvious" ADR merges were REFUTED in wave 3); **redirect stubs, never dry moves** (9 open issues + MEMORY + ssot shards hard-link doc paths → each moved path gets a stub; enumerate the 9 issues' path refs in the PR body).
- **Lot 5 — Procedure smoke:** operator-path CI smoke. (No exemption to drop.)
- **Lot 6 — optional/last:** full `factory-verify-change` + invariant-tag register at WARN. Deferring leaves a smaller-but-coherent model; the ladder already routes Q7 honestly meanwhile.
- **Cross-cutting — artifacts TTL (into `debt_expiry`):** any `artifacts/analyses/*.md` past N waves, neither `## ADR archive`-referenced nor frontmatter `graduated:`, → WARN. Delete `archive/2026-06` (−44% of repo markdown); graduate the 3 ADR-designated "current truth" artifacts up to L1 so ADR-086 "deltas only" becomes true again. **This spec is subject to the same TTL.**

**Falsifiable success metric (Lens-1 missing):** re-run the audit's drift counters after Lot 5. Target: the **20 confirmed high-drifts closed and held** (0 recurrence at PR time on gated axes); each subsequent audit re-measures the *same* counters (was mis-stated `9/7/10/16` vs real `19/13/13/19/36`). The gate moved the number or it didn't — the audit is the oracle. Delta success = the 7 MEMORY fan-out patterns each now WARN at PR time.

**MEMORY.md graduation (Lens-2 attack 4):** session-memory entries are **proto-L1/L3** with a mandatory TTL-driven triage on the `debt_expiry` cadence — each entry either **graduates** (to a domain-page `## Key invariants` row or an ADR archive row) or **expires**. Un-triaged is a 4th un-unified corpus; the cadence prevents it.

---

## 11. Generalization — instantiate in ANY repo (checklist)

1. **Pick ONE physical decomposition axis** (bounded context, usually) by the load-bearing structural asymmetry (hexagonal inside/outside). Everything else = projected index.
2. **Name the 4 altitudes** (L0 generated/gated · L1 intent/invariants · L2 procedure · L3 residue). Assign every fact to the altitude whose freshness a **machine can enforce**.
3. **One entry index** = a reader-question **ladder** in the top architecture doc. Add **row 0** (org-wide vs repo-local) if a shared doctrine layer exists.
4. **Per page:** `## Scope` (1 line = the retrieval key) + optional-by-need `## Current state` (pointers, not hand-copied inventories) / `## Key invariants` / `## See also` / `## ADR archive` (fixed shape).
5. **Glossary = grep name-as-key:** every concept slug appears verbatim as a code identifier; homonyms get an **inline** "distinguish from" note + bidirectional link. **No central glossary file.**
6. **Invariants** each name the term they constrain + one tag: `[gate:x]` (enforced) · `[law]` (permanent, un-expiring) · `[owner:@x expires=]` (= a debt row, one token/one owner).
7. **Enforcement budget:** 3–6 always-BLOCKING gates, each closing a real defect or encoding a decidable invariant; everything softer ships **WARN** and burns down via expiring exemptions. A new BLOCK gate must **close a defect or retire an older gate**.
8. **Delta = derived query, never a stored doc.** Minimum viable = a same-PR **fan-out grep** over a small sibling-consumer manifest (config keys, unit names, cross-repo refs). Heavy test-impact = optional/last.
9. **Generate, never hand-maintain,** any inventory/graph/count — **and gate the generated artifact** (ungated generated = *faster* rot).
10. **What a machine cannot decide** (concept sense, mis-filed invariant, counter staleness) → **named human owner**. Never let prose pretend to enforce.
11. **Session-memory / scratch analyses** get an explicit **TTL + graduation path** into L1/L3 on the same expiry cadence — or they become a 4th un-unified corpus.
12. **The design doc itself gets a home + TTL:** durable decisions graduate to an **ADR + the ladder**; the analysis expires. (Answers "where does the model live" — including this one.)

---

## 12. Sources (deduped, grouped by theme)

**Fitness functions / architecture governance (enforcement):**
- https://www.archunit.org/userguide/html/000_Index.html
- https://aipatternbook.com/architecture-fitness-function
- https://continuous-architecture.org/practices/fitness-functions/
- https://import-linter.readthedocs.io/en/v1.12.1/contract_types.html
- https://github.com/sverweij/dependency-cruiser
- https://nx.dev/docs/features/enforce-module-boundaries
- https://bazel.build/concepts/visibility
- https://codeql.github.com/docs/codeql-overview/about-codeql/
- https://medium.com/agoda-engineering/governance-as-code-an-innovative-approach-to-software-architecture-verification-d93f95443662
- https://danger.systems/js/

**Code-as-data / indexing / retrieval (generate-don't-maintain, name-as-key):**
- https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/
- https://kythe.io/docs/kythe-overview.html
- https://github.blog/open-source/introducing-stack-graphs/
- https://arxiv.org/html/2601.23254v1 (GrepRAG)
- https://www.cs.kent.edu/~jmaletic/Prog-Comp/Papers/biggerstaff93.pdf (concept-assignment gap)

**Architecture modeling / topology (one model, many views):**
- https://docs.structurizr.com/as-code
- https://c4model.com/
- https://contextmapper.org/docs/context-map/
- https://martinfowler.com/bliki/BoundedContext.html
- https://www.oreilly.com/library/view/domain-driven-design-distilled/9780134434964/ch02.html
- https://alistair.cockburn.us/hexagonal-architecture

**Knowledge organization / epistemics (KO types, faceting, mereology, thesaurus):**
- https://taxodiary.com/2026/01/taxonomy-thesaurus-or-ontology-clearing-the-confusion-part-two/
- https://berkeley.pressbooks.pub/tdo4p/chapter/faceted-classification/
- https://en.wikipedia.org/wiki/Mereology
- https://eng.libretexts.org/Bookshelves/Computer_Science/Programming_and_Computation_Fundamentals/An_Introduction_to_Ontology_Engineering_(Keet)/07:_Top-Down_Ontology_Development/7.03:_Part-Whole_Relations
- https://everypageispageone.com/2012/07/28/the-tyranny-of-the-terrible-troika-rethinking-concept-task-and-reference/

**Documentation genres / living docs / progressive disclosure:**
- https://diataxis.fr/
- https://diataxis.fr/reference/
- https://www.infoq.com/articles/book-review-living-documentation/
- https://asdlc.io/patterns/the-spec/
- https://dl.acm.org/doi/10.1145/3643916.3644399 (Parnas software-aging)
- arXiv:2603.28735 (RAD-AI dual-layer L0↔L1 binding — single 2026 preprint, direction not proven pattern)

**Agent context engineering / program comprehension:**
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- https://www.cs.kent.edu/~jmaletic/cs63902/Papers/ProgramComprehension/von_mayrhauser95.pdf

**Behavioral / delta analysis:**
- https://codescene.com/product/behavioral-code-analysis
- https://github.com/adamtornhill/code-maat
- https://github.com/npryce/adr-tools

---

## Appendix — critique disposition (audit trail)

`FIX` = attack accepted, model changed · `DEFEND` = attack answered, position held with mitigation. Empirical `FIX`es were grep-verified against HEAD this session.

| # | Attack | Disposition | Where |
|---|---|---|---|
| L1-1 | `## Current state` ban + false exemplar | **FIX** — ban dropped (verified 8/16 incl. `engineering-standards.md:22`) | §5, §10 |
| L1-2 | Lot 5 stale exemption (#2201) | **FIX** — step deleted (verified removed at `check_doc_drift.py:85-125`); smoke kept | §8, §10 |
| L1-3 | `concepts.md` violates `¬index séparé` | **FIX** — file cut; inline notes + prospective WARN | §6 |
| L1-4 | 6×4 grid = N×M trap | **DEFEND** — required by deliverable; labeled design-time, non-committed; `stack.yml` = SSoT for gate names | §3 header |
| L1-5 | invariant-register unenforceable / `expires=` misuse | **FIX** — advisory WARN + `[law]` + reuse `debt_expiry`; concept-binding dropped | §8, §10 |
| L1-6 | counter-outside-L0 → FAIL not decidable | **FIX** — rule deleted | §5 |
| L1-7 | "almost no machine" vs 3 gates | **FIX** — budget: 1 BLOCK + 4 WARN/fold + 1 advisory; honest count | §8 |
| L1-8 | Delta = human-review until optional Lot 6 | **FIX** — cheap fan-out WARN promoted to Lot 2; heavy tool labeled optional | §7, §8, §10 |
| L1-9 | 6 mandatory slots = renamed fourre-tout | **FIX** — optional-by-need; gate ordering + shape only | §5 |
| L1-10 | doc itself has no home / meta-rot | **FIX** — home + TTL stated; durable → ADR + ladder | header, §10, §11.12 |
| L1-miss | Lot 0 no owner/date | **FIX** — per-page, interleaved, operator-owned | §10 |
| L1-miss | no falsifiable metric | **FIX** — re-run audit counters; 20 drifts held closed | §10 |
| L1-miss | ADR-consolidation reconciliation | **FIX** — `concepts.md` cut honors `¬index séparé`/`titre = contrat` | §6, §9 |
| L2-1 | Delta lowest-invest vs highest incidence | **FIX** — fan-out MVP targets 7 MEMORY incidents | §8, §10 |
| L2-2 | AGENTS.md invariants unplaced | **FIX** — explicit 5th genre class, lighter register | §9 |
| L2-3 | global ssot layer unrouted | **FIX** — ladder row 0 + governing-shard convention | §7, §9 |
| L2-4 | MEMORY.md unplaced | **FIX** — proto-L1/L3 + `debt_expiry` graduation cadence | §10 |
| L2-5 | homonym-reintroduced not gate-caught | **FIX** — prospective homonym WARN | §6, §8 |
| L2-6 | artifacts TTL one-time not recurring | **FIX** — folded into `debt_expiry` | §8, §10 |
| L2-7 | fold-into-`doc_drift` blast-radius hidden | **FIX** — stated explicitly; true count surfaced | §8 |
| L2-8 | no write-path / page-granularity | **FIX** — authoring ladder + new-page collision rule | §7, §11 |

Every real attack is fixed or defended; every simplification from both lenses is applied.
