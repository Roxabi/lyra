---
title: Isolation des secrets pour agents — Broker, Facade & Setup
description: Analyse + recommandation pour donner aux agents l'usage des API keys (Telegram, CF, GitHub, Vercel, Brevo, X…) sans qu'ils puissent jamais les lire. Command facade > broker MITM ; validée web/xAI.
---

# Isolation des secrets pour agents — Broker, Facade & Setup

> Status: ANALYSIS — décision technique posée, implémentation à acter (ADR).
> Last updated: 2026-06-15.
> Guide visuel: `~/.roxabi/forge/roxabi-factory/visuals/agent-secret-broker.html` (8 onglets, diagrammes).
> Verdict externe: **confirmé avec caveats** (8 recherches web + xAI/Grok, 23 claims vérifiées sur source).

## TL;DR — la décision

Le bon design = une **command facade** en **user séparé** que l'agent pilote par **intent** — le secret ne
traverse jamais l'agent. Mais isoler la **lecture** de la clé ne suffit pas : l'agent peut toujours **mal
utiliser** une capacité qu'il ne peut pas lire → **ACL par-action + egress allowlist obligatoires**.

- **Facade (B)** retenue **plutôt que** broker egress TLS-MITM (A).
- Conteneurs agents `--network=none` = le **vrai** gate (l'agent ne peut pas contourner la facade).
- Identité = **UDS** (socket inode, même hôte) ou **NATS + ACL factory** (cross-host). Jamais un token bearer.
- Creds dans **Vaultwarden** (déjà en place), fetch **JIT par-requête**.
- `gh` garde son **dispenser éphémère** (cas où la facade « suffit ») ; les tokens **statiques** passent en facade.

## 1. Problème & invariant

Si un script peut lire la variable, l'agent peut la lire — donc la **leaker** (echo, logs, contexte LLM,
commit public). Vrai pour `.env` **et** pour un vault qui *délivre* le secret au process. Déclencheur réel :
un agent a déjà écrit un token Telegram en clair dans un fichier de test.

L'invariant « l'agent ne doit JAMAIS pouvoir lire la clé » élimine toute la colonne **Injection**.

| Modèle | Livraison du secret | Dans le process agent ? | Exemples |
|---|---|---|---|
| **Broker** | requête sans cred → le broker l'attache | **Jamais** | Secretless Broker, Envoy credential_injector, Infisical agent-vault |
| **Injection** | daemon écrit le secret en fichier/env → l'agent le lit | **Oui** | Vault Agent, Doppler `run`, 1Password `op run`, Akeyless |

> Vault = excellent **store** derrière un broker, mais Vault Agent = injection. Seul le modèle broker satisfait l'invariant.

## 2. Deux patterns — verdict : facade (B)

| | A. Egress broker MITM | B. Command facade (retenu) |
|---|---|---|
| L'agent lance la CLI | oui, dans son conteneur | non |
| Ce qui sort du conteneur | HTTPS sans creds, intercepté MITM | **intent / params** |
| Mécanisme | TLS-MITM + injection header | RPC/socket → la CLI tourne côté trusted |
| Surface ajoutée | **CA broker = SPF crown-jewel** | aucune CA, aucun MITM |
| Signing (Twitter v1.1) | **impossible** (HMAC pré-calculé) | **oui** (signe côté trusted) |
| Token-refresh (Reddit/IG) | bricolage | naturel |
| Coût | shims par CLI + distribution CA + fragile | curation de la surface d'opérations |

La curation redoutée (« wrapper chaque CLI ») **EST la feature** : surface d'opérations whitelistée =
scope par-agent + traçabilité, par construction.

## 3. Routage CLI (fact-checké sur source)

En MITM, l'identité = la **source transport** (la CLI ne signe rien). Mais le « no-wrap » n'est vrai que pour
un sous-ensemble, et le transparent-redirect rootless est **impossible** (`CAP_NET_ADMIN` absent sous pasta).

