# Brand Exploration Playbook

**Version:** 1.0 (2026-03-18)
**Source:** Lyra brand exploration session — reconstructed as a reusable process.
**Applies to:** Any product with an existing codebase and at least a rough vision statement.

---

## 1. Overview

This playbook describes a structured, agent-orchestrated brand exploration process that takes a product from "we have a rough identity" to a locked, production-ready brand book in a single working session.

The output is:

- A definitive brand book (positioning, personas, voice, visual identity, messaging)
- A set of interactive HTML exploration artifacts (exploratory, not cleaned up)
- One or more production-ready visual assets (logo, video brief)
- A clear record of every decision and why it was made

The process is designed for a solo founder or small team making all decisions themselves. It uses parallel AI agents aggressively to generate breadth fast, then converges through founder review at defined decision gates.

**Time estimate:** 4–8 hours of founder attention across a full day. Agent work runs in parallel and is much faster; the bottleneck is always the human decision gates.

**What this is not:** an agency-style multi-week discovery sprint. The goal is speed and founder confidence, not committee consensus.

---

## 2. Prerequisites

Gather these before starting. The more context agents have, the less they hallucinate.

### Required

- **Vision or product brief** — even one paragraph describing what the product does and who it's for. This is the source of truth for every strategic decision that follows.
- **README or product description** — how the product is described today, however rough.
- **Existing brand docs** — any prior identity, color choices, or positioning language, even informal notes.

### Strongly recommended

- **Competitor awareness** — a rough mental list of 5–10 products in your space, including indirect competitors. Agents can research this but founder perspective anchors it.
- **Target user intuition** — even a vague "I'm building this for developers like me" is enough to seed persona work.

### Optional but high-value

- **Persona files** — if your vault or notes contain any user research, pain language quotes, or persona sketches, surface them.
- **Prior visual explorations** — any moodboards, logo attempts, or color experiments. Even things you rejected are useful framing.
- **Agent persona config** — if the product has an AI agent persona (e.g., a `.persona.toml`), this informs the product voice section of the brand book.

### Required plugins & skills

These roxabi-plugins skills are used across phases. Install at project scope before starting.

#### Agent types (dev-core)

| Agent type | Used in | Role |
|------------|---------|------|
| `dev-core:product-lead` | Phases 2, 4, 6 | Positioning, personas, messaging, taglines, video brief |
| `dev-core:frontend-dev` | Phases 2, 4, 6, 7 | Visual directions, logo creation, variation galleries |
| `dev-core:doc-writer` | Phase 6 | Brand book, playbook documentation |
| `Explore` (subagent) | Phase 1 | Discovery & audit across repos/vaults |

#### Skills (invoked within agent prompts or standalone)

| Skill | Plugin | Used in | Role |
|-------|--------|---------|------|
| `product-management:write-spec` | product-management | Phase 2 | Positioning statement structure |
| `product-management:competitive-brief` | product-management | Phase 2 | Competitive analysis, battlecards |
| `product-management:synthesize-research` | product-management | Phase 2 | Persona research synthesis |
| `marketing:brand-review` | marketing | Phase 4 | Voice consistency check |
| `marketing:campaign-plan` | marketing | Phase 6 | Video brief, content calendar |
| `marketing:draft-content` | marketing | Phases 4, 6 | Taglines, copy examples |
| `marketing:competitive-brief` | marketing | Phase 2 | Competitive positioning |
| `design:design-handoff` | design | Phase 6 | Logo spec, animation details |
| `design:ux-copy` | design | Phase 4 | Tagline refinement, CTA copy |
| `design:design-critique` | design | Phase 7 | Logo iteration feedback |
| `logo-generator:logo-design` | logo-generator | Phase 6 | Animated SVG logo creation |
| `logo-generator:logo-explore-svg` | logo-generator | Phase 7 | SVG logo variation gallery |
| `logo-generator:logo-explore-ai` | logo-generator | Phase 7 | AI-generated logo concepts (Flux) |
| `visual-explainer:generate-web-diagram` | visual-explainer | All phases | Interactive HTML artifact generation |
| `content-lab:video-recipe` | content-lab | Phase 6 | Video reference analysis |

#### For production (Phase 8+, not covered in this playbook)

