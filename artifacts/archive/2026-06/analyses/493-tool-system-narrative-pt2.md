## 5. Les outils : quatre couches superposées

Sur ces deux primitives — le harness qui boucle et le bus qui relie — on va maintenant pouvoir poser les outils. Et c'est là que l'architecture devient vraiment intéressante, parce qu'elle est à la fois simple en surface et stratifiée en profondeur.

Approche-toi un instant du rayon. Ce que tu vois, c'est une rangée d'objets qui se ressemblent. Chacun a un nom, une description, un contour de paramètres. Pour l'agent qui tend la main, ils sont interchangeables dans le geste : il saisit le nom, il passe les arguments, il attend le résultat. Mais si tu retournes l'un d'eux et que tu regardes en dessous, tu découvres que le transport varie complètement d'un outil à l'autre. C'est ce que j'appelle la surface uniforme sur un fond hétérogène. Et c'est le cœur de ce que tu dois comprendre dans cette section.

### L0a — ce qui tient dans la paume

La première couche, L0a, c'est le fond du rayon. Ce sont les outils built-in, compilés directement dans le binaire du harness. Quand le LLM décide d'exécuter `bash`, ou de lire un fichier, ou d'appeler `web_fetch`, ou de publier sur un sujet NATS, ou d'invoquer l'outil `gh` — il n'y a aucune découverte, aucun appel réseau, aucun IPC. Le code s'exécute dans le même process, à côté du dispatcher, en mémoire partagée. La latence est proche de zéro. Tu tiens l'outil dans la paume, littéralement.

Ce groupe est volontairement petit. `bash`, `file_read`, `file_write`, `web_fetch`, `nats_publish` et `gh` : voilà ce qui appartient à L0a. Six outils, six primitives. Chacun fait une seule chose et la fait vite. Leur force vient de leur légèreté — ils ne nécessitent aucun démon, aucun Quadlet, aucun satellite. Ils existent tant que le harness existe.

### L0b — ce qui est à portée de fil

Un pas plus loin sur l'étagère, tu trouves L0b. À l'œil, ces outils sont identiques aux précédents : même nom, même description, même forme de paramètres. La main de l'agent les saisit exactement pareil. Mais la différence est sous la surface, dans le transport.

L0b regroupe les outils distants — la voix, avec ses deux faces `lyra.voice.tts.request` et `lyra.voice.stt.request`, la génération d'image, le LLM, Postiz, XCLI. Chacun de ces outils vit dans son propre processus, souvent dans son propre Quadlet sur une machine séparée. Pour les atteindre, le harness envoie un message sur un sujet NATS. Il attend la réponse. Le résultat voyage par le réseau avant d'atterrir.

Et voilà le point que je veux marteler, parce que c'est là que l'élégance de la conception se révèle : le LLM ne voit pas la différence entre L0a et L0b. Jamais. Pour lui, `bash` et `lyra.voice.tts.request` ont exactement le même poids, la même texture. Il voit une poignée, une description, des paramètres. Le transport — en process ou par NATS — est une affaire de couche inférieure qui se règle en silence. C'est le harness qui porte cette distinction, pas le modèle.

### L1 — la séquence chorégraphiée

Monte d'un cran. L1, c'est ce qu'on appelle un outil macro. Il est toujours présenté comme un seul outil à l'agent — une poignée, un nom, une description. Mais derrière, il exécute une séquence déterministe d'outils de niveau inférieur, sans jamais repasser par le LLM entre les étapes.

Prends l'exemple concret qui cristallise le mieux cette idée : `vault.add_from_url`. Tu donnes une URL à cet outil, et il fait trois choses dans l'ordre. Il va chercher la page — c'est un `scrape`, un appel à un outil L0b. Il la résume — `llm.summarize`, encore un outil L0b. Il range le résultat dans le vault — `vault.add`, L0a. Trois étapes, un seul geste visible depuis l'extérieur. L'agent n'a pas à orchestrer ces étapes lui-même, il n'a pas à formuler trois appels successifs en espérant que chaque tour de boucle aboutisse. Il confie la séquence à l'outil macro, qui la pilote de manière déterministe.

C'est une forme de composition qui se distingue clairement de ce que fait le LLM quand il enchaîne des outils librement. Là, l'enchaînement est codé, testé, reproductible. Le LLM délègue la chorégraphie à quelque chose de plus fiable que lui-même pour cette tâche précise.

### L2 — la partition glissée dans le prompt

L2 est d'une nature différente. Ce n'est plus un outil invoqué à l'exécution. C'est une instruction injectée dans le prompt système, avant même que la conversation commence.

Une skill — au format `SKILL.md`, dans la convention Anthropic — est une partition. Elle n'ajoute pas un nouvel outil à la liste ; elle apprend au LLM comment enchaîner les outils L0 et L1 qui existent déjà, dans un domaine particulier. Une skill pour la gestion de vault lui dit comment combiner `web_fetch`, `vault.add_from_url` et une série de vérifications sans que ces règles aient besoin d'être énoncées à chaque tour. La logique est posée une fois, à l'amont, dans le texte du prompt.

Pour l'agent, L2 est invisible comme couche distincte. Il ne sait pas qu'une skill a été injectée. Il pense simplement mieux dans ce domaine. C'est la différence entre donner un outil à quelqu'un et lui enseigner un métier.

### L3 — un harness qui appelle un autre harness

Tout en haut de l'étagère, là où les objets pèsent le plus lourd, se trouve L3. Le sous-agent.

Là encore, l'agent parent voit une poignée, une description, des paramètres. Mais quand il saisit cet outil, ce qu'il déclenche, c'est un deuxième harness complet — avec sa propre boucle de tour, son propre contexte, son propre jeu d'outils. Ce harness fils travaille dans l'isolement, accumule ses propres traces, rend un résultat et s'arrête. Le parent ne voit que le résumé final. Il ne perçoit ni la profondeur de la récursion, ni le nombre de tours que le fils a nécessités, ni les outils que le fils a appelés en chemin.

C'est la couche la plus puissante et la plus coûteuse. Elle est réservée aux tâches qui méritent vraiment leur propre espace de travail — des investigations longues, des refontes de code sur plusieurs fichiers, des analyses qui mobilisent elles-mêmes une palette d'outils.

### La surface, toujours la même

Si tu fais un pas en arrière et que tu regardes ce rayon dans son ensemble, quelque chose frappe. Ces quatre couches — l'outil in-process, l'outil distant, la séquence macro, la skill injectée, le sous-agent récursif — ont toutes exactement le même contour visible pour le LLM : un nom, une description en prose, un schéma JSON de paramètres. Le modèle n'a pas de capteur pour distinguer ce qui va s'exécuter en mémoire de ce qui va traverser le réseau. Il n'a pas de capteur pour savoir si derrière la poignée il y a une ligne de code ou un autre harness complet.

C'est délibéré. L'abstraction est totale côté modèle, et la complexité est entièrement portée par le dispatcher — cet aiguilleur silencieux qui lit le type de l'outil, vérifie les droits, valide le schéma, et décide du transport sans consulter le LLM.

Tenir ça en tête change la façon dont on pense l'ajout d'un nouvel outil. La question n'est pas « comment est-ce que je l'expose au modèle ? » — ça, c'est toujours pareil. La question est « à quelle couche appartient-il, et quel transport lui convient ? ».

*Dans la prochaine partie, on va franchir une distinction que la taxonomie effleure sans la nommer explicitement : la différence entre un worker et un outil — et pourquoi cette ligne, quand on la voit clairement, change la façon dont on déploie chaque satellite de l'écosystème.*