| CLI / lib | HTTPS_PROXY auto ? | CA custom | Verdict |
|---|---|---|---|
| `gh` (Go) | oui (`ProxyFromEnvironment`) | `SSL_CERT_FILE` | marche |
| `git` HTTPS | oui | `GIT_SSL_CAINFO` | marche |
| `vercel` | oui (PR #9880, 2023) | proxy-agent | marche |
| `wrangler` (undici) | **non** (bug #4515) | `NODE_EXTRA_CA_CERTS`¹ | shim requis |
| Python bots | `requests`=oui / `httpx`,`aiohttp`=non | per-SDK | vérif par bot |
| `git` SSH | **non** — bypasse le proxy | — | hors-bande (force HTTPS) |
| **X/Twitter v1.1** | n/a — OAuth1 HMAC-SHA1 signé | — | **MITM impossible** |

¹ `NODE_EXTRA_CA_CERTS` ne couvre pas les libs qui passent un `ca` explicite (carve-out Node).

**Identité de l'agent** (sans bearer-token leakable) :

| Primitive | Falsifiable ? | Supply-chain | Rootless Podman | Coût |
|---|---|---|---|---|
| **UDS bind-mount** (socket inode = identité) | Non | Haute | Excellent | Bas |
| mTLS / SPIFFE SVID | Faible (TTL) | Haute | Moyen | Haut |
| Source IP / netns | Moyen | Faible | Moyen | Moyen |
| Token par-boot (bearer) | **Oui (leak)** | Nulle | Trivial | Minimal |

> Même hôte → **UDS**. Cross-host M₁↔M₂ → **NATS request-reply** (`factory.tool.<service>.<action>`) :
> l'**ACL NATS de factory EST** la couche d'autorisation par-agent.

## 4. Taxonomie auth par service

| Service | Mode facade | Note |
|---|---|---|
| Telegram | exec/proxy — token dans l'URL **path** | rewrite path, pas header |
| Discord | header `Authorization: Bot` statique | trivial |
| Cloudflare | header `Bearer` statique | trivial |
| GitHub API + git-HTTPS | header `Bearer` / Basic | gh-helper couvre déjà (éphémère) |
| GitHub SSH | hors-bande | force HTTPS (`url.insteadOf`) |
| Vercel | header `Bearer` statique | trivial |
| Brevo | header **`api-key:`** (¬ `Authorization`) | nom de header custom |
| **X/Twitter v1.1** | **SIGNING** (OAuth1 HMAC) | la facade signe — impossible autrement |
| X v2 write / Reddit / Instagram | **OAUTH-REFRESH** | Reddit=1h ¬refresh-token ; IG=60j refresh |

## 5. gh-helper aujourd'hui = dispenser, pas facade

`factory-gh-helper` isole la **clé RSA** de la GitHub App (PEM, uid 1501) mais **vend le token `ghs_…` en clair**
à l'agent (uid 1500) via socket — git (credential helper) **et** gh (`GH_TOKEN` dans le child). L'agent **peut
lire/leaker** ce token. Acceptable **uniquement** car éphémère (1h) + révocable. **Non généralisable** aux tokens
statiques.

| | Idéal facade | gh-helper dispenser |
|---|---|---|
| L'agent voit le credential | jamais | oui (token court) |
| Ce qui est protégé | la capacité | la clé de minting (PEM) |
| Mitigation du leak | n/a | TTL 1h + révocable + scopé |
| Autorisation par-opération | oui | non (tout process group-1502 obtient le token) |

> La littérature (arXiv 2506.08837) nomme exactement quand la facade **suffit** : apps à actions fixes étroites
> → c'est la description de gh-helper. **Le garder tel quel** ; basculer les tokens statiques en facade.

## 6. Red-team — ce que la facade ne résout PAS

La facade empêche l'agent de **lire** le secret, pas de **mal utiliser** la capacité. La panne initiale ressurgit :
« envoie ce fichier-contenant-le-token via Telegram » → la facade le délivre.

| Risque | Sév. | Mitigation |
|---|---|---|
| Abus de capacité via canal légitime (push public, spam, delete zone) | Haut | ACL verbe+path par scope, rate-limit, human-in-loop destructif |
| Confused-deputy / SSRF via la facade | Haut | egress allowlist + DNS-pin + reject IP privées + schéma d'entrée strict |
| Facade = cible #1 (crown-jewel amplifier) | Haut | image pinnée/lockfile, creds JIT (zéro long-lived), 1 facade/type de cred, backup off-site |
| Résurgence en réponse (« list tokens ») | Moyen | filtrer/redacter les réponses, bloquer management-plane |
| Exfil via canal légitime (Discord → /home) | Moyen | inspection payload + allowlist chat_id/webhook |
| TLS-MITM (modèle A) | Bas | n/a — la facade évite tout MITM |

**3 risques les plus sous-estimés** : 1) abus via canal légitime ; 2) le MITM casse les CLI de façon
non-évidente ; 3) le store de la facade devient la cible la plus précieuse de l'infra.