| Tool | Role |
|------|------|
| `roxabi-production` + `remotion-factory` | Video composition & rendering |
| `voiceCLI` (TTS) | Narration generation |
| `voiceCLI` (STT/whisper) | Word-level timestamps for captions |
| `imageCLI` | B-roll and visual asset generation |

### File inventory checklist

Before Phase 1 begins, confirm the following are accessible to agents:

- [ ] Primary vision/brief document
- [ ] README
- [ ] Any existing brand or identity files
- [ ] Vault or notes directory (if one exists)
- [ ] Competitor list (can be mental; document it for agents)

---

## 3. Phase-by-Phase Guide

### Phase 1: Discovery and Audit

**Goal:** Gather everything that already exists before creating anything new. No output yet — only inventory.

**Inputs:**
- All file paths listed in prerequisites
- Full access to the codebase and any vaults or notes repos

**Process:**

Spawn **one research agent** with a single task: read everything and produce a gap analysis.

The agent should read:
- Product vision and brief documents
- README and any existing brand docs
- Vault persona files (e.g., `.persona.toml`, avatar notes)
- Visual charter or config files (e.g., `visual-charter.json`)
- Architecture and design principles docs
- Any notes about history, pivots, or rejected directions

The research agent's deliverable is a **written gap analysis** covering:
1. What exists (with file paths)
2. What is contradicted or inconsistent across sources
3. What is entirely missing (e.g., no competitive positioning, no tone-of-voice)
4. What is surprisingly good and should be carried forward

**Outputs:**
- Gap analysis (written summary, no HTML needed — this is internal)
- A consolidated list of all existing positioning language, color decisions, and persona descriptions found

**Decision point:**
Founder reads the gap analysis and confirms:
- "This is everything" — proceed to Phase 2
- "You missed X" — agent reads additional sources and resubmits

Do not skip this phase. Agents in Phase 2 will cite this research as context. Without it, they generate from scratch and produce generic output.

---

### Phase 2: Strategic Exploration (Divergent)

**Goal:** Generate a wide range of strategic and visual options. Do not converge yet. Breadth is the objective.

**Inputs:**
- Phase 1 gap analysis
- All source documents from Phase 1
- Competitor list (founder-supplied or research-agent-compiled)

**Process:**

Spawn **4 agents in parallel**, each with the full Phase 1 context. Every agent produces a standalone interactive HTML artifact.

#### Agent 1: Positioning and Competitive Analysis
Agent type: product-lead

Deliverable: `[product]-positioning-exploration.html`

Contents:
- 3 candidate category definitions (what category does this product live in?)
- 3 positioning angles (what is the lead differentiator?)
- Interactive competitive scatter plot (map 10–15 competitors on two axes)
- 5–8 differentiators ranked by defensibility
- Anti-positioning cards (what this product is explicitly NOT)
- 3 alternative color palette directions (visual signal, not final)

#### Agent 2: Customer Personas and Voice
Agent type: product-lead

Deliverable: `[product]-customer-personas.html`

Contents:
- 4 detailed personas (name, role, day-in-the-life, pain points, goals, objections)
- Customer voice research per persona (exact pain language, desire language, search queries, communities, trigger events that cause them to look for a solution)
- Jobs-to-Be-Done framework (functional job, emotional job, social job) per persona
- Priority order proposal with rationale

#### Agent 3: Brand Voice and Messaging Framework
Agent type: product-lead

Deliverable: `[product]-messaging-framework.html`

Contents:
- Marketing voice guide (5 attributes with "this / not that" contrasts)
- Tone spectrum across contexts (landing page, docs, community, product)
- Writing rules with before/after examples
- Vocabulary (words to use vs. avoid, with reasons)
- 4 messaging pillars with copy examples per pillar
- 8–12 tagline options across 4–5 angles
- Narrative arc (Problem → Agitate → Solution → Proof → CTA)

#### Agent 4: Visual Direction Exploration
Agent type: frontend-dev

Deliverable: `[product]-visual-directions.html`

Contents:
- 5 distinctly different visual directions (label them A–E)
- Each direction includes: color palette with hex values, typography stack, SVG logo concept, card component, hero section mockup, mood keywords
- Tab navigation to switch between directions
- Make directions genuinely different — not variations of the same idea

**Outputs:**
- 4 interactive HTML files in the brand directory
- Each file is self-contained (no external dependencies)

**Decision point:**
None — Phase 2 produces options only. The founder reviews all 4 artifacts before Phase 3 begins.

---

