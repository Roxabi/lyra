export const meta = {
  name: 'audit-epic-1662',
  description: 'Vérifier que toutes les 22 subissues de l\'epic #1662 sont correctement implémentées',
  phases: [
    { title: 'Audit', detail: '4 agents explorent chaque cluster de vérification en parallèle' },
    { title: 'Synthesize', detail: 'Reconcile agent agrège les résultats et émet le verdict final' },
  ],
}

const args = {
  epic_number: 1662,
  subissues: [1636,1637,1634,1660,1663,1664,1665,1635,1659,1638,1666,1667,1640,1661,1639,1652,1653,1654,1655,1656,1657,1658],
  prs: [1685,1687,1683,1684,1682,1681,1679,1680,1690,1688,1692,1693,1695,1697,1696,1699,1694,1686,1689,1675],
}

phase('Audit')

log('Lancement audit parallèle — 4 clusters')

const results = await parallel([
  () => agent(`
Tu es l'agent d'audit CLUSTER A: Refactorings DRY + Stage-axis.

Mission: vérifier que les 5 issues suivantes sont correctement implémentées dans le code:
- #1660 feat(adapters): create BasePlatformAdapter + BaseFormatter + shared send helper
- #1663 Bootstrap wiring refactor: collapse Telegram/Discord standalone duplication
- #1664 Agent factory: collapse voice_overlay NATS init duplication
- #1665 Agent CLI: collapse telegram/discord into parameterized platform module
- #1666 Stage-axis: relocate inbound shared helpers to core/shared
- #1667 Stage-axis: refactor wire parsers to use Protocol aliases

Vérifications obligatoires:
1. Lire src/factory/adapters/telegram.py et discord.py — confirmer qu'ils héritent d'une classe base commune (BasePlatformAdapter) et utilisent un formatter partagé.
2. Lire src/factory/bootstrap/ — confirmer que les fonctions standalone Telegram/Discord sont remplacées par un bootstrap unifié.
3. Lire src/factory/agents/ — confirmer que voice_overlay et NATS init ne sont plus dupliqués.
4. Lire src/factory/agent_cmd/ — confirmer que les commandes Telegram/Discord sont factorisées en un module paramétrable.
5. Lire src/factory/inbound/ — confirmer que les shared helpers sont bien relocalisés dans src/factory/core/shared/.
6. Lire src/factory/inbound/wire_*.py — confirmer qu'ils utilisent des Protocol aliases (typing.Protocol) plutôt que des imports directs.
7. Grep pour trouver des patterns dupliqués RESTANTS entre Telegram et Discord (ex: "send_message", "edit_message", "reply_markup", etc.) — si > 20 lignes dupliquées → flag.
8. Vérifier que les PRs #1681, #1684, #1683, #1682, #1690, #1688 ont bien fusionné leurs changements dans staging.

Livrable: rapport structuré avec pour chaque issue:
- status: ✅ | ⚠️ | ❌
- evidence: fichiers lus + lignes clés
- regressions: tout pattern dupliqué restant
- confidence: high | medium | low
`, { label: 'Cluster-A: DRY+Stage-axis', phase: 'Audit', agentType: 'backend-dev' }),

  () => agent(`
Tu es l'agent d'audit CLUSTER B: Configs & Protocoles.

Mission: vérifier que les 3 issues suivantes sont correctement implémentées:
- #1659 feat(core): extract BusConfig + MemoryConfig + PlatformConfig + TurnStoreConfig
- #1661 feat(ports): create llm_types.py + AuthStoreProtocol + IdentityAliasStoreProtocol
- #1655 feat(ci): file exemption expiry dates

Vérifications obligatoires:
1. Lire src/factory/core/config.py ou les fichiers config équivalents — confirmer que BusConfig, MemoryConfig, PlatformConfig, TurnStoreConfig existent et sont utilisés.
2. Grep dans src/factory/core/ pour les anciennes hardcoded constants (ex: bus_timeout=5, memory_limit=100, etc.) — elles doivent référencer les nouvelles Config classes.
3. Lire src/factory/ports/llm_types.py — confirmer qu'il existe et contient les Protocols.
4. Lire src/factory/ports/auth_store.py et identity_alias_store.py — confirmer qu'ils existent.
5. Vérifier que le gate check_hardcoded_constants.sh fonctionne: lire tools/hardcoded_constants_baseline.txt et tools/check_hardcoded_constants.sh. Exécuter le script si possible.
6. Vérifier tools/file_exemptions.txt — chaque entrée doit avoir un token expires=YYYY-MM-DD valide.
7. Grep pour les imports de llm_types.py et des Protocols dans le code — confirmer qu'ils sont consommés.

Livrable: rapport structuré avec pour chaque issue:
- status: ✅ | ⚠️ | ❌
- evidence: fichiers lus + lignes clés
- regressions: constants encore hardcodées
- confidence: high | medium | low
`, { label: 'Cluster-B: Configs+Protocols', phase: 'Audit', agentType: 'backend-dev' }),

  () => agent(`
Tu es l'agent d'audit CLUSTER C: CI Gates & Process.

Mission: vérifier que les 7 issues suivantes sont correctement implémentées:
- #1652 feat(ci): debt expiry gate — block stale DEBT: markers
- #1653 feat(ci): ban sleep() in tests without event-based sync comment
- #1654 feat(ci): hardcoded constant gate
- #1655 feat(ci): file exemption expiry dates
- #1656 docs(process): PR template with debt/sleep/constant checklist
- #1657 docs(process): mandatory axial review for cross-layer PRs
- #1658 docs(process): debt retrospective after each /dev cycle

Vérifications obligatoires:
1. Lire .github/workflows/ — confirmer que les gates CI sont présents (debt_expiry, test_sleep, hardcoded_constants, file_exemptions, architecture_snapshot).
2. Exécuter localement si possible:
   - tools/check_debt_expiry.sh
   - tools/check_test_sleep.sh
   - tools/check_hardcoded_constants.sh
   - tools/check_file_exemptions.sh
   - tools/check_architecture_snapshot.sh
   Reporter le résultat (exit code + output).
3. Lire .github/pull_request_template.md — confirmer que les checklists debt/sleep/constant sont présentes.
4. Lire .github/workflows/axial-review.yml — confirmer qu'il existe et qu'il applique le label dev-core:axial-adr-review.
5. Lire docs/process/dev-cycle.md — confirmer que la debt retrospective y est documentée.
6. Vérifier que .importlinter existe et est valide.

Livrable: rapport structuré avec pour chaque issue:
- status: ✅ | ⚠️ | ❌
- evidence: fichiers lus + scripts exécutés
- regressions: gates manquants ou cassés
- confidence: high | medium | low
`, { label: 'Cluster-C: CI+Process', phase: 'Audit', agentType: 'devops' }),

  () => agent(`
Tu es l'agent d'audit CLUSTER D: Bootstrap & Qualité.

Mission: vérifier que les 3 issues suivantes sont correctement implémentées:
- #1636 P0: Refactor process_one god method (165 lines)
- #1637 P0: Harden _log_turn error contract — prevent permanent turn loss
- #1639 P1: Harden bootstrap broad-catch — narrow except Exception to specific types

Vérifications obligatoires:
1. Lire la fonction process_one (src/factory/ ou équivalent) — mesurer sa longueur en lignes. Si > 120 lignes → ⚠️. Identifier la décomposition en sous-fonctions.
2. Lire _log_turn (src/factory/core/ ou équivalent) — confirmer que le contrat d'erreur est durci (pas de perte de turn silencieuse, gestion de retry/fallback, pas de suppression de l'erreur).
3. Grep 'except Exception' dans src/factory/bootstrap/ — compter les occurrences. Pour chacune, vérifier si elles sont accompagnées d'un commentaire justifiant le broad-catch. Si > 3 broad-catch sans commentaire → ❌.
4. Vérifier que les PRs #1685, #1687, #1675, #1686 ont bien appliqué leurs changements.
5. Lire docs/process/dev-cycle.md — vérifier que la checklist de fermeture inclut le nettoyage pre-cleanup.

Livrable: rapport structuré avec pour chaque issue:
- status: ✅ | ⚠️ | ❌
- evidence: fichiers lus + lignes clés + mesures
- regressions: god method encore présent, broad-catch non justifié
- confidence: high | medium | low
`, { label: 'Cluster-D: Bootstrap+Quality', phase: 'Audit', agentType: 'security-auditor' }),
])