## 7. Validation externe (8 recherches — web ×6 + xAI ×2)

**Verdict : confirmed-with-caveats.** Consensus industrie, pas un design idiosyncratique.

**Validations**
- **V1** — pattern = consensus : CyberArk Secretless, GitHub Agentic Workflows MCP gateway, AWS Bedrock AgentCore,
  Arcade.dev, Infisical Agent Vault, SANS CB4A, **Anthropic Zero-Trust for AI Agents** (tool-firewall + tokens courts),
  **OWASP Top 10 Agentic 2026 ASI03**.
- **V2** — `--network=none` = le vrai gate : Red Hat confirme **AF_UNIX survit à `--network=none`** (egress IP éliminé) ;
  **SO_PEERCRED = identité noyau inforgeable** → « socket inode = identité » est sain, sans token bearer.
- **V3** — rejeter le MITM pour une facade signing-aware = correct : SigV4 / OAuth1 HMAC cassent sous un proxy qui
  modifie les headers.

**Réfutations (caveats)**
- **R1** — facade = amplificateur crown-jewel : attaque supply-chain **LiteLLM (mars 2026)**, fenêtre PyPI 40 min →
  AWS/GCP/Azure/SSH/K8s creds de toute la base ; fuites OpenRouter ×48 YoY.
- **R2** — le prompt-injection détourne l'**intent**, pas la clé : *lethal trifecta* (Willison) ; la facade exécute
  fidèlement la requête compromise (M365 Copilot, GitHub MCP, GitLab Duo). Pièce manquante = **Dual LLM** (arXiv 2506.08837).
- **R3** — SSRF inhérent à toute facade HTTP-sortante (sinks LiteLLM, bypass DNS-rebinding / IPv6 `fd00:ec2::254`).

**Ajustements justifiés**
- **A1 (R1)** — durcir la facade : image pinnée + lockfile, creds **JIT par-requête** (zéro long-lived au repos),
  **1 facade par type de cred**, audit append-only obligatoire.
