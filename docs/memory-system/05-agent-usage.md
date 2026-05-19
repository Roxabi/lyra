# 05 - Utilisation par les Agents

## À quoi ça sert ?
Définir précisément **comment** l'orchestrateur principal et les sub-agents interagissent avec la mémoire du système.
L'objectif est de garder un contexte propre, performant et sans pollution tout en respectant les principes d'isolation et de decay.

## Qui l'utilise ?
- **Main Orchestrator** (Hermes-style) – le seul qui a accès complet à la mémoire
- **Tous les sub-agents** – ils démarrent avec un contexte vierge
- **Job de nuit / Retain** – pour les mises à jour asynchrones

## Architecture technique détaillée

### 5.1 Main Orchestrator
À chaque nouveau message utilisateur :

1. **Recherche multi-stratégie** dans le Knowledge Graph + Vector Store (voir 02)
2. **Récupération** des Compiled Truth des entités les plus pertinentes (seulement celles avec `memory_strength > 0.35`)
3. **Récupération** des faits récents/denses (filtrés par `weight_temporal`)
4. **Injection** dans le system prompt selon ce template exact :

```markdown
<compiled_truth>
... (versions Markdown les plus récentes et pertinentes)
</compiled_truth>

<relevant_facts>
... (faits les plus denses et récents, avec decay appliqué)
</relevant_facts>
```

→ Seule la synthèse propre est injectée. Aucun bruit brut du Raw Layer.

### 5.2 Sub-Agents
- Démarrent avec un **contexte complètement vierge**
- Reçoivent uniquement :
  - Le goal précis donné par le parent
  - Un petit contexte ciblé (max 4 000 tokens)
  - Aucun accès à la mémoire globale ni à `session_search`
- À la fin de leur travail : renvoient **uniquement** un résumé structuré au parent (format JSON défini dans les prompts – voir 08)

### 5.3 Isolation & Performance
- Le Main Orchestrator reste toujours propre
- Le bruit éventuel des sub-agents reste enfermé dans leur propre session (stockée uniquement dans le Raw Layer)
- Limite de profondeur de délégation configurable (ex: max 3 niveaux)
- Le decay (07) est appliqué systématiquement pour éviter l'inflation du contexte

## Repo de référence
**garrytan/gbrain** + usage dans OpenClaw/Hermes

**Ce qu'on change** :
- Isolation encore plus stricte des sub-agents (zéro accès à la mémoire globale)
- Injection prioritaire des **Compiled Truth** plutôt que des faits bruts
- Utilisation systématique du decay pour filtrer automatiquement les faits trop anciens ou peu pertinents
- Tout appel LLM de sub-agent est tracé dans le Raw Layer pour reproductibilité

---

**Points clés à retenir :**
- Seul le Main Orchestrator voit la mémoire complète.
- Les sub-agents sont des « workers » isolés → ils ne polluent jamais le contexte principal.
- Grâce au Compiled Truth + decay, le prompt du Main Orchestrator reste toujours dense et pertinent.