log('Audit clusters terminés, lancement synthèse')

phase('Synthesize')

const verdict = await agent(`
Tu es l'agent de synthèse (architecte) pour l'audit de l'epic #1662.

Tu reçois 4 rapports d'audit de clusters. Ta mission:
1. Agréger les résultats en un tableau global: Issue | Status | Evidence | Confidence.
2. Vérifier les dépendances blocked-by déclarées dans l'epic:
   - #1638 blocked by #1634 → le stage-axis refactor a-t-il attendu le DRY?
   - #1640 blocked by #1638 → les protocols ont-ils attendu le stage-axis?
   - #1661 blocked by #1640 → llm_types a-t-il attendu les protocols?
   - #1660 blocked by #1634 → BasePlatformAdapter a-t-il attendu le DRY?
   - #1659 blocked by #1635 → les configs ont-elles attendu l'extraction?
   - #1656 blocked by #1652, #1653, #1654, #1655 → le PR template a-t-il attendu les gates?
3. Émettre un verdict global:
   - ✅ Fully implemented — tous les clusters OK, pas de régression
   - ⚠️ Mostly implemented — 1-2 warnings mineures, pas bloquant
   - ❌ Incomplete — au moins 1 issue n'est pas implémentée correctement
4. Proposer les actions correctives si nécessaire.

Les 4 rapports à analyser:

${JSON.stringify(results)}

Livrable: synthèse finale en markdown structuré.
`, { label: 'Reconcile: verdict final', phase: 'Synthesize', agentType: 'dev-core:architect' })

return {
  epic: 1662,
  clusters: results,
  verdict: verdict,
  date: '2026-06-02',
}