- **A2 (R2)** — intent en contrôle **primaire** : human-in-loop par défaut (Rule of Two), **Dual LLM**
  (LLM quarantaine sans accès facade traite l'input ; LLM privilégié appelle la facade), ACL **deny-all** par défaut.
- **A3 (R3)** — egress durci : DNS résolu une fois (pin, refus rebinding), reject IP privées/link-local, refus URL
  avec creds, **schéma d'entrée strict** par endpoint.

## 8. Architecture recommandée & rollout

```
agents (--network=none, aucun secret)
   │  intent via UDS (même hôte) | NATS+ACL factory (cross-host)
   ▼
facade (user dédié)  ── policy ──▶ ACL / egress allowlist (deny-all)
   │  signe · refresh   ── log ───▶ audit append-only (autre uid)
   ├── fetch cred JIT ─▶ Vaultwarden (creds réels, backup off-site)
   └── appel authentifié ─▶ Upstream APIs (Telegram·CF·GitHub·Vercel·Brevo·X)
```

| Phase | Objet | Détail |
|---|---|---|
| **P1** | Contrat & identité | surface d'opérations facade ; schéma NATS subjects/ACL aligné factory ; UDS vs NATS |
| **P2** | 1er service bout-en-bout | PoC Telegram ou Cloudflare (token statique) ; `--network=none` ; creds JIT Vaultwarden |
| **P3** | Migration tokens statiques | Discord, Brevo, Vercel, CF en facade ; gh garde son dispenser ; Twitter v1.1 → facade signe |
| **P4** | Durcissement | ACL deny-all + egress allowlist + DNS-pin ; audit append-only + rate-limit ; human-in-loop + Dual LLM |

## 9. Décisions / ADR à acter

- Facade-vs-dispenser + découpage par-service (gh = dispenser éphémère ; statiques = facade).
- Creds = Vaultwarden (déjà en place), fetch JIT.
- Réutiliser **NATS + ACL factory** comme couche d'autorisation cross-host.
- Facade durcie en cible supply-chain (image pinnée, 1 facade par type de cred).
- **Recovery destructive obligatoire** : si la clé maîtresse / le store est perdu, l'opérateur doit pouvoir **wipe les data locales** et **tout regénérer** sans état zombie (voir §10).

## 10. Recovery — clé perdue → wipe + regen

**Invariant ops :** perte de la clé maîtresse ≠ état indéterminé. L'opérateur doit avoir un chemin
documenté : *backup si possible → wipe data dépendantes → regen from scratch → re-seed depuis les
fournisseurs upstream*.

### Précédent factory (NATS nkeys — déjà en place)

Rotation **destructive mais réversible** via backup horodaté :

```bash
# Backup ~/.roxabi/factory/nkeys/ → nkeys.bak.{epoch}/ puis wipe + regen complète
factory-acl genkeys --regenerate          # TTY : confirmation interactive
factory-acl genkeys --regenerate --yes    # scripts / CI

# Identités external (M₂ llmCLI, voiceCLI…) : fan-out manuel obligatoire
factory-acl genkeys --regenerate --yes --ack-external-distribution

# Recharger secrets Podman + redémarrer la stack
./deploy/install.sh --force --secrets-only
make converge
```

Chemins touchés : `~/.roxabi/factory/nkeys/*.seed`, `auth.conf`, secrets Podman `factory-nats-*`.
Les **clients NATS** (hub, adapters, workers) doivent redémarrer — anciennes seeds = auth rejetée.

> `factory-acl genkeys --regen-authconf` **ne wipe pas** : re-render `auth.conf` depuis seeds
> existants. Insuffisant si les `.seed` sont perdus — il faut `--regenerate`.

### Broker / Vaultwarden (à acter — gap actuel)

Si le **mot de passe maître Vaultwarden** ou le **chiffrement du vault** est perdu :

| Étape | Action |
|-------|--------|
| 1. Wipe | Supprimer les data Vaultwarden **factory** (DB SQLite + attachments) — pas de « déchiffrer sans clé » |
| 2. Regen store | Réinitialiser l'org/collection factory (nouveau master password) |
| 3. Re-seed | Re-saisir chaque API key depuis les consoles fournisseurs (Telegram BotFather, GitHub, CF, Brevo…) |
| 4. Redémarrer | `systemctl --user restart` sur les facades ; agents `--network=none` inchangés (aucun secret local) |

**Ce qu'on ne peut pas regénérer automatiquement :** les tokens upstream — il faut les **révoquer +
recréer** côté fournisseur si fuite suspectée, puis re-importer dans le vault neuf.

**Data à wipe explicitement** (pas de demi-mesure) :

- Vaultwarden : DB + fichiers attachés du vault factory
- Facade : cache JIT éventuel, audit log local (optionnel — append-only peut être archivé avant wipe)
- **Pas** les agents : ils n'ont jamais stocké la clé

**Garde-fous ADR :**

- Commande unique documentée (`factory secrets reset --i-know-what-im-doing` ou runbook dédié)
- Confirmation interactive + `--yes` pour automation
- Backup automatique avant wipe (comme `nkeys.bak.{epoch}`)
- Checklist post-regen : smoke par service (Telegram send, gh api, …)

### Lien avec l'architecture §8

Les facades ne gardent **aucun cred long-lived au repos** (JIT Vaultwarden) → le blast radius d'une
perte de clé maîtresse se limite au store central, pas aux conteneurs agents. Le prix : **obligation**
d'un runbook wipe+regen clair — sinon l'opérateur reste bloqué sans chemin de sortie.

## Sources

- À lire en 1er — **arXiv 2506.08837**, *Design Patterns for Securing LLM Agents against Prompt Injections* (IBM/ETH/Google/MS) — Dual LLM + condition d'adéquation de la Command Facade.
- CyberArk Secretless Broker · GitHub Agentic Workflows security architecture · Anthropic « Zero Trust for AI Agents » (2026-05) · OWASP Top 10 for Agentic Applications 2026 (ASI03, Excessive Agency).
- Red Hat socket-activation Podman (AF_UNIX + `--network=none`) · SPIFFE/SPIRE (SO_PEERCRED).
- Simon Willison « the lethal trifecta » · HeroDevs / Escape.tech (attaque & SSRF LiteLLM).
- Fact-checks CLI : code go-gh, code git `http.c`, workers-sdk #4515, vercel #9880, RFC 5849.
