# Qaya — Architecture Analysis

> **Source**: LinkedIn posts (série Qaya³, auteur inconnu)
> **Date**: 2026-03-10
> **Category**: Event-driven persistent cognitive architecture
> **Family**: ClawFamily (architectures agentiques de référence)
> **License**: N/A (concept publié, pas open-source)

---

## TL;DR

Qaya est une architecture IA **persistante et event-driven** centrée sur un monde interne partagé (WoE — World Object Embedding). Là où les LLMs classiques vivent requête-par-requête, Qaya maintient un état cognitif continu : elle n'attend pas les événements, elle les **anticipe**.

**Tagline**: *"Pas un modèle qui répond. Un monde interne qui évolue."*

---

## 1. Vue d'ensemble

### Le problème résolu

Les LLMs sont stateless par nature : contexte in, réponse out, oubli. Pour un système persistant (agent qui tourne H24, gère plusieurs canaux, accumule du contexte), cette architecture atteint rapidement ses limites. Qaya propose une alternative : un espace du monde partagé qui évolue dans le temps, autour duquel gravitent des modules spécialisés.

### Ce qui est fondamentalement différent

| Paradigme classique | Qaya |
|---------------------|------|
| Stateless (requête/réponse) | Stateful (monde interne persistant) |
| Pipeline linéaire | Boucle circulaire autour d'un WoE |
| Réactif aux événements | Anticipatif (événements latents) |
| Contexte figé | Fenêtres de contexte vivantes (RAM → archive) |
| Attention uniforme | MoE événementiel (7 experts par type de motif) |

---

## 2. Architecture

### WoE — World Object Embedding

Centre du système. Contient :
- **Objets** : entités du monde (utilisateurs, ressources, systèmes)
- **Concepts** : abstractions et relations sémantiques
- **Dynamiques** : évolution dans le temps

Persiste en RAM pour les fenêtres actives, archivé sinon. Réactivable si continuité probable détectée.

### Les 4 modules (Liquid Neural Networks)

```
          ┌──────── WoE ────────┐
          │  (monde interne)    │
          │                     │
    ED ───┤ fenêtres ctx        ├─── ECAD
(events)  │                     │   (actions)
          │                     │
   OAD ───┤ saillance objets    ├─── LOTAD
(attn)    │                     │  (sémantique)
          └─────────────────────┘
```

**ED-LNN** — Event Dynamics
Observe le flux brut d'événements. Décide quand ouvrir/fermer/fusionner des fenêtres de contexte. Anticipe les événements probables.

**OAD-LNN** — Object Attention Dynamics
Ne crée pas les objets du WoE. Détermine lesquels deviennent saillants dans un contexte donné.

**LOTAD-LNN** — Language-Object-Task Attention Dynamics
Relie langage, concepts et objets du WoE. Crée les concepts manquants à la demande.

**ECAD-LNN** — Event-Causal Action Dynamics
Raisonne sur la causalité. Déclenche les actions cognitives ou motrices.

### ED-LNN — Mixture of Experts (7 experts)

| Expert | Rôle |
|--------|------|
| **Rythmes normaux** | Modélise les fréquences attendues (distributions de Poisson) — filtre le bruit physiologique |
| **Rafales** | Détecte les bursts soudains (explosion d'erreurs, messages, requêtes) |
| **Anomalies rares** | Surveille les événements quasi-inexistants → attention maximale immédiate |
| **Corrélations séquentielles** | Reconnaît les patterns A→B→C annonçant un comportement |
| **Événements manquants** | Détecte les silences : heartbeat absent, réponse oracle tardive |
| **Anticipation** | Crée des événements latents quand ECAD déclenche une action (attend le retour) |
| **Compression** | Regroupe N événements similaires en une seule fenêtre de contexte |

Le **routeur MoE** choisit dynamiquement quels experts analysent chaque événement entrant.

---

## 3. Propriétés clés

### Anticipation vs Réaction

Quand ECAD déclenche une action externe (génération d'image, appel API), ED crée un **événement latent** : le système s'attend à un retour, à un délai, ou à une erreur selon l'historique. C'est de la continuité cognitive, pas juste de la réactivité.

### Fenêtres de contexte vivantes

- **Actives** : en RAM, attention haute
- **Compactées** : événements répétitifs mergés (expert compression d'ED)
- **Archivées** : persistées sur disque si la fenêtre se ferme
- **Réactivées** : si continuité probable détectée plus tard

### Liquid Neural Networks

Les LNNs sont des réseaux dont les équations différentielles s'adaptent en continu au flux de données temporelles — particulièrement adaptés aux séquences d'événements irréguliers.

---

## 4. Forces & Faiblesses

### Forces
- **Persistance cognitive réelle** : l'état survit entre les interactions
- **Anticipation** : le système modélise ce qui *devrait* arriver, pas seulement ce qui arrive
- **Silence comme signal** : les événements manquants sont traités comme des données
- **Compression intelligente** : pas de saturation de l'attention sur les événements répétitifs
- **Architecture circulaire** : pas de goulot d'étranglement linéaire

### Faiblesses / Inconnues
- Aucune implémentation open-source connue
- Complexité d'implémentation des LNNs élevée
- Coût computationnel du routeur MoE sur chaque événement
- WoE : pas de détail sur la représentation concrète (vecteurs ? graphe ? SQLite ?)

---

## 5. Applicabilité à Lyra

Voir section dédiée dans ce fichier.

→ **Concepts directement transposables** : expert "événements manquants", expert "compression", anticipation d'actions, fenêtres de contexte archivables/réactivables.

→ **Concepts inspirants mais complexes** : MoE événementiel complet, LNNs (remplaçables par une logique de scoring heuristique dans un premier temps).

→ **Concepts à éviter** : WoE tel quel (trop abstrait, pas de spec concrète disponible).
