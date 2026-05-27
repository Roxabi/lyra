# 03 - Compiled Truth (Markdown)

## À quoi ça sert ?
Version **synthétique, propre et humaine** de chaque entité importante du système.

C'est la forme de connaissance la plus dense et la plus utile : elle est injectée en priorité dans les prompts des agents (orchestrateur surtout) pour fournir un contexte de très haute qualité sans bruit.

## Qui l'utilise ?
- **Main Orchestrator** (injection prioritaire dans le system prompt)
- **Humains** (lecture, édition manuelle et compréhension rapide)
- **Job de nuit / Compiled Truth Regeneration** (régénération automatique)
- **Outils de validation** (lecture pour décision humaine)

## Architecture technique détaillée

- **Stockage** : un fichier Markdown par entité importante
  Exemple de chemin : `compiled_truth/projets/lyra.md` ou `compiled_truth/personnes/jean-dupont.md`
- **Structure stricte du fichier** :

```markdown
# Titre de l'entité (ex: Lyra)

**Dernière mise à jour** : 2026-04-29

## Synthèse actuelle
[2 à 4 paragraphes ultra-concis et clairs – le cœur de la connaissance]

## Évolution récente
- Point clé 1 (date ou version)
- Point clé 2

## Sources principales
- Raw entries : [liste des hashes les plus pertinents]
- Relations clés : [liste courte des liens importants]
```

- **Important** : la **timeline complète** n'est **pas stockée** dans ce fichier. Elle est générée à la volée via une requête sur le Raw Layer + Knowledge Graph quand nécessaire.
- Les Compiled Truth sont **versionnés** dans la base PostgreSQL (table `compiled_truth`) pour garder l'historique.

## Repo de référence
**garrytan/gbrain** – https://github.com/garrytan/gbrain

**Ce qu'on change par rapport à gbrain** :
- Suppression complète de la Timeline (générée dynamiquement)
- Régénération **incrémentale** uniquement sur les entités modifiées (via Impact Analysis du job nocturne)
- Version plus courte, plus focalisée et plus humaine que dans gbrain
- Intégration native du decay : seules les entités avec `memory_strength > 0.35` sont injectées automatiquement

---

**Points clés à retenir :**
- Le Compiled Truth est **la forme la plus précieuse** de la mémoire : dense, lisible et directement utilisable par l'orchestrateur.
- Il est toujours dérivé du Raw Layer + Graph → jamais source de vérité.
- Il reste éditable par un humain (le système détecte les modifications manuelles et les intègre).
- Grâce au decay (07), les entités oubliées ne polluent plus les prompts.