### Phase 3: First Convergence Round

**Goal:** Narrow from wide exploration to 2–3 viable directions across all dimensions. This is a founder gut-check, not a final decision.

**Inputs:**
- All 4 Phase 2 artifacts
- Founder's gut reactions

**Process:**

Founder opens all 4 HTML files and takes notes. For each dimension, identify:

1. **Visual**: Which direction(s) feel right? Which are immediately wrong and why?
2. **Positioning**: Which category label and which angle resonate?
3. **Messaging**: Which pillar resonates most? Which taglines feel true vs. generic?
4. **Personas**: Which persona is the primary target? Does it match the visual direction?

**Critical question to ask yourself:** Does the visual direction match the primary persona's identity? A technically precise visual (Swiss grid, neutral palette) might appeal to you aesthetically but miss what your primary user will feel is "built for them."

Capture reactions as a short written brief. Minimum viable input per dimension:
- "I liked X but not Y because..."
- "The [label] feels right, not [label]"
- "Pillar [N] resonated because..."

**Outputs:**
- A written convergence brief (even rough notes work)
- Implicit kill list: which Phase 2 directions are eliminated

**Decision point:**
Before Phase 4, confirm:
- 1 primary persona is locked
- 1 positioning angle is leading (even if not final)
- 1–2 visual directions survive (the others are eliminated)
- At least 1 messaging pillar is flagged as resonant

If the visual direction that "looked best" doesn't match the locked persona — eliminate it. Visual identity must serve the primary audience, not the founder's aesthetic preferences.

---

### Phase 4: Refined Exploration (Convergent)

**Goal:** Go deeper on the chosen directions. Generate polished options from the narrowed set.

