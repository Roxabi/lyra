# 06 - Modèle d'Exécution : Input / Output + Synchrone / Asynchrone

## Vue Globale du Système

**Input principal du système**
→ Message utilisateur (texte) ou artefact (document, code, analyse, etc.)

**Output principal du système**
→ Réponse finale de l'orchestrateur (texte + éventuels artefacts) + mise à jour silencieuse et asynchrone de la mémoire

Le système est conçu pour que **l'utilisateur ne voie jamais** la complexité du pipeline mémoire. L'expérience reste fluide et instantanée tandis que tout le travail de consolidation, decay et régénération se déroule en arrière-plan.

---

## Détail par Composant : Input → Output + Mode d'exécution

| Composant                        | Input                                      | Output                                              | Type d'exécution          | Déclenchement                  | Remarques |
|----------------------------------|--------------------------------------------|-----------------------------------------------------|---------------------------|--------------------------------|-----------|
| **User Message**                 | Message utilisateur                        | Message ajouté dans Raw Layer                       | **Synchrone**             | Immédiat (à chaque envoi)      | Première étape |
| **Main Orchestrator**            | Message utilisateur + contexte mémoire     | Réponse finale + éventuels tool calls               | **Synchrone**             | À chaque message utilisateur   | Cœur de l'expérience utilisateur |
| **Recherche Multi-Stratégie**    | Requête de l'orchestrateur                 | Compiled Truth + faits pertinents (avec decay)      | **Synchrone**             | Appelé par l'orchestrateur     | Doit être < 300 ms |
| **Sub-Agent Delegation**         | Goal + petit contexte ciblé                | Résumé structuré final du sub-agent                 | **Asynchrone**            | Appel de l'orchestrateur       | Parent attend le résultat |
| **Retain Job**                   | Nouvelle entrée Raw                        | Entités candidates + faits extraits                 | **Asynchrone / Background** | **Event-triggered** (écriture Raw) | Déclenché automatiquement |
| **Entity Resolution**            | Entités candidates                         | Décision (merge / nouvelle / ambiguous)             | **Asynchrone / Background** | Après Retain                   | Partie critique |
| **Knowledge Graph Update**       | Faits validés + relations                  | Mise à jour entités + relations + decay             | **Asynchrone / Background** | Après Entity Resolution        | Decay appliqué ici (voir 07) |
| **Compiled Truth Regeneration**  | Entités impactées                          | Nouvelle version Markdown                           | **Asynchrone / Background** | Après Impact Analysis          | Uniquement les entités modifiées |
| **Nightly Consolidation Job**    | Tout le Raw de la journée                  | Mise à jour complète + validation queue             | **CRON**                  | Toutes les nuits (ex: 03:00)   | Job lourd de consolidation |
| **Validation Queue**             | Cas ambigus (score 0.6-0.85)               | Décision humaine → merge ou nouvelle entité         | **Manuel / Asynchrone**   | Humain (quand il veut)         | File d'attente humaine |

---

### Explications des modes d'exécution

- **Synchrone** :
  Se passe **pendant** la conversation utilisateur. L'utilisateur attend la réponse.
  Exemples : Main Orchestrator, Recherche Multi-Stratégie, génération de la réponse finale.

- **Asynchrone / Background** :
  Se passe **en parallèle** sans bloquer l'utilisateur.
  Exemples : Retain Job, Entity Resolution, Graph Update, Compiled Truth Regeneration.

- **Event-triggered** :
  Déclenché automatiquement dès qu'un événement se produit (ici : écriture dans le Raw Layer).
  C'est le cas du **Retain Job**.

- **CRON** :
  Déclenché à heure fixe.
  C'est le **Nightly Consolidation Job** qui applique le decay massif et les consolidations lourdes (voir 07).

---

### Flux typique d'une conversation (chronologique)

1. **Synchrone** : Utilisateur envoie message → Main Orchestrator
2. **Synchrone** : Orchestrateur fait recherche multi-stratégie → injection Compiled Truth + faits (decay appliqué)
3. **Synchrone** : Orchestrateur génère la réponse
4. **Asynchrone** : Message écrit dans Raw Layer
5. **Asynchrone (event-triggered)** : Retain Job se lance automatiquement
6. **Asynchrone** : Entity Resolution → Graph Update + Decay
7. **CRON (nuit)** : Nightly Job fait le nettoyage, decay global et régénérations

---

**Points clés à retenir :**
- L'utilisateur ne voit **que** la partie synchrone (très rapide).
- Tout le travail de mémoire (Retain, Graph, Decay, Compiled Truth) est **invisible** et asynchrone.
- Le Raw Layer reste la seule source de vérité : même si un job background plante, tout est rejouable.
- Le decay temporel (07) est appliqué principalement dans les jobs asynchrones et nightly.
