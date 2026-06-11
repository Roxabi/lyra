# Backlog review — 89 issues ouvertes (2026-06-10)

Méthode : dataset complet (labels, milestones, parent/sub-issues natifs, blocked-by natifs) + 5 agents ∥ par cluster sémantique avec vérification code (grep src/, packages/, deploy/, docs/). 18 issues re-vérifiées manuellement après détection de sections fabriquées dans 2 rapports d'agents (obs #1766-1774, CM #1816-1817, jobs #1798/#1800) — les verdicts ci-dessous pour ces issues viennent d'une lecture directe des bodies, pas des agents.

## Scorecard

| Verdict | n | Issues |
|---|---|---|
| **OK** | ~15 | 1759, 1761-1774 (doc exemplaire), 1796-1800, 1805, 1812, 1813, 1814-1818, 439, 1791 |
| **UPDATE** (garder, corriger labels/relations/body) | ~65 | le reste |
| **STALE** (prémisse caduque, réécrire) | 3 | #1203, #120, #414 |
| **DROP?** (candidates fermeture) | 3 | #16, #17, #20 |

Couverture milestones : **100 %** (toutes les issues ont un milestone, mapping cohérent M0/M1/M2/M3/M7/M9/M10/Final/Phase4/Phase5). Couverture parent : 72/89 ; les 17 sans parent sont des epics top-level ou des standalones légitimes — sauf exceptions ci-dessous.

## 1. Relations à corriger

### Parents pointant sur des epics FERMÉS
| Issue | Parent actuel | Fix proposé |
|---|---|---|
| #1720 (Discord set-watch-channels CLI) | #1049 fermé 06-04 | re-parenter → #475 (rule engine) ou #493 |
| #481 (inter-agent communication) | #63 fermé 06-01 | re-parenter → epic Phase 4 ou standalone |

### blocked-by manquants (déclarés en body mais absents du champ natif)
| Issue | Ajouter | Source |
|---|---|---|
| #1620, #1621 | #1619 | body : « Blocked by #1619 » |
| #1778 (epic JobContext) | #1619 | body : depends-on |
| #1792 (epic Shape D) | #1778, #1619, #1203 | body : Cross-refs Blocked-by |
| #1772, #1773, #1774 | #1771 | body : « Renders in #1771 shell » |
| #1817 (HITL approve) | #1818 ? | approve→publish nécessite le tool X — à confirmer |

### blocked-by/labels périmés (blockers fermés)
| Issue | Périmé | Action |
|---|---|---|
| #475 | label `blocked` + #1284 fermé | retirer le label `blocked` — débloquée |
| #477 | #1807 fermé (spike omp, orthogonal au ToolHandler) | retirer la relation |
| #1624 | #1377 fermé | retirer ; reste #1619 |
| #1009 | #1284, #1008 fermés | retirer ; reste #1619 |
| #669, #671, #641 | prose « Depends on #668/#665/#639 » — tous fermés | nettoyer les bodies (relations natives déjà correctes) |

Débloquées prêtes à lancer : **#1782** (size:S, blocker #1708 fermé), **#1812** (#1810/#1811 fermés), **#475**, **#477**, **#1720**.

## 2. Hygiène labels

| Problème | Issues | Fix |
|---|---|---|
| Schéma legacy seul (`priority:P*`, `phase:N`) sans canon P*-high/medium/low | 16, 17, 20, 86, 87-96, 477, 478, 479¹, 480¹, 481¹, 493, 644, 645, 725 | sweep → labels canon |
| Doublon legacy + canon (`P2-medium` **et** `priority:P2`) | 120, 414, 439, 475, 640, 641, 642 | retirer les `priority:P*`/`phase:N` |
| `size:` manquant | 1619, 1620, 1621, 1623, 1624, 1778, 1791, 1794, 1795, 1796, 1797, 1798, 1799, 1800, 1805 | ajouter (majorité = S/F-lite) |
| `feat` au lieu de `feature` | 641, 642, 1720 | normaliser |
| Priorité epic ≠ enfants | #1792 P1-high, enfants 1796-1800 tous P3-low | la critical review 06-09 a dépriorisé Shape D au profit de la slice CM → demote #1792 → P3-low (ou re-prioriser les enfants si ce n'est pas voulu) |
| Milestone douteux | #640 (« M3 statelessness », body dit M4) rangé dans milestone « M2 — Tools » | déplacer vers un milestone dédié ou documenter |

¹ 479/480/481 ont les deux schémas (P3-low + priority:P3).

Note : `graph:lane/*` n'est PAS legacy — posé par issue-triage sur les issues du 06-10. Seuls `priority:P*` et `phase:N` sont les vrais résidus.

## 3. Sweep lyra→factory jamais fait sur le backlog (epic #1671 fermé 06-08)

| Issue | Stale |
|---|---|
| #1044 | titre « lyra-code-worker » |
| #1047 | titre « lyra worker bootstrap » + incohérence heartbeat (`factory.worker.heartbeat` vs `factory.code-worker.heartbeat` — trancher) |
| #1050, #1051, #1053 | titres `lyra.jobs.*` + paths `src/lyra/…` |
| #1052, #1054 | bodies `lyra.jobs.*` |
| #1490 | section « Naming » : `lyra.harness.*`, container `lyra-harness` |
| #86, #87 | URLs `github.com/Roxabi/lyra` |
| #414, #640, #641, #642 | paths `src/lyra/…` |
| #439, #480, #481, #1799 | prose « Lyra » (cosmétique) |

