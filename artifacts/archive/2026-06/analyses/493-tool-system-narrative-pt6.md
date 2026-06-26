## 12. Suivre un message, du clavier à la réponse

On a passé les sections précédentes à examiner les serrures et les clés — qui peut entrer, qui ne peut pas, comment les accréditations voyagent depuis un fichier d'environnement jusqu'au moment précis où une porte s'ouvre ou reste close. C'est une bonne façon de finir la visite des mécanismes. Mais un atelier ne se comprend vraiment qu'en regardant travailler la matière. Alors suis un message, du premier geste jusqu'à la réponse.

Tu ouvres Telegram. Tu écris quelque chose. Tu appuies sur entrée.

À l'autre bout du fil, l'adaptateur lyra-telegram est en écoute. Il capte le message — nom d'utilisateur, identifiant de conversation, texte brut — et le reformate en un événement qu'il publie sur le bus NATS. Ce n'est pas encore une décision : c'est une traduction. L'adaptateur ne sait pas ce que le message signifie, il sait seulement le mettre en forme et le pousser vers le hub.

Le hub reçoit l'événement. Il tient le rôle qu'on lui connaît depuis le début : il assemble le contexte, reconstruit la tranche d'historique utile, décide à qui router. Puis il publie une demande de tour sur le sujet `lyra.harness.turn.request` — un ticket qui dit, en substance : quelqu'un a besoin d'un tour d'agent, voici tout ce qu'il lui faut.

L'instance `lyra-agent@telegram` était là, en attente dans la file de queue. Elle capte la requête. C'est l'empreinte du moule harness — la même structure, instanciée pour ce canal, avec sa propre identité. Elle ne choisit pas de répondre ; elle est le travailleur que la queue désigne.

Le harness compose le prompt. Pour cela, il lit la liste d'outils autorisés pour cet agent, inscrite dans son fichier d'environnement. C'est exactement la serrure qu'on a visitée : la liste dit ce que cet agent a le droit de demander, et pas un outil de plus. Le prompt constitué, le harness appelle le LLM via le worker llmCLI qui fait le relais vers le modèle.

Le LLM examine la demande et décide qu'il lui faut générer une image. Il demande l'outil `image.generate`.

La demande sort du LLM et tombe dans le dispatcher. Le dispatcher est uniforme — c'est la même pièce pour tous les outils, celle qu'on a vue en Partie 3. Il consulte le SDK, qui connaît le chemin : cette demande part par NATS Micro vers le worker imageCLI. Le message traverse le bus, arrive au worker, et l'image se génère.

Une fois générée, l'image est déposée dans le blobstore. Et c'est là que quelque chose de discret se passe : ce qui revient dans la réponse, ce n'est pas l'image elle-même — c'est sa référence, un `blob_ref`, une adresse légère qui dit où trouver la chose sans transporter la chose. Le fil NATS ne porte pas des mégaoctets, il porte des pointeurs.

Le harness reçoit cette réponse et la rejette dans le LLM. C'est le deuxième tour de la boucle. Le LLM voit maintenant que l'image existe, que la demande a été satisfaite, et il produit la réponse en texte — la phrase que l'utilisateur attend.

Le harness émet des événements vers le hub. Le hub diffuse vers l'adaptateur Telegram. L'adaptateur pousse la réponse à l'utilisateur. Telegram affiche le message.

Quelques secondes se sont écoulées. Chaque pièce a joué sa note, à son moment, dans un seul flux continu. L'adaptateur a traduit, le hub a assemblé, le harness a orchestré, le dispatcher a routé, le worker a produit, le blobstore a reçu, le LLM a bouclé. Aucune pièce ne savait tout faire — chacune savait exactement ce qu'elle avait à faire. C'est la récompense des sections précédentes : comprendre chaque rôle séparément, c'est ce qui rend le flux lisible d'un bout à l'autre.

---

## 13. L'horizon : ce qui reste à voir

On est maintenant dans la lumière du seuil. Derrière toi, l'atelier avec toutes ses pièces en place, ses circuits qu'on peut suivre du regard. Devant toi, quelques portes encore fermées — pas des murs, des portes. Il est honnête de les nommer.

La première concerne le moteur interne du harness. Tout ce qu'on a décrit — la boucle, les tours, la gestion des outils — suppose un moteur qui orchestre ces étapes. Mais ce moteur n'est pas encore choisi. LangGraph est sur la table, qui apporte des garanties de graphe et de reprise. Une boucle maison légère est aussi envisagée, plus simple, plus directement contrôlable. Et Hermes — le fork de Nous Research qui vit dans cet écosystème — pourrait jouer ce rôle. Un prototype tranchera. C'est la bonne façon de décider : on ne choisit pas un moteur sur le papier, on le fait tourner.

La deuxième porte, c'est la couche des skills — les partitions `SKILL.md` qu'un agent peut lire pour savoir quand et comment appeler un outil. Cette couche viendra, probablement au format Anthropic. Mais c'est une itération suivante, pas la fondation.

La troisième, c'est la question des sub-agents : un harness qui appelle un harness, une instance qui en délègue une partie à une autre. L'architecture y est prête dans ses grandes lignes, parce qu'un harness sans état persistant peut s'imbriquer sans friction. Mais s'en charger dès la première version ou le remettre à l'itération suivante — cette ligne de crête reste à franchir.

Il y a aussi un terme qui a flotté un jour dans une conversation : « lightpool ». Personne ne sait exactement ce qu'il voulait dire. Probablement une confusion avec clipool, le pool de processus Claude qu'on connaît bien. Peut-être une idée en gestation qui n'a pas encore trouvé son contour. Ce mystère-là mérite d'être élucidé avant qu'il devienne une fausse certitude dans les échanges.

Et puis il y a le passage concret, opérationnel : aller du pool partagé actuel vers les instances Quadlet à arobase — ces unités nommées, isolées, une par canal. Ce chemin est tracé, mais le valider sur un prototype avant de le déployer partout est la précaution qui s'impose. On ne bascule pas une topologie de production sur une intuition, même bien fondée.

Ces portes n'obscurcissent pas ce qu'on a vu. Elles indiquent simplement où la promenade continue.

Il y a quelques heures, tu es entré dans cet atelier par la grande porte — le hub, ses sujets NATS, ses adaptateurs qui traduisent le monde extérieur. Tu as traversé la forge des outils, remonté les fils du dispatcher, reconnu l'empreinte du moule harness dans chaque instance, compris comment les serrures distribuent les droits sans les concentrer. Et tu as suivi un message, du premier geste sur un clavier jusqu'à la phrase qui revient en réponse. L'atelier est cohérent. Pas parfait, pas fini, mais cohérent — et c'est ce qui compte pour construire dessus.
