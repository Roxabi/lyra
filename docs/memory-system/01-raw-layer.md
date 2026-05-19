# 01 - Raw Layer (Couche Immuable)

## À quoi ça sert ?
Couche de stockage **immuable** qui conserve **l'intégralité** des données brutes générées par le système (messages utilisateur, réponses agents, artefacts, documents d'architecture, code, logs, etc.).

Elle garantit la **reconstructibilité totale** de n'importe quel état passé du système, même après plusieurs années.

## Qui l'utilise ?
- Job de nuit (Consolidation)
- Système de backup / audit / forensic
- Humains (reconstruction manuelle d'un contexte historique)
- Outils de replay (rejouer une session à une date précise)
- Retain Job (event-triggered)

## Architecture technique détaillée

- **Stockage principal** : système de fichiers append-only (JSONL) + base PostgreSQL pour les métadonnées
- **Structure de dossiers recommandée** :
```bash
raw/
├── conversations/
│   └── YYYY-MM-DD_HH-MM-SS_[session-id].jsonl
├── documents/
│   └── YYYY-MM-DD_[slug].md
├── artefacts/
│   └── architecture-v2.json
└── logs/
    └── agent-events_YYYY-MM-DD.log
```

- **Format de chaque entrée** (obligatoire) :
  - `timestamp_utc`
  - `hash_sha256` (calculé sur l'ensemble du payload)
  - `type` (`conversation`, `document`, `artefact`, `log`, etc.)
  - `session_id`
  - `agent_id`
  - `payload` (JSONB complet)

- **Immuabilité** : une fois écrit, le fichier ne doit **jamais** être modifié. Toute modification ou correction crée une **nouvelle entrée** avec un nouveau hash.

## Repo de référence
**garrytan/gbrain** – https://github.com/garrytan/gbrain

**Ce qu'on change par rapport à gbrain** :
- Suppression totale du mélange Compiled Truth / Raw
- Ajout systématique de `hash_sha256` sur chaque entrée
- Stockage en JSONL pour permettre un streaming rapide lors du job de nuit et du Retain
- Pas de timeline dans cette couche (générée dynamiquement via le Graph)

---

**Points clés à retenir :**
- Le Raw Layer est **la seule source de vérité** du système.
- Tout le reste (Knowledge Graph, Compiled Truth, Decay) est dérivé et peut être reconstruit à partir d'ici.
- Immuabilité stricte = auditabilité et reproductibilité totale.