**Inputs:**
- Phase 3 convergence brief
- Eliminated directions (so agents don't regenerate them)
- All Phase 2 source artifacts for reference

**Process:**

Spawn **2 agents in parallel**.

#### Agent 1: Visual Directions v2
Agent type: frontend-dev

Deliverable: `[product]-visual-directions-v2.html`

Contents:
- 3 new directions, all oriented around the energy/metaphor identified in Phase 3
- Each with: palette, typography, logo concept, card, hero, a component that demonstrates the core metaphor in action (e.g., a modularity showcase)
- Label these F–H (continuing from A–E) so there's no confusion with Phase 2

#### Agent 2: Taglines and Positioning Refinement
Agent type: product-lead

Deliverable: `[product]-taglines-refined.html`

Contents:
- 15–20 taglines in 4–5 groups (name each group by its angle)
- Refined positioning statement (one paragraph, anatomized into components)
- 2 elevator pitches (one technical audience, one non-technical)
- One-pager structure proposal
- Resolution of any tensions identified in Phase 3 (e.g., "intelligence vs. relationship" language)

**Outputs:**
- 2 interactive HTML files

**Decision point:**
After reviewing both artifacts, founder locks:
- Final visual direction (one of F, G, or H)
- Lead tagline
- 1–2 supporting taglines
- Final category label

Write these decisions down explicitly. Phase 6 agents treat them as immutable inputs.

---

### Phase 5: Final Convergence

**Goal:** Lock all decisions. No more exploration.

**Inputs:**
- Phase 4 artifacts
- Phase 3 convergence brief (for context)

**Process:**

Founder makes final calls. Document every decision with the reason:

- Visual: [direction chosen] because [reason]
- Lead tagline: "[tagline]" because [reason]
- Category: "[label]" because [reason]
- Primary persona: [name] because [reason]
- Lead messaging pillar: [pillar] because [reason]

The "because" is important. When someone asks why in six months — or when you're briefing a designer or copywriter — this record is what prevents brand drift.

**Outputs:**
- A locked decision document (can be a single markdown section, not a separate file)

**Decision point:**
All dimensions are locked before Phase 6 begins. Phase 6 agents do not make brand decisions — they execute them.

---

### Phase 6: Production Assets

**Goal:** Create definitive brand assets from locked decisions.

**Inputs:**
- Phase 5 locked decisions
- All Phase 1–4 context
- Specific asset briefs (see below)

**Process:**

Spawn **3 agents in parallel**.

#### Agent 1: Logo
Agent type: frontend-dev

Deliverable: `[product]-logo-[direction].html` (e.g., `lyra-logo-forge.html`)

Contents:
- Animated SVG logo based on the locked visual direction
- Intro animation sequence (5–7 phases that tell the product story)
- Idle loop animations (subtle, reinforce "always running")
- Wordmark + submark + variant lockups
- Export-ready format

Brief this agent with: hex values, typography, core metaphor, primary logo mark description, animation principles from the visual direction.

#### Agent 2: Brand Book
Agent type: doc-writer

Deliverable: `BRAND-BOOK.md`

Contents (8 sections):
1. Brand overview (what it is, mission, category)
2. Positioning (statement, anatomy, competitive stance, anti-positioning)
3. Target audience (personas with summaries, priority order, copy guidance)
4. Brand voice (marketing voice attributes, context register, product/agent voice, writing rules, vocabulary)
5. Messaging (pillars, taglines, narrative arc, any language tensions resolved)
6. Visual identity (direction, color palette, typography, logo description, animation principles)
7. Core metaphor (elements and meaning, why it works for this product, key tensions)
8. Asset index (all files in brand directory with descriptions)

This document replaces any prior identity doc as the authoritative reference.

#### Agent 3: Video Creative Brief (optional)
Agent type: product-lead

Deliverable: `[product]-video-brief.html`

Contents:
- 2–3 video specs (launch trailer, product demo, social clips)
- Scene-by-scene breakdown per video
- Component mapping to your animation/video framework (e.g., Remotion)
- Production pipeline notes

Skip this agent if video is not in the current roadmap.

**Outputs:**
- Logo HTML file
- `BRAND-BOOK.md`
- Video brief HTML (if produced)

**Decision point:**
Founder reviews the brand book for accuracy and completeness. The logo is reviewed separately — expect at least one iteration (see Phase 7).

---

### Phase 7: Asset Iteration

**Goal:** Refine specific assets based on feedback. This phase repeats as needed.

**Inputs:**
- Specific feedback on Phase 6 assets
- The locked decisions from Phase 5 (as guardrails)

**Process:**

Iteration on the logo is almost always needed. Typical feedback categories:
- "Element X doesn't work — replace with Y"
- "The metaphor is right but the execution is off"
- "Simplify — remove Z"

**Critical rule: always version files.**

Never edit the base file. Create `[product]-logo-[direction]-v0.2.html`, then `v0.3.html`. Reasons:
- You may want to go back to a previous version
- A variation gallery (showing all versions side by side) becomes valuable for decision-making
- The base file is the stable reference; variants are experiments

When building a variation gallery (multiple logo variants in one file), use a shared base template approach:
- Define the shared elements (e.g., diamond shape, wordmark, colors) as a JavaScript template or CSS variables
- Each variant only specifies what differs (e.g., aura style, base element)
- This prevents copy-paste drift and makes the gallery easier to maintain

---

## 4. Agent Orchestration Patterns

### When to parallelize

Parallelize when agents need the **same inputs** and produce **independent outputs**. All of Phase 2 and Phase 6 qualify.

Do not parallelize when:
- One agent's output is another agent's input (serialize these)
- Agents would make conflicting strategic decisions (only one strategic decision-maker at a time)
- You haven't finished your own decision gate (agents working from ambiguous briefs produce ambiguous output)

### When to serialize

- Phase 1 must complete before Phase 2 (research before creation)
- Phase 3 (founder review) must complete before Phase 4 (agents need the narrowed brief)
- Phase 5 (locked decisions) must complete before Phase 6 (agents execute, not explore)

### How to brief agents

Every agent in this process should receive:
1. **Context block**: What product, what phase, what has been decided so far
2. **Task brief**: Exactly what to produce (deliverable name, format, contents)
3. **Source files**: Which files to read before starting
4. **Constraints**: What to exclude (eliminated directions, off-limits language, etc.)

The more specific the brief, the better the output. "Explore visual directions" produces generic results. "Produce 5 visual directions for a self-hosted AI agent engine targeting engineer-builders; directions should span the spectrum from technical/minimal to warm/organic; each direction must include a logo concept, color palette with 6 tokens, and a hero mockup" produces useful output.

### Handling questions from agents

Agents will sometimes surface ambiguities mid-task. Three categories:

1. **Factual gaps** (e.g., "I couldn't find a competitor list") — resolve immediately, provide the information, let the agent continue
2. **Strategic choices** (e.g., "Should I frame this as B2C or B2B?") — if this is a Phase 2 exploration agent, tell them to explore both; if this is a Phase 6 execution agent, answer the question definitively
3. **Out-of-scope requests** (e.g., "Should I also build a landing page?") — no; scope agents tightly

### Token limits

Long-running agents (especially frontend-dev building complex HTML) can hit context limits before completing. Mitigations:
- Break large artifacts into logical sections and build them sequentially
- Use shared templates or component definitions at the top of the file and reference them by name in sections
- If an agent hits a limit mid-artifact, have it produce what it has and note what's missing; do not ask it to start over unless the output is truly unusable

---

## 5. Artifact Format

### Why interactive HTML

Interactive HTML artifacts are the right format for brand exploration for several reasons:

- **Self-contained** — one file, no dependencies, opens in any browser, shareable by drag-and-drop
- **Founder-reviewable without tools** — no Figma, no design software, no account needed
- **Visually explorable** — tab navigation lets a founder switch between directions without context-switching
- **Annotatable** — the source is readable; agents can add comments inline
- **Versionable** — a 50KB HTML file is trivially stored in git

Do not use Figma, Notion, or any tool that requires an account for brand exploration artifacts. The goal is speed and frictionless review, not polished handoff.

### Naming conventions

Use this pattern consistently:

```
[product]-[artifact-type].html
[product]-[artifact-type]-v[N].[M].html    # for versioned iterations
```

Examples:
```
lyra-visual-directions.html           # Phase 2 exploration
lyra-visual-directions-v2.html        # Phase 4 refined exploration
lyra-logo-forge.html                  # Phase 6 production logo (direction name in filename)
lyra-logo-forge-v0.2.html             # Phase 7 first iteration
lyra-logo-forge-v0.3.html             # Phase 7 second iteration
```

The brand book and identity docs use uppercase: `BRAND-BOOK.md`, `BRAND-IDENTITY.md`.

### Versioning rules

These rules prevent the most common mistake (editing the base file and losing the original):

1. The base file (v1 or no version suffix) is always the first production version. Never edit it after delivery.
2. Iterations are always new files with a version suffix: `v0.2`, `v0.3`, `v1.1`.
3. `v0.x` = pre-lock iteration (still exploring)
4. `v1.x` = post-lock iteration (refinements only, no strategic changes)
5. When building a variation gallery, put it in a new file: `[product]-logo-[direction]-gallery.html`

### Artifact lifetime

Exploration artifacts (Phases 2 and 4) are never authoritative — they are research. The brand book is authoritative. When there is a conflict between an exploration artifact and the brand book, the brand book wins.

Keep exploration artifacts in the brand directory for reference and historical record. Do not delete them.

---

## 6. Key Learnings

### What worked well

**Parallel agent spawning in Phase 2.** Generating all four strategic dimensions simultaneously — rather than sequencing them — produces genuine breadth. Each agent approaches the product fresh, without being anchored to another agent's framing. This is where unexpected angles emerge.

**The visual-persona fit check.** The most important question in Phase 3 is not "which direction looks best" but "which direction fits the person you're building for." Aesthetic preference and persona fit are often different answers. Forcing this question explicitly — before Phase 4 — prevented wasted work on directions that looked good but would have repelled the primary user.

**The bidirectionality insight.** Strategic insights often emerge from reviewing messaging pillars, not positioning statements. In the Lyra session, the resonance with Pillar 4 (Extensibility) led to the insight that extension is bidirectional — you extend Lyra, and Lyra extends you. That reframe was more generative than any positioning angle produced by the positioning agent. Pay attention to what resonates in messaging; it often contains the clearest statement of your core value.

**Anti-positioning as a filter.** The anti-positioning cards produced by the positioning agent were more immediately useful than the positioning options themselves. Knowing what you are NOT eliminates half the copy decisions and prevents the brand from drifting toward safe, generic language.

**The decision-before-execution gate.** Spawning Phase 6 production agents before Phase 5 decisions are locked produces off-target output. The one-time cost of a proper decision gate is recovered many times over in agent output quality.

### What to do differently

**Version files from the start.** This is the single most common operational mistake. The first iteration of a production asset is almost never the final one. Build the versioning habit from Phase 6 delivery — not after the first round of feedback when the base file has already been modified.

**Brief variation galleries as split-architecture files.** When building a gallery of logo variants, define shared elements (core mark, wordmark, colors) in one JavaScript template block and have each variant only specify its unique properties. This prevents the most common failure mode: copy-paste drift between variants making the gallery difficult to maintain.

**Anchor persona work to real language.** The most useful part of the persona artifacts is the customer voice research — specifically the pain language and trigger event phrasing. This language should feed directly into headline copy and the narrative arc. In Phase 2, explicitly instruct the persona agent to produce exact-phrasing quotes (not paraphrases) for each persona's trigger event.

**Name the core metaphor early.** In the Lyra session, the Forge metaphor emerged in Phase 4 (visual v2) and then needed to be backfilled into positioning and messaging. If a strong metaphor emerges at any point, immediately check whether it restructures prior work. A named metaphor is the most powerful alignment tool in the brand — it makes every subsequent decision faster.

**Resolve the intelligence-vs-relationship tension explicitly.** Most products with an AI agent component face a version of this tension: warm/relational language attracts some users and repels others. Don't leave it implicit. Force the brand book to include a section that defines where each register is appropriate and provides specific before/after examples. See Section 5 of the Lyra brand book for a worked example.

---

## 7. Checklist

A quick reference for running this process on a new product.

### Pre-session

- [ ] Vision or product brief document exists and is accessible to agents
- [ ] README or product description is current
- [ ] Existing brand docs (if any) are located and listed
- [ ] Vault/notes directory is accessible (if one exists)
- [ ] Competitor list (mental or written) is ready to share with agents

### Phase 1

- [ ] Research agent has read all relevant source files
- [ ] Gap analysis is written (not just a file list — actual assessment of what's missing)
- [ ] Founder has reviewed gap analysis and confirmed completeness

### Phase 2

- [ ] All 4 agents briefed with the full context block (product, source files, task, constraints)
- [ ] Each agent produces a standalone HTML file (no external dependencies)
- [ ] Visual directions agent produces 5 genuinely different directions (A–E)
- [ ] Founder has time to review all 4 artifacts before Phase 3

### Phase 3

- [ ] Founder has reviewed all 4 Phase 2 artifacts
- [ ] Convergence brief written (even rough notes)
- [ ] Primary persona is named and locked
- [ ] 1–2 visual directions survive (others are explicitly killed)
- [ ] At least 1 resonant messaging pillar is identified
- [ ] Visual direction vs. persona fit has been explicitly checked

### Phase 4

- [ ] Visual v2 agent brief includes the metaphor/energy direction and the eliminated directions
- [ ] Tagline agent brief includes the convergence brief and the leading positioning angle
- [ ] Both agents label their directions with new letters (F–H, not A–E)

### Phase 5

- [ ] Final visual direction locked (with reason)
- [ ] Lead tagline locked (with reason)
- [ ] Category label locked (with reason)
- [ ] Primary persona confirmed (with reason)
- [ ] Decision record written before Phase 6 begins

### Phase 6

- [ ] Logo agent brief includes: hex values, typography stack, logo mark description, animation principles
- [ ] Brand book agent has access to all Phase 1–5 artifacts as source material
- [ ] Brand book explicitly replaces any prior identity doc (state this in the frontmatter)
- [ ] All files are versioned correctly — no editing the base file

### Phase 7 (iteration)

- [ ] Feedback on logo is specific (what to remove/add/change, not just "I don't like it")
- [ ] Iteration is a new file with version suffix (v0.2, v0.3) — base file untouched
- [ ] If building a variation gallery: shared elements are defined once, variants only specify differences

### Brand book quality check

- [ ] Section 1: Product is defined, category is claimed, mission is stated
- [ ] Section 2: Positioning statement is written, competitive stance is explicit, anti-positioning is listed
- [ ] Section 3: Personas are in priority order, primary persona has a copy guidance rule
- [ ] Section 4: Marketing voice is distinct from product/agent voice; both are documented
- [ ] Section 5: All pillars are ordered, taglines are listed with lead tagline called out, narrative arc is present
- [ ] Section 6: All color tokens have hex values and semantic roles; typography roles are defined; logo description is literal enough to re-implement
- [ ] Section 7: Core metaphor is named, its elements are mapped to product architecture, bidirectionality (or equivalent tension) is resolved
- [ ] Section 8: Every file in the brand directory is listed with a one-line description

---

*Derived from the Lyra brand exploration session — 2026-03-18*
*Authored as a reusable process playbook for all Roxabi products.*
