# Plan narratif — L'atelier Lyra : architecture des outils

> Plan de lecture du texte narratif accompagnant `493-tool-system-articulation-analysis.mdx`.
> Cible : un texte d'environ 3500 mots lu à voix haute en 15-20 min.
> Tonalité : mentor-architecte qui fait visiter l'atelier, café à la main.
> Champs lexicaux dominants : **visuel** (panorama, contour, strate, lumière) + **kinesthésique** (main, saisir, peser, pouls, geste).
> Date : 2026-05-27

## Titre proposé

**« L'atelier Lyra — voir et saisir l'architecture des outils »**

Alternatives : *Le hangar et la main* / *Sous les outils de Lyra : promenade architecturale*

## Mouvement narratif

Du panorama (vue d'avion) → descente dans l'atelier (objet par objet) → mise en circulation (le flux qui passe) → cadenas et clés (sécurité) → un message qui traverse tout l'ensemble, du clavier à la réponse.

## Audience & format

| | |
|---|---|
| Lecteur | Toi, lu à voix haute / transmis à quelqu'un qui découvre |
| Durée | 15-20 min |
| Forme | Prose légère, aucune liste à puces dans le texte, aucun tableau, métaphores filées |
| Posture | Calme, profond, friendly — pas un manuel, pas un cours |

## Notes de style

| Aspect | Choix |
|---|---|
| Langue | Français |
| Personne | « Tu » singulier — on est ensemble dans l'atelier |
| Phrases | Courtes à moyennes, peu de subordonnées en cascade |
| Métaphores filées | Atelier / strate / pouls / clés — ne pas mélanger dix métaphores |
| À éviter | Acronymes sans explication, listes nues, jargon corporate |
| À garder | Quelques termes techniques (NATS, Quadlet, JSON Schema) — nommés une fois, expliqués, puis utilisés |

---

## §1 — Pourquoi on est là (prologue)

**Rôle narratif** : poser la table, lever le brouillard, rendre désirable la suite.

**Messages clés** :
- Aujourd'hui quatre espaces de design vivent côte à côte sans vraiment se parler (#493 tools, #1044 workers, harness drafté, satellites).
- Le mot « tool » recouvre quatre réalités très différentes qu'on confond depuis le début.
- L'enjeu : un seul langage, du built-in au sub-agent, sans drift.
- Le texte n'invente rien — il met d'aplomb ce qui existe déjà.

**Ancres lexicales** : pièces éparses, brouillard, le tableau qui se redresse, mettre d'aplomb, l'atelier ouvert, l'établi, le vocabulaire commun.

## §2 — Le panorama (vue d'ensemble en 5 strates)

**Rôle narratif** : établir LA vue d'avion, une seule image qui tient.

**Messages clés** :
- Cinq strates empilées comme la coupe d'un terrain — on les lit de bas en haut.
- Identité (Quadlet `@`) → Harness → Tools (quatre couches) → Contrats partagés → LLM.
- Chaque strate fait une chose, ne déborde pas, repose proprement sur la précédente.
- Le LLM regarde par-dessus l'épaule et ne voit que la surface — pour lui, tout outil est une poignée identique.

**Ancres lexicales** : strate, empilement, contour, l'œil qui balaie, profondeur, sédimentation, mezzanine, coupe.

## §3 — Le harness (la coquille qui contient l'agent)

**Rôle narratif** : faire toucher ce qu'est le harness, ce qu'il porte et surtout ce qu'il ne porte PAS.

**Messages clés** :
- Le harness est la boucle agentique en vie : LLM → tool → résultat → re-LLM jusqu'à `end_turn`.
- Il tient dans la main une mémoire de turn, jetée après usage.
- Pas la mémoire de session (qui appartient au hub), pas les gros artefacts (blobstore), pas le savoir durable (volumes).
- Un seul binaire identique partout, instancié N fois — c'est ce qui le rend interchangeable.

**Ancres lexicales** : la coquille, l'enveloppe, ce que la main tient, le souffle, ouvrir-fermer, amnésique, intérieur.

## §4 — L'agent comme instance (pattern Quadlet `@`)

**Rôle narratif** : montrer comment un même harness devient cinq agents distincts.

**Messages clés** :
- Un seul moule (`lyra-agent@.container`), N empreintes (`@telegram`, `@discord`, `@gh-bot`…).
- systemd substitue l'arobase, et tout se résout à partir de l'instance : volume, env file, secret NATS.
- Chaque instance a son territoire propre — sans dupliquer une ligne de binaire.
- L'identité (prompt système, liste de tools, persona, modèle) vit dans le fichier d'environnement — auditée via git, révocable, lisible.
- Image du moule à madeleines : même four, mêmes proportions, empreintes différentes.

**Ancres lexicales** : le moule, l'empreinte, le clone teinté, le sceau, le pochoir, le territoire, le four, la cuisson.

## §5 — Les outils : quatre couches superposées (cœur de la promenade)

**Rôle narratif** : décomposer L0a / L0b / L1 / L2 / L3 avec des images concrètes — c'est la section la plus longue.

**Messages clés** :
- **L0a built-in** : ce qui tient dans la main de l'agent — `bash`, `file_*`, `gh`, `web_fetch`, `nats_publish/request`. Instantané, sans IPC.
- **L0b remote** : ce qui est à portée de fil — voix, image, LLM, Postiz, XCLI. NATS porte le geste.
- **L1 macro** : la séquence chorégraphiée — `scrape → résumé → vault.add`. Déterministe, sans LLM intermédiaire.
- **L2 skill** : la partition glissée dans le prompt système — l'agent apprend à enchaîner les outils de plus bas niveau.
- **L3 sub-agent** : un harness qui appelle un autre harness, contexte isolé, retourne un résumé vers le parent.
- Pour le LLM, les cinq couches sont identiques — le transport varie en silence sous la surface.

**Ancres lexicales** : ce qu'on saisit, ce qu'on tend, l'enchaînement, la partition, l'écho, la chorégraphie, la poignée commune.

## §6 — La distinction worker ≠ tool (lever la confusion)

**Rôle narratif** : démêler ce qu'on a longtemps confondu — section courte mais essentielle.

**Messages clés** :
- Un worker n'est PAS un tool. Un worker est une main qui tend des tools.
- `imageCLI` = un worker qui expose plusieurs tools (`image.generate`, `image.list_engines`, `image.cancel`).
- Le LLM ne voit jamais le worker — il voit le tool ; le worker reste en coulisses.
- Cette distinction porte des conséquences concrètes pour le déploiement (un .container par worker, pas par tool).

**Ancres lexicales** : la main et l'objet, l'artisan derrière l'outil, ce qui est tendu vs ce qui tient, en coulisses.

## §7 — Interne vs externe (la frontière qui n'existe pas pour le LLM)

**Rôle narratif** : déconstruire une fausse distinction du côté agent, vraie distinction du côté infra.

**Messages clés** :
- Built-in = dans le binaire harness, sous la paume, instantané.
- Remote = à l'autre bout d'un sujet NATS, mais le geste pour invoquer est identique côté agent.
- Dispatcher uniforme (Claude Code, smolagents) — même ACL, même `ToolResult`, transport différent en sous-main.
- Rejet du modèle branché (Codex CLI) — deux dispatchers, deux pipelines de sécurité, deux fois plus de surface d'erreur.

**Ancres lexicales** : la frontière floue, le geste identique, derrière le mur, la même poignée, ce qui se cache, ce qui se voit.

## §8 — Les contrats partagés (le plancher invisible)

**Rôle narratif** : présenter `roxabi-contracts/tools/` + `roxabi-tools-sdk` comme infra commune.

**Messages clés** :
- `roxabi-contracts/tools/` = les formes-types : `InProcessTool`, `RemoteTool`, `ToolResult`, `ToolManifest`, `ToolHints`.
- `roxabi-tools-sdk` = la plomberie : adaptateur NATS Micro, middleware identité, registry client en heartbeat-TTL.
- Une seule vérité, importée par tous les satellites — fin du drift N×M (axial-validé).
- Sans ce plancher, chaque tool ré-écrit les mêmes vingt lignes — et cinq versions divergent en six mois.

**Ancres lexicales** : le plancher, la fondation, le câblage caché, les conduits, ce qui porte le poids, la chape.

## §9 — La circulation des données (volumes, blobstore, KV)

**Rôle narratif** : suivre l'eau dans les tuyaux — qui boit où, qui partage quoi, qui garde quoi pour soi.

**Messages clés** :
- Trois niveaux de volume : `shared_ro` (HuggingFace, mutualisé multi-tools), `tool_data` (`~/.roxabi/<tool>/`), `per_agent` (`agents/<id>/` en subdir, isolé).
- Blobstore HTTP (`lyra-blobstore`, ADR-067/068) : les gros artefacts (images, audio) circulent par référence URL, jamais en bytes dans NATS.
- JetStream KV (#640) : canonical store, la mémoire durable de session.
- Harness : juste la mémoire de turn, éphémère, jetée après usage.
- La règle d'or : NATS pour les petits messages structurés, blobstore pour ce qui pèse, KV pour ce qui doit survivre.

**Ancres lexicales** : cours d'eau, tuyaux, réservoirs, ce qui coule vs ce qui dort, la nappe phréatique, la citerne, le débit.

## §10 — Les événements et le pouls (NATS subjects)

**Rôle narratif** : faire entendre le rythme du système — visuel et kinesthésique malgré le sujet sonore.

**Messages clés** :
- Chaque tool publie son heartbeat (`lyra.<domain>.heartbeat`) — le pouls qu'on prend.
- Requêtes : `lyra.<domain>.<action>.request` → réponses sur inbox NATS (request-reply).
- Harness : `lyra.harness.turn.request`, écouté par le hub.
- `$SRV.INFO` répond à la demande quand un agent veut découvrir un tool.
- Tout circule par sujets ; HTTP uniquement pour le blobstore.

**Ancres lexicales** : pouls, battement, rythme, écho, l'onde qui traverse, la pulsation régulière, le sang qui passe.

## §11 — Les serrures et les clés (identité, auth, ACL)

**Rôle narratif** : montrer comment chaque agent a son trousseau, sans le surcharger.

**Messages clés** :
- Un NATS account par rôle (`lyra-hub`, `voice-worker`, `image-worker`…) — granularité réaliste à ≤10 agents.
- Une NKey credential par composant — secret Podman monté en `type=mount`, jamais en variable d'env.
- L'identité de l'agent appelant voyage dans le payload JSON (champ `agent_id`) — pas en header NATS (zéro changement transport).
- ACL appliquée au moment de l'instanciation — deny-first, hérité de Claude Code.
- Un seul site d'enforcement dans le SDK — pas de réimplémentation par tool (axial-mandé).

**Ancres lexicales** : la clé, la serrure, le coffre, le portier, le passage, le trousseau, l'empreinte, la marque qu'on signe.

## §12 — Une histoire complète (suivre un message du début à la fin)

**Rôle narratif** : rassembler tout en un récit vivant — c'est ici qu'on récolte le bénéfice des sections précédentes.

**Messages clés** :
- Un utilisateur écrit sur Telegram → `lyra-telegram` capte → publie sur le hub.
- Hub route → `lyra.harness.turn.request` → l'instance `lyra-agent@telegram` capte la requête.
- Harness compose le prompt avec sa liste de tools autorisés (lue depuis son env file) → appelle le LLM via `llmCLI`.
- LLM demande `image.generate` → dispatcher uniforme → SDK → NATS Micro → `imageCLI` worker.
- Image générée → blob_ref via blobstore → réponse renvoyée → harness rejette dans le LLM.
- Tour suivant, LLM produit la réponse texte → harness streame des events → Telegram pousse.
- On voit le rôle de chaque pièce dans le flux — chaque strate joue sa note, à son moment.

**Ancres lexicales** : suivre, traverser, le voyage, le chemin, ce qui se passe quand on appuie sur entrée, le sillage.

## §13 — Ce qui reste à voir (l'horizon)

**Rôle narratif** : conclure honnêtement, sans faux semblant — c'est l'horizon, pas un mur.

**Messages clés** :
- Le harness runtime (LangGraph / custom / Hermes) — POC à faire.
- Layer 2 (Skills SKILL.md format Anthropic) — adoption plus tard.
- Layer 3 (sub-agent via Task tool) — v1 ou follow-up à trancher.
- « Lightpool » — terme mystère à élucider.
- Passage Quadlet pool partagé → instances `@` (POC à valider avant rollout).

**Ancres lexicales** : l'horizon, les portes encore fermées, les marches à venir, la ligne de crête, le seuil.

---

## Glossaire de poche (à utiliser dans le texte sans surcharger)

| Terme | Définition compacte (peut être glissée en incise) |
|---|---|
| **Harness** | La coquille qui fait tourner la boucle de l'agent. |
| **Tool** | Tout ce que le LLM peut invoquer — local ou distant, du même geste. |
| **Worker** | La main qui tend un ou plusieurs tools. Pas un tool lui-même. |
| **Atomique** | Un seul geste, un seul appel, un seul résultat. |
| **Composite** | Une séquence d'atomiques enchaînés sans repasser par le LLM. |
| **Skill** | Une partition de prompt qui apprend au LLM à enchaîner. |
| **Sub-agent** | Un harness imbriqué, contexte isolé, qui rend un résumé. |
| **NATS** | Le réseau de messages qui relie tout l'atelier. |
| **Quadlet** | Le format `.container` qui orchestre les conteneurs Podman via systemd. |
| **Blobstore** | Le grenier où dorment les gros artefacts. |
| **JetStream KV** | Le coffre où vit la mémoire de session. |
