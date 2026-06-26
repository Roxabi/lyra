## 8. Le plancher invisible : les contrats partagés

Tu te souviens de la frontière qu'on venait de traverser — cette ligne entre les outils internes qui habitent le hub et les satellites qui vivent sur des machines distantes, reliés par le réseau. Le dispatcher uniforme tenait les deux côtés sans les distinguer. Ce qui rend cela possible, ce n'est pas de la magie : c'est un plancher commun, coulé sous les pieds de tout le monde avant même que le premier outil ne soit écrit.

Ce plancher est composé de deux dalles, chacune avec son rôle propre.

La première, on l'appelle `roxabi-contracts`. C'est un module de contrats, un sous-paquetage du workspace qui vit dans `packages/roxabi-contracts/`. Il porte les formes-types que tous les outils importent : la définition d'une requête TTS, la structure d'une réponse d'image, le schéma d'une erreur unifiée, les sujets NATS qui servent de noms officiels sur le réseau. Chaque outil qui veut parler à Lyra puise dans ce même registre, côté hub comme côté satellite. Un champ renommé dans les contrats devient immédiatement une erreur de typage dans les deux camps — le désaccord silencieux entre un éditeur et un lecteur, ce piège classique, est transformé en erreur visible avant même que le code ne tourne. Les contrats portent aussi les indices de comportement : cet outil est en lecture seule, celui-ci est idempotent, cet autre est destructeur. Le hub les lit pour décider comment se comporter face à une panne.

La seconde dalle, c'est `roxabi-nats`, l'autre sous-paquetage, dans `packages/roxabi-nats/`. Là réside la plomberie : l'adaptateur qui branche un outil sur le réseau NATS, le middleware qui propage l'identité de l'agent tout le long du chemin, le client de registre qui tient à jour la liste des workers vivants grâce à des battements de cœur réguliers. C'est le transport pur, sans aucune connaissance des sujets Lyra ou des domaines métier. Il ne sait pas ce qu'il transporte. Il sait seulement comment le transporter en sécurité.

Deux briques distinctes pour deux raisons distinctes. Les contrats évoluent quand un domaine change de forme — une nouvelle requête, un nouveau champ. Le transport évolue quand la mécanique réseau change. Les mêler dans un seul paquetage condamnerait les satellites à prendre des mises à jour de plomberie chaque fois qu'un contrat de voix se précise, et vice-versa.

Sans ce plancher, chaque satellite réécrit les mêmes vingt lignes. Lyra copie-colle les mêmes schémas Pydantic, voiceCLI les redéfinit de son côté, imageCLI fait de même — et six mois plus tard, cinq versions dérivent en silence. C'est ce qu'on appelle le drift N×M : le nombre de paires hub-satellite multiplie la surface de désaccord potentiel. La revue d'axe a validé ce risque explicitement. La réponse, c'est cette source unique, importée par tous.

## 9. Les cours d'eau : comment la donnée circule

Maintenant que le plancher tient, regarde comment la donnée se déplace dans cet atelier. Ce n'est pas un flot unique — c'est plusieurs cours d'eau distincts, chacun taillé pour ce qu'il transporte.

Le territoire sur disque est organisé en trois niveaux d'isolation. Il y a d'abord ce qui est partagé en lecture seule entre plusieurs outils : les poids de modèles HuggingFace, par exemple, qui coûtent cher à télécharger et n'ont aucune raison d'exister en double sur la même machine. Un seul exemplaire, plusieurs consommateurs. Ensuite vient ce qui appartient à un outil précis, posé dans `~/.roxabi/<outil>/` — son espace propre, son bureau, ses données de travail. À l'intérieur de cet espace, un sous-dossier `agents/<id>/` isole la mémoire de chaque agent individuellement. Trois niveaux d'emboîtement : le commun, le propre à l'outil, le propre à l'agent. Chacun a sa raison d'être, chacun a sa frontière.

Pour les artefacts lourds — les fichiers audio générés par la synthèse vocale, les images produites par imageCLI, les pièces jointes entrantes des plateformes de messagerie — il y a le blobstore. C'est un service HTTP dédié, `lyra-blobstore`, qui tourne sur la machine hub dans son propre conteneur Quadlet et écoute sur le port 8449. Pense à lui comme au grenier de l'atelier : on y dépose les choses volumineuses, et on en revient avec une étiquette. Cette étiquette, c'est une référence — une URL, un identifiant de contenu — qui circule dans les messages NATS à la place des octets bruts. Jamais les octets bruts ne traversent le réseau de messages. Le message NATS dit : « le fichier audio est là-bas » ; il ne le porte pas sur lui. Ce principe tient pour voiceCLI, pour imageCLI, pour tout outil qui produit ou consomme des artefacts de poids.

La mémoire durable de session, elle, vit ailleurs encore. Elle repose dans un magasin clé-valeur hébergé sur JetStream — le bus de messages persistant qui sous-tend NATS. C'est le coffre : ce qui y est déposé survit à un redémarrage du hub, à une reconnexion d'adaptateur, à une coupure réseau passagère. La conversation ne tombe pas si le processus tombe.

Le harness, lui, ne tient qu'une seule chose : la mémoire du tour en cours. Ce que l'agent est en train de faire, le contexte de cette unique interaction. Une fois le tour terminé, cette mémoire est jetée. Éphémère par conception, pour ne pas accumuler ce qui n'a plus à être tenu.

La règle d'or qui gouverne ces cours d'eau est simple une fois qu'on la voit : NATS pour les petits messages structurés qui doivent traverser vite, le blobstore pour ce qui pèse et ne mérite pas d'encombrer le réseau de messages, le magasin clé-valeur pour ce qui doit survivre entre les sessions. Trois cours d'eau, trois natures de données, trois destinations. Mélanger les trois produirait un seul fleuve boueux où rien ne circulerait bien.

---

*La prochaine étape nous emmène sous le sol — dans les tuyaux eux-mêmes : le pouls des événements NATS qui fait battre l'ensemble, et les serrures qui décident qui a le droit de passer.*
