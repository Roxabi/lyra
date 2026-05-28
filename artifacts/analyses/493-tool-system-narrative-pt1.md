# L'atelier Lyra — voir et saisir l'architecture des outils

**Partie 1 — Sections 1 à 4 : entrer dans l'atelier**

---

## 1. Pourquoi on est là

Avant de pousser la porte, prenons une seconde pour regarder l'état actuel. Depuis plusieurs mois, on parle d'outils. On en a construit, démonté, redessiné. À chaque conversation, le mot *tool* a glissé d'une réalité à l'autre : tantôt une fonction Python qu'un agent invoque directement, tantôt un service NATS qui tourne en arrière-plan sur une autre machine, tantôt un sidecar Quadlet qui attend ses requêtes, tantôt une compétence que le LLM apprend à orchestrer sans même savoir qu'il le fait. Quatre espaces de design vivent côte à côte sans vraiment se parler. L'épic #493 trace une route, le worker fleet #1044 en trace une autre, le harness en prépare une troisième dans son coin, et nos satellites — l'image, la voix, le LLM, Postiz, XCLI — avancent chacun à leur rythme avec leur propre vocabulaire.

C'est comme un atelier où quatre artisans travaillent dans la même pièce, chacun à son établi, chacun avec son patois pour désigner les mêmes objets. Tant que chacun reste dans son coin, ça tient. Mais le jour où l'un d'eux demande un marteau à un autre, on s'aperçoit qu'ils ne pointent pas vers la même chose.

L'idée de ce qui suit, c'est de poser le plan de l'atelier. Pas un nouveau dessin — juste la mise au propre de ce qui existe déjà, recadré pour qu'on parle d'une seule voix. On va monter sur la mezzanine, embrasser l'ensemble, redescendre objet par objet, ouvrir les portes, suivre les flux, regarder les serrures. À la fin, tu devrais pouvoir prendre n'importe quelle pièce et savoir précisément où elle se range, ce qu'elle touche, et à quoi elle sert.

Le ton restera léger, mais ce qu'on regarde est sérieux. C'est la fondation sur laquelle tout le reste va s'empiler. Si elle n'est pas droite, tout penche.

---

## 2. Le panorama

Monte avec moi sur la mezzanine, deux mètres au-dessus du sol. D'ici, l'atelier se lit en cinq strates empilées. Pas cinq pièces à côté — cinq strates posées les unes sur les autres, comme on lirait la coupe géologique d'un terrain. Chaque strate repose sur celle qui la précède, chaque strate sert celle qui lui succède.

Tout en bas, la strate de l'identité. C'est là qu'on dépose les marques — qui est cet agent, à quel groupe il appartient, quels outils il a le droit de toucher. Cette strate est physique : elle se matérialise en fichiers de configuration sur le disque, en clés d'authentification qu'on monte comme on glisse une clé dans un coffre, en volumes nommés qui découpent le territoire. Sans cette strate, rien n'a d'identité ; tout flotte.

Juste au-dessus, la strate du harness. Le harness, c'est l'enveloppe vivante qui contient la boucle de l'agent. Il prend le prompt, le passe au LLM, capte les demandes d'outils, les fait exécuter, renvoie les résultats au LLM, recommence — jusqu'à ce que l'agent dise *j'ai fini*. Le harness ne tient en main qu'une chose à la fois : la mémoire du tour en cours. Tout le reste — l'historique long, les données partagées, les gros fichiers — vit ailleurs.

Plus haut, la strate des outils. C'est le rayon où l'agent vient piocher ce dont il a besoin. Quatre catégories d'outils se côtoient sur ce rayon : ceux qui tiennent directement dans la main du harness, ceux qu'il appelle à distance par messages, ceux qui enchaînent plusieurs outils en une séquence déterministe, et ceux qui sont en réalité d'autres harness imbriqués. On y reviendra en détail un peu plus loin, c'est la section la plus dense.

Encore au-dessus, la strate des contrats partagés. C'est le langage commun. Chaque pièce de l'atelier importe ces contrats : la définition de ce qu'est un *tool*, la forme d'un *résultat*, la structure d'un *manifeste*. Sans cette strate, chaque artisan retombe dans son patois — et c'est exactement ce qu'on est en train de corriger.

Et tout en haut, le LLM. Il regarde l'ensemble par-dessus l'épaule. Il ne voit que la surface : la liste d'outils qu'on lui tend, leur description, leurs paramètres. Il ne sait pas, et il n'a pas besoin de savoir, qu'un outil tient dans la paume du harness pendant que l'autre est à dix mètres au bout d'un câble. Pour lui, c'est la même poignée.

Voilà le panorama. Cinq strates, lisibles de bas en haut. Chacune fait une chose, ne déborde pas, et repose proprement sur la suivante. Garde cette image en tête : on va y revenir à chaque étape.

---

## 3. Le harness

Redescends. Pousse cette porte — celle au milieu, marquée *harness*. C'est là que vit l'agent quand il pense.