(#1009 : `_lyra_sessions` est le nom RÉEL du dict actuel — llm_client.py:62, cli_pool.py:92 — pas un stale.)

## 4. STALE — à réécrire

- **#1203** (JetStream JOBS stream) : stream défini sur `lyra.jobs.>` + `After=lyra-nats.service` ; contredit la taxonomie `factory.job.<id>.*` (#1793, son propre blocker). Réécrire après #1793.
- **#120** (auto-remediation LLM health) : repose sur `src/factory/monitoring/escalation.py` que #1768 supprime. Réécrire contre Alertmanager webhook (M3) ; parent #1760 OK mais son body devra suivre.
- **#414** (personality vs voice config) : partiellement fait — `AgentTTSConfig.personality` existe (agent_config.py:80) ; agents désormais en config.db. Re-scoper sur le gap restant (personality → system prompt). A un enfant (#479) sans être epic.

## 5. DROP candidates

#16 (LegalTech), #17 (MedTech), #20 (Polymarket) : stubs vision sans scope, redondants sous #96 qui garde l'intention. Fermer « deferred » ou fusionner dans #96. #88-95 (Layer shells) : thin mais structure de décomposition valide pour M10 — garder.

## 6. Qualité doc

- **Excellente** (Goal/Context/Scope/AC/Deps) : tout juin — 1759-1774, 1792-1800, 1814-1818, 1812/1813. Les chaînes blocked-by y sont logiques et déclarées.
- **Thin, sans AC** : cluster M2 Tools (#477, #478, #644, #645, #725, #1713, #1720, #475) — à re-spécifier avant lancement (le flow /dev frame→spec le fera ; minimum : AC + paths actuels).
- **Vérifié contre le code** : WorkEnvelope = 0 hit (#1619 réel) ; `factory.results/progress` encore dans subjects.py:27-28 (#1793 réel) ; ToolHandler/RemoteTool/SatelliteRegistry = 0 hit hors collision `StreamToolHandler` (#493 entièrement pre-impl) ; obs/ = base+noop seulement (#669 réel) ; pas de /metrics hub (#1765 réel) ; pas d'OTEL env dans les quadlets clipool (#1764 réel) ; #1782 : subjects.py déjà dédupliqué mais daemon.py:142,144 + `_SAFE_WORKER_ID_RE` restent.

## 7. Chemins critiques

```
#1782 (S, débloquée) → #1793 (taxonomie) → #1795, #1203, #1799, #1798…  ← gate de M1
#1619 (WorkEnvelope, débloquée, 9+ dépendants) → 1620/1621/1623/1624/1048/1009/1773/1796  ← gate transverse
M9 slice CM (P1) : #1815 → #1816 → #1817 ; #1818 ∥ — prête, aucune dépendance externe déclarée
M0 : #1812 (débloquée) → #1813
```

## Batches d'exécution proposés (via roxabi-issues:issue-triage)

1. **Relations** (~15 mutations) : re-parents ×2, blocked-by manquants ×8, périmés ×4, label `blocked` ×1.
2. **Labels** (~45 mutations) : purge priority:P*/phase:N (~25 issues), size: (~15), feat→feature (3), priorité #1792.
3. **Bodies lyra-sweep** (~16 issues) : titres ×5, bodies/paths ×11.
4. **Réécritures** : #1203 (post-#1793), #120, #414.
5. **Fermetures à confirmer** : #16, #17, #20.

---

# Exécution (2026-06-11)

Tous les batches exécutés via `roxabi-issues:issue-triage` (+ `gh` pour labels legacy/bodies/closures, hors verbes du skill).

| Batch | Résultat |
|---|---|
| 1. Relations | #1720 re-parenté #1049→#475 · #481 parent #63 retiré · blocked-by ajoutés : 1778←1619, 1792←{1778,1619,1203}, 1772/1773/1774←1771, 1817←1818, 120←1763 · retirés : 477←1807, 1624←1377, 1009←{1284,1008} · (1620/1621←1619 existaient déjà — faux positif agent) |
| 2. Labels | `priority:P*`/`phase:N` purgés sur 30 issues · `blocked` retiré (#475) · `feat`→`feature` ×3 · size: ajouté ×16 (1619-1624, 1713, 1720, 1778, 1791, 1794-1800, 1805) · priorité canon ajoutée ×19 · **#1792 demoted P1→P3** (aligné enfants, post-critical-review) |
| 3. Sweep lyra | 7 titres renommés (1044, 1047, 1050, 1051, 1053, 1203, 1490) · 54 remplacements bodies (87, 640-642, 1050-1054, 1203, 1490) · subjects jobs → placeholder `factory.job.<id>.*` (taxonomie finale = #1793) · #1203 bannière « design à revalider post-#1793 » |
| 4. Réécritures | #120 re-ciblé Alertmanager/M3 (+blocked-by 1763) · #414 re-scopé sur le gap restant (personality→system prompt), paths vérifiés · #1203 = bannière+sweep (réécriture complète après #1793) |
| 5. Fermetures | #16, #17, #20 fermées « not planned » (intention conservée dans #96) |

État final : **86 issues** ouvertes du scope audité (+#1832 hors scope, créée 06-11) · 0 label legacy `priority:*`/`phase:*` · 100 % size + priorité canon · 0 parent fermé · 0 blocked-by périmé.

Corrections vs rapport initial (fabrications d'agents détectées en exécution) : #86 n'avait aucune URL stale ; #1047 n'avait pas d'incohérence heartbeat ; 1620/1621 avaient déjà leur blocked-by.
