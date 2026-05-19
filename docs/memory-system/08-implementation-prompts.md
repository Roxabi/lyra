# 08 - Prompts d'Implémentation LLM

## À quoi ça sert ?
Centraliser, versionner et documenter **tous** les prompts LLM utilisés dans le pipeline de mémoire.

Cela garantit :
- Cohérence absolue des extractions, décisions et régénérations
- Reproductibilité totale (même entrée → même sortie structurée)
- Facilité d'amélioration itérative et de A/B testing
- Performance optimisée (structured output JSON, température contrôlée)

## Qui l'utilise ?
- **Retain Job** (event-triggered après chaque écriture Raw)
- **Entity Resolution** (background)
- **Impact Analysis**
- **Compiled Truth Regeneration**
- **Nightly Consolidation Job**
- **Main Orchestrator** (recherche multi-stratégie + injection contexte)
- **Outils de validation humaine**
- **Sub-agents** (prompts de synthèse finale)

## Architecture technique détaillée

### Emplacement des prompts
```
prompts/
├── retain/
│   ├── system_v1.jinja
│   └── fewshot_examples.json
├── entity_resolution/
│   ├── system_v1.jinja
│   └── schema.json
├── compiled_truth_regeneration/
│   ├── system_v1.jinja
│   └── examples/
├── impact_analysis/
├── orchestrator_search/
└── validation_human/
```

Chaque prompt est :
- **Versionné** (`_v1`, `_v2`, etc.)
- **Stocké en Jinja2** pour injection dynamique de variables
- **Accompagné d'un JSON Schema** (Pydantic v2) pour forcer le structured output
- **Testé** contre un golden dataset (dans `tests/prompts/`)

### Paramètres LLM par défaut (dans `config/llm.yaml`)
- Modèle : `grok-beta` (ou équivalent)
- Température : **0.0** pour Retain / Resolution / Regeneration (déterministe)
- Température : **0.3** pour Compiled Truth (légère créativité de synthèse)
- Mode : **JSON mode** + guided decoding

---

### 8.1 Prompt Retain (Extraction d'entités et faits)

**Fichier** : `prompts/retain/system_v1.jinja`

```markdown
Tu es un expert en extraction d'entités et de faits pour un système de mémoire à long terme ultra-précis.

Entrée : une entrée brute du Raw Layer (JSON).

Ta tâche :
1. Extraire toutes les **entités** importantes.
2. Extraire les **faits** bruts et les **relations** potentielles.
3. Ne rien inventer.

Réponds UNIQUEMENT en JSON valide selon le schéma suivant :

{{ schema | tojson }}

Règles strictes :
- `entities` : slug unique, type, name, confidence (0-1)
- `facts` : phrase concise, importance (1-5)
- `relations` : source_slug → target_slug → relation_type
- Toujours inclure `timestamp_utc` et `raw_entry_hash`
```

---

### 8.2 Prompt Entity Resolution

**Fichier** : `prompts/entity_resolution/system_v1.jinja`

```markdown
Tu es un expert en Entity Resolution pour un Knowledge Graph.

Tu reçois :
- Une entité candidate
- La liste des 10 entités les plus similaires existantes (slug, name, cosine, attributs)

Décide : merge / nouvelle / ambigu

Réponds UNIQUEMENT en JSON selon le schéma.

Critères stricts :
- Score > 0.85 → merge automatique
- Score 0.6-0.85 → validation humaine
- Score < 0.6 → nouvelle entité
```

---

### 8.3 Prompt Compiled Truth Regeneration

**Fichier** : `prompts/compiled_truth_regeneration/system_v1.jinja`

```markdown
Tu es un rédacteur expert en "Compiled Truth".

Génère une nouvelle version Markdown selon ce template exact :

# {{ entity_name }}
**Dernière mise à jour** : {{ today }}

## Synthèse actuelle
[2-4 paragraphes ultra-denses]

## Évolution récente
[bullet points]

## Sources principales
- Raw entries : [...]
- Relations clés : [...]

Règles :
- Style neutre, professionnel, humain
- Longueur cible : 300-600 mots
- Prioriser les faits avec weight_temporal > 0.4 et memory_strength > 0.35
```

---

### 8.4 Prompt Impact Analysis & Orchestrateur

- **Impact Analysis** : liste de slugs à régénérer avec score d'impact.
- **Orchestrateur Search** : reformulation + sélection des Compiled Truth et faits à injecter.

---

## Bonnes pratiques communes

| Pratique              | Détail |
|-----------------------|--------|
| Structured Output     | Toujours JSON + Pydantic |
| Few-shot              | 3-5 exemples quand nécessaire |
| Température           | 0.0 pour tout ce qui est factuel |
| Logging               | Chaque appel tracé dans le Raw Layer |
| Versionning           | Changement → nouvelle version + entrée Raw |

## Repo de référence
- **garrytan/gbrain**
- **kitfunso/hippo-memory**

**Ce qu'on change** :
- Jinja2 + JSON Schema systématiques
- Température 0.0 sur tous les jobs critiques
- Tracabilité complète via `raw_entry_hash`

---

**Points clés à retenir :**
- Les prompts sont la **seule source de vérité** du comportement du pipeline.
- Toute modification doit être validée sur le golden dataset.
- Le Raw Layer garde la trace brute de chaque appel LLM.
