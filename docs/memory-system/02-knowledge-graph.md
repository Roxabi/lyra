# 02 - Knowledge Graph + Vector Store

## À quoi ça sert ?
Cerveau structuré de la machine : stockage optimisé pour la **recherche rapide**, le **traversal de relations** et la **gestion intelligente de la temporalité** (decay, renforcement, oubli).

C'est ici que le système transforme la donnée brute en connaissance exploitable en temps réel.

## Qui l'utilise ?
- **Main Orchestrator** (recherche multi-stratégie synchrone)
- **Job de nuit** (mise à jour + decay + consolidation)
- **Retain Job** (event-triggered)
- **Sub-agents** (requêtes ciblées en lecture seule, très rarement)

## Architecture technique détaillée

- **Backend principal** : PostgreSQL 16 + **pgvector** (recommandé pour simplicité et performance)
- **Option avancée** (relations très denses) : Qdrant (vector) + Neo4j (graph)

### Schéma principal (tables clés)

```sql
-- Entities
CREATE TABLE entities (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,           -- identifiant humain unique (ex: projet-lyra)
    type TEXT NOT NULL,                  -- person, project, concept, document...
    name TEXT NOT NULL,
    embedding VECTOR(1536),              -- text-embedding-3-large ou nomic-embed-text-v1.5
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    memory_strength FLOAT DEFAULT 1.0    -- pour le decay sur les entités
);

-- Relations (avec decay temporel)
CREATE TABLE relations (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES entities(id),
    target_id BIGINT REFERENCES entities(id),
    relation_type TEXT NOT NULL,         -- is_founder_of, mentions, part_of...
    weight_semantic FLOAT DEFAULT 1.0,
    weight_temporal FLOAT DEFAULT 1.0,   -- decay = exp(-Δt / σ)
    confidence FLOAT DEFAULT 0.9,
    timestamp TIMESTAMPTZ NOT NULL,
    metadata JSONB
);
```

- **Namespaces de mémoires** : `world_facts`, `experiences`, `mental_models` (stockés dans une table `memories` ou via metadata)
- **Index** :
  - HNSW sur les embeddings (recherche vectorielle ultra-rapide)
  - GIN sur les relations + indexes BRIN sur les timestamps
- **Formule de decay** (intégrée directement) :
  ```math
  weight_temporal = exp(-Δt / σ)
  ```
  (σ configurable par type de relation – voir 07-decay-mechanism.md)

## Repo de référence
**vectorize-io/hindsight** – https://github.com/vectorize-io/hindsight

**Ce qu'on change** :
- Ajout natif du **decay temporel** (weight_temporal + memory_strength) inspiré de Hippo
- Hybrid self-wiring (regex + LLM) pour créer automatiquement des liens déterministes
- Stockage exclusif d'embeddings 1536 dimensions
- Plus de séparation stricte entre Raw et Graph (le Graph est **entièrement dérivé**)

---

**Points clés à retenir :**
- Le Knowledge Graph est **toujours dérivé** du Raw Layer → il peut être entièrement reconstruit.
- La recherche multi-stratégie (vector + graph + temporal) est le cœur de la performance de l'orchestrateur.
- Le decay est appliqué en background et nightly pour garder le graphe propre et pertinent.
