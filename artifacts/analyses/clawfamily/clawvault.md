# ClawVault — Deep Analysis

> **URL**: https://github.com/Versatly/clawvault
> **Date**: 2026-03-10
> **Category**: Persistent memory system for AI agents
> **Family**: ClawFamily (OpenClaw ecosystem wrappers & harnesses)
> **License**: MIT (open-source, npm package)

---

## TL;DR

ClawVault est la **memory-plane** du ClawFamily. Où Paperclip orchestre les agents et MetaClaw les entraîne, ClawVault résout le problème fondamental de la **"context death"** — agents qui perdent tout contexte entre sessions. Markdown comme primitive de stockage, graph-aware, local-first, session-resilient.

**Tagline**: *"An elephant never forgets. Neither should your AI."*

**Position**: Si OpenClaw est l'employé, Paperclip est l'entreprise, MetaClaw est la formation, ClawVault est la **mémoire institutionnelle**.

---

## 1. Product Overview

### What It Is

Un système de mémoire structurée CLI + npm, qui stocke les connaissances d'un agent en fichiers Markdown organisés dans un vault local. Chaque session génère des observations, décisions, leçons qui persistent entre contextes. Un graph de connaissances émerge naturellement des wiki-links.

### Core Problem Solved

Les agents AI sont amnésiques par design — chaque session repart de zéro. ClawVault résout cela sans base de données complexe : des fichiers Markdown humainement lisibles, versionnable avec git, directement exploitables dans Obsidian.

### What Makes It Different

| vs. | ClawVault fait |
|-----|----------------|
| Vector databases (Pinecone, Weaviate) | Local-first, aucun cloud, coût zéro |
| MEMORY.md statique | Structure dynamique, graph traversal, search hybride |
| Mem0 / Zep | Markdown-native, git-friendly, lisible par humains |
| LangChain memory | Indépendant du framework agent, fonctionne avec tout |
| Notion/Obsidian seuls | Lifecycle agent intégré (wake/sleep/checkpoint) |

---

## 2. Tech Stack

| Layer | Technology |
|-------|-----------|
| **Language** | TypeScript |
| **Runtime** | Node.js 18+ |
| **Package** | npm (`clawvault`) |
| **Search** | `qmd` (github:tobi/qmd) — BM25 + semantic |
| **Hybrid search** | BM25 + embeddings + Reciprocal Rank Fusion (RRF) |
| **Graph** | JSON graph index (wiki-links + frontmatter) |
| **Storage** | Markdown files (filesystem) |
| **Integrations** | Obsidian, OpenClaw hooks, Tailscale + WebDAV |
| **LLM providers** | Anthropic, OpenAI, Gemini, xAI, Ollama, OpenAI-compatible |

### LLM Provider Priority

`OpenClaw > Anthropic > OpenAI > Gemini > xAI > Ollama`

### v3.0 — Structured Memory

- **Fact Store** : extraction de faits structurés à l'écriture (conflits résolus, dédupliqués)
- **Entity Graph** : requêtes multi-hop ("Alice travaille chez Google + Google est en CA → Alice est en CA")
- **Hybrid Search** : BM25 + embeddings + RRF

---

## 3. Architecture

### Data Flow

```
Session → Observe → Score → Route → Store → Reflect → Promote
```

### Components

| Composant | Rôle |
|-----------|------|
| **Session Watcher** | Surveille l'activité de l'agent |
| **Observer/Compressor** | Compresse et classe les observations |
| **Router** | Décide du dossier de destination |
| **Markdown Vault** | Stockage fichiers structurés par catégorie |
| **Graph Index** | `graph-index.json` — index des wiki-links |
| **Context Profiles** | Profiles de retrieval selon le contexte |
| **qmd/vec Search** | Recherche hybride BM25 + sémantique |

### Vault Structure

```
vault/
├── .clawvault/           # Internal state
│   ├── graph-index.json  # Knowledge graph
│   ├── last-checkpoint.json
│   └── config.json
├── decisions/            # Key choices with reasoning
├── lessons/              # Insights and patterns
├── people/               # One file per person
├── projects/             # Active work tracking
├── tasks/                # Task files with frontmatter
├── backlog/              # Quick captures and ideas
├── handoffs/             # Session continuity
├── inbox/                # Quick captures
└── templates/            # Document templates
```

### Session Lifecycle

```
clawvault wake                          # Début de session — charge contexte
    ↓
clawvault checkpoint --working-on X    # Mid-session — sauvegarde état
    ↓
clawvault sleep "summary" --next Y     # Fin de session — handoff propre
```

### MEMORY.md vs Vault

