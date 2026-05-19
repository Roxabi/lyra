# References — Repos à étudier (non cités dans 00-08)

Triés par pertinence par rapport à notre architecture.

## Top priorité

| Repo | GitHub | Complète notre… | Pourquoi |
|------|--------|-----------------|----------|
| **mem0ai/mem0** | https://github.com/mem0ai/mem0 | 02, 03, 04 | Le plus production-ready — multi-niveaux (user/session/agent), entity resolution, forgetting. Très proche Compiled Truth + Graph. |
| **gannonh/memento-mcp** | https://github.com/gannonh/memento-mcp | 02 | Knowledge Graph Neo4j + vector search + temporal awareness native. Exactement notre 02. |
| **CaviraOSS/OpenMemory** | https://github.com/CaviraOSS/OpenMemory | 01, 02, 07 | Temporal knowledge graph, valid_from/valid_to, composite scoring. Très proche Raw + Decay. |
| **Growth-Kinetics/DiffMem** | https://github.com/Growth-Kinetics/DiffMem | 01, 03 | Mémoire basée sur Git (markdown + commits). Immuabilité + évolution via git diff. |
| **agiresearch/A-mem** | https://github.com/agiresearch/a-mem | 05, 02 | Agentic Memory, organisation dynamique style Zettelkasten + ChromaDB. |
| **StructureMA/memory-decay** | https://github.com/StructureMA/memory-decay | 07 | Implémentation directe courbe Ebbinghaus + renforcement + confidence temporelle. |
| **sachitrafa/cognitive-ai-memory** | https://github.com/sachitrafa/cognitive-ai-memory | 07 | Decay biologique Ebbinghaus + spaced repetition + pruning auto. Très proche de notre `memory_strength`. |
| **matrixorigin/memoria** | https://github.com/matrixorigin/memoria | 01, 06 | "Git for AI Agent Memory" — version control + snapshot + rollback. Immuabilité poussée. |

## Bonus

- **TsinghuaC3I/Awesome-Memory-for-Agents** — https://github.com/TsinghuaC3I/Awesome-Memory-for-Agents
  Liste de papiers académiques (short-term vs long-term, success/failure based, etc.)

## 3 à explorer en priorité

1. **mem0ai/mem0** — le plus mature, vision globale la plus proche
2. **gannonh/memento-mcp** — pour améliorer le Knowledge Graph temporel (02)
3. **matrixorigin/memoria** ou **Growth-Kinetics/DiffMem** — pour pousser l'immuabilité Git-like (01)