À l'intérieur, c'est étonnamment simple. Au centre de la pièce, une boucle. Le harness reçoit un prompt complet, l'envoie au LLM, attend la réponse, regarde ce que le LLM lui demande de faire. Si le LLM réclame un outil, le harness va le chercher dans le rayon, attend le résultat, le redonne au LLM, et la boucle recommence. Elle tourne jusqu'à ce que le LLM décide qu'il n'y a plus rien à faire — ce qu'on appelle un *end_turn* dans son jargon. À ce moment-là, la boucle se referme, le harness rend la main, et le tour se termine.

Pendant que la boucle tourne, le harness ne tient dans la main qu'une seule chose : la mémoire du tour. Quels outils il a déjà appelés, quels résultats il a déjà reçus, à quel stade de la conversation il en est. Cette mémoire est volontairement éphémère. Dès que le tour se termine, elle est jetée. Le harness ne garde rien.

C'est important parce qu'on confond souvent les rôles. Tout ce qui doit survivre au tour vit ailleurs. L'historique long de la conversation est stocké par le hub, qui sait qui a parlé à qui et quand. Les gros artefacts — les images, les fichiers audio, tout ce qui pèse — sont déposés dans le blobstore, et seules leurs références circulent. La connaissance durable d'un agent — ses notes, ses préférences, ses traces — vit dans son propre volume sur disque. Le harness, lui, est volontairement amnésique. C'est ce qui le rend interchangeable.

Et il faut bien comprendre l'autre dimension : il n'y a qu'un seul binaire harness. Un seul. La même image de conteneur, le même code, partout. Ce qui change d'un agent à l'autre, ce n'est jamais le code du harness — c'est sa configuration au moment de l'instanciation. Même corps, identités différentes. C'est le point qui nous emmène directement à la section suivante.

---

## 4. L'agent comme instance

Comment ce harness unique devient-il cinq agents distincts ? C'est là qu'on touche au pattern le plus élégant de toute l'articulation.

Quadlet — l'outil de Podman qui nous sert à orchestrer les conteneurs sous systemd — supporte nativement une notion qu'on appelle le *template d'instance*. Concrètement, on dépose un fichier `.container` avec un arobase dans son nom : `lyra-agent@.container`. Ce n'est pas un conteneur fonctionnel à proprement parler — c'est un moule.

Quand on veut faire vivre un agent, on demande à systemd de démarrer `lyra-agent@telegram.service`. systemd lit le moule, remplace l'arobase par *telegram* partout où il apparaît, et fabrique une instance concrète. Le volume monté n'est plus `~/.roxabi/lyra/agents/`, c'est `~/.roxabi/lyra/agents/telegram/`. Le fichier d'environnement n'est plus `env/.env`, c'est `env/telegram.env`. Le secret NATS n'est plus `lyra-nats-`, c'est `lyra-nats-telegram`. Tout se résout proprement à partir de l'arobase.

Si demain on veut un agent Discord, un agent bot GitHub, un agent assistant personnel, on démarre `lyra-agent@discord.service`, `lyra-agent@gh-bot.service`, `lyra-agent@assistant.service`. Chaque instance utilise la même image, le même binaire harness, mais habite un territoire isolé. Sa propre liste d'outils autorisés. Son propre prompt système. Ses propres secrets. Ses propres données sur le disque.

L'image qu'il faut garder en tête, c'est celle du moule à madeleines. Un seul moule, plusieurs empreintes, chacune unique mais cuite au même four, avec la même pâte. L'isolation est physique : un agent ne peut pas lire les volumes d'un autre, ne peut pas signer avec la clé d'un autre, ne peut pas usurper son identité. Mais on ne paie pas la duplication du binaire. La maintenance d'un harness pour tous, l'identité propre à chacun.

Et tout ce qui décide vraiment de ce qu'un agent peut faire — sa personnalité, sa liste d'outils, son modèle de LLM, son persona, son ton de voix — vit dans le fichier d'environnement, posé sur le disque, versionné dans git, audité par n'importe qui qui ouvre le repo. C'est lisible, c'est traçable, c'est révocable d'un commit. Si demain on veut retirer un outil à un agent, on édite son env file, on relance son service, c'est fait. Pas de migration de base de données, pas de redéploiement du binaire, pas de coordination entre équipes.

C'est cette élégance-là — un moule, des empreintes — qui nous permet de tenir la promesse de la section précédente : un seul harness, N agents, isolation propre. On verra à la section §11 comment les clés et les serrures viennent compléter ce tableau ; pour l'instant, retiens juste qu'on a deux primitives en place : un harness amnésique et un moule à instances. Sur ces deux primitives, on va maintenant pouvoir poser les outils.

---

*Fin de la Partie 1. La Partie 2 ouvrira la porte du rayon des outils : les quatre couches superposées, la distinction worker ≠ tool, et la frontière qui n'existe pas pour le LLM.*