Pattern canonique :
- **MEMORY.md** = Boot context (résumé exécutif vu immédiatement par l'agent)
- **Vault** = Full knowledge store (searchable, structured, versioned)

MEMORY.md ⊂ Vault. Mise à jour périodique depuis le vault, pas mirroring complet.

---

## 4. Les 8 Primitives

Framework conceptuel pour modéliser comment les agents interagissent avec la mémoire persistante :

| Primitive | Description | Implémentation ClawVault |
|-----------|-------------|--------------------------|
| **Goals** | Ce que l'agent veut atteindre | `tasks/`, `projects/`, `--working-on` |
| **Agents** | Identité et ownership | `--owner` metadata, handoffs |
| **State Space** | Contexte et environnement | `checkpoint`, `recover`, session state |
| **Feedback** | Apprentissage des résultats | `lessons/`, `observations/`, reflection |
| **Capital** | Ressources et contraintes | Token budgets, context profiles, priority |
| **Institution** | Règles et patterns | `decisions/`, `preferences/`, injection rules |
| **Synthesis** | Combinaison d'informations | Graph traversal, context blending, search |
| **Recursion** | Boucles d'auto-amélioration | `reflect`, weekly promotion, archival |

---

## 5. Context Profiles

| Profile | Purpose |
|---------|---------|
| `default` | Retrieval équilibré |
| `planning` | Contexte stratégique large |
| `incident` | Événements récents, blockers, urgents |
| `handoff` | Transition de session |
| `auto` | Sélectionné par hooks selon l'intent |

---

## 6. Feature Matrix

| Feature | Description |
|---------|-------------|
| `wake` / `sleep` | Lifecycle session agent — boot context + handoff propre |
| `checkpoint` | Sauvegarde état en cours de session |
| `remember` | Stocke une décision, leçon, ou fait |
| `capture` | Capture rapide dans inbox/ |
| `search` / `vsearch` | Recherche keyword + sémantique |
| `context` | Récupère le contexte pertinent pour une tâche |
| `inject` | Injecte décisions/préférences dans un prompt |
| `reflect` | Analyse la session, génère des leçons |
| `graph` | Vue du knowledge graph |
| `entities` | Entités extraites (facts store) |
| `handoff` | Passage de contexte entre agents/sessions |
| `kanban sync` | Sync avec Obsidian Kanban |
| `canvas` | Dashboard Obsidian auto-généré |
| Obsidian | Plugin, graph themes, Bases views, Canvas |
| Tailscale + WebDAV | Sync vault mobile |

---

## 7. Business Model & Positioning

### Current
- MIT licensed, self-hosted, npm package gratuit
- **$CLAW token** sur pump.fun — signal de tokenisation du projet (memecoin angle, red flag)
- Communauté active : 466 tests, 20+ PRs, 6 contributeurs externes

### Positioning
- Ciblage: développeurs d'agents Claude Code, Cursor, GPT-based
- Obsidian integration = audience PKM (Personal Knowledge Management)
- Tailscale + WebDAV = power users, sync multi-device sans cloud

---

## 8. GitHub Metrics (2026-03-10)

| Metric | Value |
|--------|-------|
| Stars | **426** |
| Forks | 31 |
| Language | TypeScript (100%) |
| Created | 2026-01-30 (38 jours) |
| Last pushed | 2026-03-06 |
| License | MIT |
| Tests | 466 (71 fichiers) |
| Contributors | 6 externes |
| PRs merged | 20+ |
| Version | v3.0 |
| npm | `clawvault` |
| Org | Versatly (indépendant) |

---

## 9. ClawFamily Comparison

### Position dans l'écosystème

```
┌─────────────────────────────────────────────────┐
│                   PAPERCLIP                      │
│  Control-plane : org, goals, budgets, multi-agent│
└──────────────────┬──────────────────────────────┘
                   │ orchestrates
┌──────────────────▼──────────────────────────────┐
│                   OPENCLAW                       │
│  The agent — exécute les tâches                  │
└────┬─────────────┬────────────────────────────┬──┘
     │             │                             │
     │ proxied by  │ hooks into                  │ handoffs
     │             │                             │
┌────▼──────┐  ┌───▼───────────────┐         ┌───▼──────┐
│ METACLAW  │  │    CLAWVAULT      │         │  autres  │
│ Learning  │  │  Memory-plane     │         │  agents  │
│ -plane    │  │  vault, graph,    │         └──────────┘
└───────────┘  │  lifecycle, search│
               └───────────────────┘
```

### Head-to-Head (tous les 3)

| Dimension | Paperclip | MetaClaw | ClawVault |
|-----------|-----------|---------|-----------|
| **Layer** | Control-plane | Learning-plane | Memory-plane |
| **Problème** | Coordination chaos | Stagnation agent | Context death |
| **Stars** | 14 091 (7j) | 74 (1j) | 426 (38j) |
| **Language** | TypeScript | Python | TypeScript |
| **Maturité** | v0.3.0, prod-ready | Research (1j) | v3.0, mature |
| **Storage** | PostgreSQL/PGlite | Tinker LoRA cloud | Fichiers Markdown |
| **Cible** | AaaS founders | Researchers | Agent devs / PKM |
| **License** | MIT | MIT | MIT |
| **Concurrent** | CrewAI, LangGraph | RLHF pipelines | Mem0, Zep |

### Complémentarité

Paperclip coordonne *qui fait quoi*. MetaClaw améliore *comment l'agent fait*. ClawVault *se souvient de tout* entre sessions. Les trois se stackent — tu pourrais faire tourner ClawVault sous chaque agent que Paperclip orchestre, avec MetaClaw améliorant chacun en continu.

---

## 10. Relevance to Lyra / 2ndBrain

### Alignements Forts

| ClawVault concept | Lyra équivalent | Notes |
|------------------|----------------|-------|
| Vault structure (decisions/, lessons/, tasks/) | Mémoire 5 niveaux | Mapping quasi-parfait avec les niveaux épisodique, sémantique, procédural |
| MEMORY.md vs Vault | MEMORY.md vs memory.db | **Exactement** le même split que Lyra utilise déjà |
| Context profiles (default/planning/incident/handoff) | N/A pour l'instant | Lyra n'a pas encore de profils de retrieval — intéressant à implémenter |
| Hybrid search (BM25 + embeddings + RRF) | SQLite + BM25 + sqlite-vec | Déjà fait dans 2ndBrain — ClawVault valide l'approche |
| wake/sleep/checkpoint | Session memory niveau 2 | Lyra a les sessions JSONL mais pas de lifecycle structuré avec handoffs explicites |
| Injection de contexte (inject, context) | SLM routing + mémoire procédurale | ClawVault le fait en CLI, Lyra doit l'automatiser |
| Entity graph + multi-hop | Non implémenté | Lyra v2 potentiel — les entités NER pourraient alimenter un graph |
| reflect (session analysis) | Compaction automatique | 2ndBrain compacte déjà — ClawVault ajoute la génération de leçons explicites |

### Ce que Lyra ne fera pas comme ClawVault

| ClawVault feature | Pourquoi pas pour Lyra |
|------------------|------------------------|
| Fichiers Markdown | Lyra utilise SQLite — meilleur pour la recherche sémantique et le volume |
| Obsidian integration | Hors scope — Lyra est un assistant conversationnel pas un PKM |
| Tailscale + WebDAV | Lyra ne sync pas de fichiers |
| qmd (external) | Dépendance externe fragile — Lyra internalise son propre search |

### Emprunts Prioritaires

1. **Context profiles** — Implémenter `incident` / `planning` / `handoff` dans le retrieval de Lyra selon le contexte détecté
2. **Les 8 primitives** — Framework conceptuel excellent pour auditer la complétude de l'architecture mémoire de Lyra
3. **Lifecycle wake/sleep/handoff** — Lyra a des sessions JSONL mais pas de handoff structuré avec intent + next steps — à implémenter
4. **Reflection engine** — Après chaque session, générer automatiquement des leçons vers la mémoire procédurale

---

## 11. Risks & Concerns

| Risk | Severity | Notes |
|------|----------|-------|
| $CLAW token (pump.fun) | High | Signal de projet spéculatif/memecoin. Peut distraire les priorités du projet. |
| qmd dependency | High | Installé depuis GitHub, pas npmjs.com — fragile, single author (tobi). Si `qmd` disparaît, search casse. |
| Fichiers Markdown à l'échelle | Medium | Performance dégradée avec beaucoup de fichiers. SQLite serait plus robuste. |
| TypeScript/Node (vs Python) | Low | Ecosystème différent de Lyra — pas d'intégration directe possible, que de l'inspiration |
| Obsidian coupling | Low | Forte dépendance à l'écosystème Obsidian pour certaines features. Hors scope pour Lyra. |

---

## Summary

ClawVault est le projet le plus mature du ClawFamily (v3.0, 466 tests, 38 jours). Il résout **context death** — le problème le plus fondamental et universel pour les agents AI — avec une approche deliberately simple : des fichiers Markdown.

La validation la plus importante pour Lyra : **le split MEMORY.md vs vault** est exactement l'architecture que Lyra utilise déjà. **Les 8 primitives** sont un excellent framework d'audit. **Le lifecycle wake/sleep/handoff** est une lacune concrète de Lyra.

Red flag notable : le token $CLAW sur pump.fun suggère une dimension spéculative qui peut nuire à la crédibilité et aux priorités du projet à long terme.

**Verdict** : S'inspirer fortement des patterns conceptuels (8 primitives, context profiles, reflection engine). Ne pas intégrer directement (TypeScript vs Python, fichiers vs SQLite). Surveiller : si le token $CLAW prend le dessus, le projet perdra en qualité.
