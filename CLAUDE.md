# CLAUDE.md — Instructions pour Claude Code

## Projet

**Lyra** — Moteur d'agent IA personnel (hub-and-spoke, asyncio, multi-canal).
Voir `ARCHITECTURE.md` pour le contexte complet.

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `ARCHITECTURE.md` | Architecture + décisions techniques |
| `ROADMAP.md` | Roadmap et priorités |
| `topics/` | Notes de recherche et design |
| `artifacts/` | Frames, specs, plans, analyses (dev-core) |
| `setup.sh` | Script post-install Machine 1 |

## Infrastructure locale

Les données machines (IPs, partitions, configs) sont dans **`local/machines.md`** (gitignored, non versionné).

Consulter ce fichier pour :
- IPs et hostnames des machines
- Layout des disques
- Commandes SSH utiles
- Services actifs

```bash
# Connexion Machine 1 (Hub)
ssh mickael@192.168.1.16
```

## Machines

- **Machine 1** (`roxabituwer`, `192.168.1.16`) — Hub central, Ubuntu Server 24.04, RTX 3080, 24/7
- **Machine 2** (`ROXABITOWER`) — AI Server, Windows + WSL2, RTX 5070Ti, à la demande

## Conventions

- Langue : français pour les docs et commits, anglais pour le code
- Commits : Conventional Commits (`feat:`, `fix:`, `chore:`, etc.)
- Issues : via `dev-core` workflow (`/dev #N`)
