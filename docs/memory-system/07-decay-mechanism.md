# 07 - Mécanisme de Decay (Oubli Temporel)

## Objectif du Decay
Le decay permet au système d'**oublier progressivement** les informations peu utilisées, exactement comme le cerveau humain (courbe d'Ebbinghaus).

Il s'applique à **deux niveaux distincts** :
- Sur les **liens / relations** (`weight_temporal`)
- Sur les **souvenirs / mémoires** (entités et Compiled Truth)

Cela garantit que le Knowledge Graph et les prompts restent toujours pertinents et légers, même après des années d'accumulation de données.

---

### 1. Decay sur les Liens (Relations)

**Formule utilisée :**
```math
weight_temporal = exp(-Δt / σ)
```
où :
- `Δt` = temps écoulé depuis le dernier renforcement de la relation (en jours)
- `σ` = constante de demi-vie (configurable par type de relation)

**Valeurs par défaut de σ** (dans `config/decay.yaml`) :
- Relations très importantes (`is_founder_of`, `is_core_concept_of`, etc.) → σ = 90 jours
- Relations normales → σ = 30 jours
- Relations faibles / contextuelles → σ = 7 jours

**Quand le decay est appliqué ?**
- **Asynchrone / Background** : à chaque mise à jour d'une relation (Retain Job ou Entity Resolution)
- **CRON Nightly** : recalcul massif de tous les `weight_temporal` chaque nuit

**Effet concret :**
Quand `weight_temporal` descend en dessous de **0.2**, la relation devient **presque invisible** lors des recherches multi-stratégie (sauf si recherche temporelle explicite).

---

### 2. Decay sur les Souvenirs / Mémoires (Entités)

Le decay sur les entités suit une **courbe d'oubli avec renforcement** plus sophistiquée.

**Formule (inspirée Hippo + Ebbinghaus) :**
```math
memory_strength = base_strength × (1 + reinforcement_factor) × exp(-Δt / half_life)
```
où :
- `base_strength` : force initiale lors de la création (0.0 à 1.0)
- `reinforcement_factor` : +0.3 à chaque rappel / utilisation récente
- `half_life` : demi-vie configurable par type d'entité (ex: 45 jours pour un projet, 10 jours pour un concept mineur)

**Mécanismes de renforcement :**
- Chaque fois qu'une entité est **rappelée** (utilisée dans une recherche ou injectée dans un prompt) → + reinforcement
- Chaque fois qu'elle apparaît dans un nouveau Raw → + renforcement fort
- Pendant le **Nightly Job** : consolidation des entités très utilisées

**Quand le decay est appliqué ?**
- **Background** : à chaque rappel ou nouvelle mention
- **CRON Nightly** : recalcul global de toutes les entités + éventuel "sleep cycle"

**Effet sur le Compiled Truth :**
- Si `memory_strength` < **0.35** → le Compiled Truth de cette entité **n'est plus injecté automatiquement** (sauf demande explicite)
- Seule la version la plus récente reste dans le Markdown, mais elle perd de la priorité dans les recherches

---

### 3. Interaction Decay ↔ Compiled Truth

Le Compiled Truth **n'est jamais supprimé**. Il est simplement :
- Moins souvent injecté dans les prompts
- Régénéré uniquement si l'entité redevient forte

Possibilité de forcer une régénération manuelle via commande (`/rebuild compiled <slug>`).

---

### 4. Synthèse des Déclencheurs

| Action                          | Type                | Fréquence          | Ce qui est impacté              |
|---------------------------------|---------------------|--------------------|---------------------------------|
| Mise à jour d'une relation      | Background          | À chaque événement | `weight_temporal`               |
| Rappel d'une entité             | Background          | À chaque recherche | `memory_strength`               |
| Nouveau Raw                     | Event-triggered     | Immédiat           | Renforcement + Retain           |
| Nightly Consolidation           | CRON                | Toutes les nuits   | Decay global + consolidation    |

---

**Points clés à retenir :**
- Le decay est **progressif et non brutal** (pas de suppression soudaine).
- Il y a toujours un **renforcement** quand on réutilise une information.
- Le Raw Layer reste **immuable** : même si une entité est très faible, on peut toujours la faire remonter en relisant l'historique brut.
- Tout est configurable via `config/decay.yaml`.
