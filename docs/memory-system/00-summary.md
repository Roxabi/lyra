# 00 - Summary : Architecture Globale du Système de Mémoire

## Vue d'ensemble

Le **Système de Mémoire** est un cerveau artificiel persistant, immuable et auto-entretenu conçu pour les agents intelligents (orchestrateur principal + sub-agents).

Il repose sur **quatre couches fondamentales** qui travaillent ensemble pour garantir :
- **Immuabilité totale** de l'historique brut (Raw Layer)
- **Compréhension structurée et rapide** (Knowledge Graph + Vector Store)
- **Contexte ultra-dense et humain** (Compiled Truth)
- **Oubli intelligent et maintenance automatique** (Decay + Job Nocturne)

Ce système permet à l'orchestrateur de toujours disposer d'un contexte propre, à jour et hautement pertinent, tout en conservant la possibilité de reconstruire **n'importe quel état passé** à n'importe quel moment.

---

## Les 8 composantes principales

| Fichier | Nom | Rôle principal | Type |
|--------|-----|----------------|------|
| **01-raw-layer.md** | Raw Layer (Couche Immuable) | Stockage append-only de **tout** ce qui se passe | Source de vérité |
| **02-knowledge-graph.md** | Knowledge Graph + Vector Store | Cerveau structuré, recherche rapide, relations + temporalité | Lecture/écriture intelligente |
| **03-compiled-truth.md** | Compiled Truth (Markdown) | Synthèses propres, concises et lisibles par l'humain et les agents | Injection prioritaire dans les prompts |
| **04-consolidation-nightly.md** | Job de Consolidation Nocturne | Transformation automatique du Raw → Graph + Compiled Truth | Cœur du maintien de la mémoire |
| **05-agent-usage.md** | Utilisation par les Agents | Règles précises d'accès à la mémoire (orchestrateur vs sub-agents) | Isolation et performance |
| **06-execution-model.md** | Modèle d'Exécution (Synchrone / Asynchrone) | Flux Input/Output et timing de chaque composant | Architecture d'exécution |
| **07-decay-mechanism.md** | Mécanisme de Decay (Oubli Temporel) | Oubli progressif inspiré Ebbinghaus + renforcement | Maintien de la pertinence à long terme |
| **08-implementation-prompts.md** | Prompts d'Implémentation LLM | Tous les prompts versionnés du pipeline | Cohérence et reproductibilité |

---

## Architecture globale (vue simplifiée)

```
Utilisateur
    ↓ (message ou artefact)
Main Orchestrator (synchrone)
    ↓
Recherche Multi-Stratégie → Compiled Truth + Faits pertinents (avec decay)
    ↓
Génération réponse + écriture dans Raw Layer
    ↓ (asynchrone / event-triggered)
Retain Job → Entity Resolution → Graph Update + Decay
    ↓ (CRON nightly)
Nightly Consolidation Job → Impact Analysis → Compiled Truth Regeneration
```

**Source unique de vérité** : **Raw Layer** (jamais modifiée après écriture).
Tout le reste (Graph, Compiled Truth) est dérivé et peut être reconstruit à tout moment.

---

## Principes fondateurs

1. **Immuabilité** – Rien n'est jamais écrasé dans le Raw.
2. **Decay intelligent** – Le système oublie progressivement ce qui n'est plus utile (comme un vrai cerveau).
3. **Compiled Truth prioritaire** – Les agents reçoivent d'abord des synthèses propres et denses.
4. **Isolation stricte** – Les sub-agents n'ont **aucun** accès à la mémoire globale.
5. **Reproductibilité totale** – Tout est tracé, versionné et rejouable.
6. **Maintenance automatique** – 95 % du travail se fait en arrière-plan (background + nightly).

---

## Qui utilise quoi ?

- **Utilisateur** → voit uniquement la réponse de l'orchestrateur
- **Main Orchestrator** → utilise Compiled Truth + faits récents (recherche synchrone ultra-rapide)
- **Sub-agents** → contexte vierge + goal ciblé uniquement
- **Job Nocturne / Retain** → accèdent au Raw et au Graph
- **Humain** → peut lire/éditer les Compiled Truth et la validation queue

---

## État actuel du projet (avril 2026)

- Documentation complète (00 à 08)
- Schéma PostgreSQL + pgvector défini
- Diagrammes d'architecture et flux validés
- Prompts LLM versionnés et structurés (JSON + Jinja2)
- Mécanisme de decay formalisé
- Prêt pour implémentation

**Prochaines étapes recommandées** (dans l'ordre) :
1. Création du fichier `config/decay.yaml` et `config/llm.yaml`
2. Scripts SQL complets + indexes
3. Prototype Python du Retain Job + Entity Resolution
4. Tests end-to-end sur un golden dataset

---

**Ce document est l'entrée unique du système de mémoire.**
Toutes les autres sections (01 à 08) détaillent chaque brique avec précision.
