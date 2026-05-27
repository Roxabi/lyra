# 04 - Job de Consolidation Nocturne

## À quoi ça sert ?
Processus automatique qui transforme la donnée brute du jour en connaissance structurée (Knowledge Graph + Compiled Truth).

C'est le **cœur du maintien de la mémoire** : il extrait, résout, renforce et fait vieillir intelligemment toutes les informations du système.

## Qui l'utilise ?
Le système entier – c'est le processus central qui fait vivre la mémoire à long terme.

## Architecture technique détaillée & Séquence

Le job s'exécute en deux modes :
- **Retain Job** : event-triggered (dès qu'une nouvelle entrée est écrite dans le Raw Layer)
- **Nightly Consolidation Job** : CRON toutes les nuits (ex: 03:00)

### Séquence complète (Retain + Nightly)

1. **Scan Raw** : lecture de toutes les nouvelles entrées du jour (ou de la dernière entrée pour le Retain)
2. **Retain** : extraction d'entités candidates + faits + relations via LLM (prompt structuré – voir 08)
3. **Entity Resolution** :
   - Fuzzy matching (Levenshtein + Metaphone)
   - Cosine similarity sur embeddings (> 0.85 = merge automatique)
   - Attributs secondaires (email, URL, slug, date)
   - Score ambigu (0.6-0.85) → file de validation humaine
4. **Mise à jour Graph** : ajout faits + relations + mise à jour des `weight_temporal` et `memory_strength` (decay + renforcement)
5. **Impact Analysis** : détection des Compiled Truth impactés par les nouvelles données
6. **Regénération** : LLM génère ou met à jour la version Markdown du Compiled Truth (seulement les entités modifiées)

## Repo de référence
- **kitfunso/hippo-memory** (decay & sleep cycle) – https://github.com/kitfunso/hippo-memory
- **garrytan/gbrain** (reconciliation et nightly job)

**Ce qu'on change** :
- Approche **push** depuis le Raw du jour uniquement (au lieu de tout scanner à chaque fois)
- Decay temporel appliqué en temps réel (background) + recalcul massif nightly
- File de validation humaine pour les cas ambigus (score 0.6-0.85)
- Régénération incrémentale des Compiled Truth uniquement sur les entités impactées

---

**Points clés à retenir :**
- Le Raw Layer reste **immuable** : le job ne fait que lire et dériver.
- 95 % du travail se fait en arrière-plan → l'utilisateur ne voit jamais de latence.
- Même si le job plante, on peut tout rejouer depuis le Raw.
- Le decay et le renforcement se produisent ici (voir 07).
