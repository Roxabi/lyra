<!-- Partie 3 — §6 + §7 — Narratif outil système Lyra -->

## 6. La main et l'objet : worker n'est pas tool

Tu as vu, dans les couches précédentes, comment les outils s'empilent — du built-in au remote, du geste immédiat au voyage sur le réseau. Mais avant d'aller plus loin, il faut saisir une distinction que l'architecture défend avec soin, parce que la confondre coûte cher en déploiement et en clarté mentale : un worker n'est pas un tool.

Pose ta tasse un instant et regarde les mains. Quand un charpentier tend un marteau, tu ne confonds pas la main et le marteau. La main, c'est ce qui tient, ce qui sélectionne, ce qui rend disponible. Le marteau, c'est ce qu'on saisit pour frapper. Un worker, c'est la main. Les tools, ce sont les objets qu'elle tend.

Prends imageCLI comme exemple concret, parce qu'il rend la chose très tangible. imageCLI est un seul worker — une seule unité déployée, un seul processus qui tourne en arrière-boutique. Et pourtant, il expose plusieurs outils distincts : générer une image, lister les moteurs disponibles, annuler une génération en cours. Trois gestes différents, trois objets dans la main. Mais une seule main. Le LLM, quand il décide d'agir, ne voit jamais le worker. Il voit l'outil — il voit l'objet tendu, pas la main qui le tient. Le worker reste en coulisses, invisible au modèle, silencieux derrière le rideau.

Cette distinction a une conséquence très concrète au moment du déploiement, et c'est là qu'elle mord vraiment. On ne déploie pas une unité conteneur par outil — ce serait absurde, comme commander un atelier entier pour chaque clé à molette. On déploie une unité par worker : un fichier `.container` Quadlet, un service systemd, une seule chose à démarrer, surveiller, redémarrer. Un worker, plusieurs outils, une seule unité. La granularité de déploiement est celle du worker, jamais celle de l'outil. Si imageCLI gagne un quatrième outil demain — disons, inspecter les métadonnées d'une génération — rien ne change dans l'infrastructure : la main est déjà là, elle tend simplement un objet de plus.

C'est une règle qui semble évidente une fois énoncée, et pourtant on voit souvent des architectures qui la violent — qui confondent l'objet et la main, qui font proliférer les services au rythme des outils, qui transforment l'atelier en entrepôt de pièces détachées. Ici, on fait le choix inverse. L'arrière-boutique est propre. Le LLM voit une surface nette.

## 7. La frontière invisible : interne et externe

Il reste une tension à résoudre, et elle est moins évidente que la précédente. Certains outils sont built-in — ils vivent dans le binaire du harness, sous la paume, prêts à répondre sans aucun aller-retour réseau. D'autres sont remote — ils attendent à l'autre bout d'un sujet NATS, derrière un worker qui tourne peut-être sur une autre machine. Ces deux catégories semblent mériter deux traitements différents. L'instinct d'ingénieur dit : deux chemins, deux aiguilleurs, une logique par type.

Lyra dit non.

Le dispatcher — l'aiguilleur qui exécute les outils — est uniforme. Quand un outil built-in est appelé, et quand un outil remote est appelé, le geste d'invocation côté agent est rigoureusement identique. Même contrôle d'accès, même format de résultat, même surface visible pour le LLM. Seul le transport change en sous-main : l'un s'exécute en mémoire, l'autre part sur le réseau. Mais cette différence est un détail de plomberie, pas une distinction de surface. L'agent ne sait pas, et n'a pas besoin de savoir, si l'outil qu'il vient d'invoquer était sous sa paume ou à l'autre bout du fil.

Cette approche s'inspire de deux sources : Claude Code, qui normalise les outils derrière une interface stable quelle que soit leur origine, et smolagents, qui pousse la même idée — un registre d'outils, un seul chemin d'exécution. Le point commun entre ces deux référentiels, c'est qu'ils ont tous les deux reconnu que la frontière interne/externe est réelle pour l'infrastructure — déploiement, latence, observabilité — mais qu'elle ne doit pas remonter jusqu'à la surface cognitive du modèle.

L'alternative rejetée, c'est ce que fait Codex CLI : built-in et externe passent par deux chemins différents, deux aiguilleurs, deux pipelines. Ça paraît logique au premier regard — chaque chose à sa place. Mais ça signifie concrètement deux pipelines de sécurité, deux points d'entrée pour des règles d'accès potentiellement divergentes, deux fois plus de surface d'erreur à maintenir. Quand une règle change, il faut la changer aux deux endroits. Quand un audit passe, il doit couvrir les deux chemins. Le coût s'accumule en silence, et on ne le voit vraiment qu'au moment où les deux pipelines commencent à dériver l'un par rapport à l'autre.

L'aiguilleur uniforme coupe ce problème à la racine. La frontière interne/externe existe — elle a de vraies conséquences opérationnelles sur la latence, sur le déploiement des workers, sur la topologie réseau. Mais elle est encapsulée là où elle appartient : dans le transport, pas dans le dispatcher. L'atelier a une organisation interne que l'architecte connaît bien. Le client qui entre et saisit un outil n'a pas besoin de savoir si cet outil vient de l'étagère du fond ou de la pièce d'à côté.

*Dans la partie suivante, on va descendre encore d'un cran : une fois que l'outil a été invoqué et qu'il répond, qu'est-ce qui circule exactement ? Quels sont les contrats partagés entre workers et dispatcher, et comment les données voyagent-elles depuis le résultat d'un outil jusqu'à l'assistant message que le modèle reçoit ?*
