# Lyra — Sécurité, Routing & Mémoire Isolée

> Reference document. Last updated: 2026-03-13.
> **Status**: #auth (#151 ✅), #routing (#152 ✅) are shipped. #commands and #memory-isolation remain open.

---

## Vue d'ensemble

4 domaines à implémenter pour garantir qu'un utilisateur autorisé reçoit la bonne réponse, du bon agent, sur le bon canal, avec sa mémoire isolée.

```
[Canal] → AuthMiddleware        (qui peut parler ?)
        → CommandParser          (quelle action ?)
        → Bus → Router           (quel agent / pool ?)
                → ComplexityEstimator → LLMConfig   (quel modèle ?)
                → Agent → MemoryManager (user_id filter absolu)
                        → RoutingContext (bon bot + bon channel)
        → Adapter (vérifie routing avant envoi)
```

---

## #auth — AuthMiddleware + TrustLevel

### Problème

Sans auth, n'importe quel utilisateur peut envoyer un message qui atteint le Bus et consomme des ressources (LLM tokens, mémoire, CPU).

### Solution

Auth au niveau Adapter, **avant** le Bus. Le message est rejeté à la source.

```python
class TrustLevel(Enum):
    OWNER   = "owner"    # accès total, toutes commandes
    TRUSTED = "trusted"  # accès normal
    PUBLIC  = "public"   # accès limité (si activé)
    BLOCKED = "blocked"  # rejeté silencieusement

class AuthMiddleware:
    trust_map: dict[str, TrustLevel]  # user_id → TrustLevel
    default: TrustLevel = TrustLevel.BLOCKED

    async def check(self, raw_event) -> TrustLevel:
        user_id = self.extract_user_id(raw_event)
        return self.trust_map.get(user_id, self.default)
```

**Intégration dans chaque Adapter :**

```python
async def on_event(self, raw_event) -> Message | None:
    trust = await self.auth.check(raw_event)
    if trust == TrustLevel.BLOCKED:
        return None  # dropped — never reaches the Bus
    msg = self.normalize(raw_event)
    msg.trust_level = trust
    return msg
```

### Config

```toml
# config.toml
[auth.telegram]
trusted_users = ["7377831990"]  # Mickael
owner_users   = ["7377831990"]
default       = "blocked"

[auth.discord]
trusted_roles = ["admin", "trusted"]
default       = "blocked"
```

### Implementation — ✅ Shipped (#151)

- [x] `AuthMiddleware` in `src/lyra/core/auth.py`
- [x] `TrustLevel` enum in `src/lyra/core/trust.py`
- [x] Config-driven trust_map (TOML)
- [x] Integrated in TelegramAdapter + DiscordAdapter
- [x] CLIAdapter (trust = OWNER by default)
- [x] Rejection logging

---

## #routing — RoutingContext + vérification Adapter — ✅ Shipped (#152)

### Problème

Sans `RoutingContext` complet dans la `Response`, l'Adapter sortant ne sait pas sur quel bot, quel chat, quel thread envoyer la réponse — risque d'envoyer au mauvais endroit dans un setup multi-bot ou multi-channel.

### Solution

Chaque `Response` porte un `RoutingContext` complet, populé dès la création du `Message` entrant.

```python
class RoutingContext:
    channel: str            # "telegram" | "discord" | "cli"
    bot_id: str             # identifiant du bot qui doit répondre
    chat_id: str            # chat_id Telegram / guild+channel Discord
    thread_id: str | None   # forum thread, Discord thread
    reply_to_message_id: str | None  # threading natif Telegram/Discord
    user_id: str
    session_id: str
```

**Population à l'entrée (dans `normalize()`) :**

```python
def normalize(self, update: TelegramUpdate) -> Message:
    return Message(
        ...
        routing=RoutingContext(
            channel="telegram",
            bot_id=self.bot_id,
            chat_id=str(update.message.chat.id),
            thread_id=str(update.message.message_thread_id) if update.message.is_topic_message else None,
            reply_to_message_id=str(update.message.message_id),
            user_id=str(update.message.from_user.id),
            session_id=self.make_session_id(update),
        )
    )
```

**Vérification à la sortie (dans l'Adapter outbound) :**

```python
async def send(self, response: Response) -> None:
    ctx = response.routing
    assert ctx.channel == self.channel, f"Wrong channel: {ctx.channel}"
    assert ctx.bot_id == self.bot_id,   f"Wrong bot: {ctx.bot_id}"
    await self.bot.send_message(
        chat_id=ctx.chat_id,
        text=response.content,
        message_thread_id=ctx.thread_id,
        reply_to_message_id=ctx.reply_to_message_id,
    )
```

### Implementation — ✅ Shipped (#152)

- [x] `RoutingContext` dataclass in `src/lyra/core/message.py`
- [x] Population in TelegramAdapter + DiscordAdapter normalize()
- [x] Outbound verification (channel + bot_id) in each adapter
- [x] Propagation RoutingContext from InboundMessage → Response

---

## #commands — CommandParser + ComplexityEstimator

### Problème

Sans parsing de commandes, `/imagine`, `!help`, `/config` sont traités comme du texte brut par le LLM — pas de routing vers les bons skills/agents, pas d'optimisation de modèle.

### CommandParser

```python
PREFIXES = ['/', '!']

class CommandContext:
    prefix: str       # "/" ou "!"
    name: str         # "imagine", "help", "config"
    args: str         # reste du message après le nom
    raw: str          # texte original complet

class CommandParser:
    def parse(self, text: str) -> CommandContext | None:
        for prefix in PREFIXES:
            if text.startswith(prefix):
                parts = text[1:].split(None, 1)
                return CommandContext(
                    prefix=prefix,
                    name=parts[0].lower(),
                    args=parts[1] if len(parts) > 1 else "",
                    raw=text,
                )
        return None
```

**Routing des commandes :**

```python
COMMAND_ROUTING = {
    "imagine": ("image_agent", "image_pool"),
    "config":  ("admin_agent", "admin_pool"),
    "help":    ("lyra",        "default_pool"),
    "voice":   ("lyra",        "voice_pool"),
}
```

### ComplexityEstimator

Sélection du modèle selon la complexité du message — évite d'utiliser un modèle lourd pour "bonjour".

```python
class ComplexityLevel(Enum):
    LOW    = "low"     # Haiku / Qwen-fast
    MEDIUM = "medium"  # Sonnet
    HIGH   = "high"    # Opus / Qwen full

class ComplexityEstimator:
    def estimate(self, msg: Message) -> ComplexityLevel:
        signals = [
            len(msg.content) > 500,
            self._contains_code(msg.content),
            len(msg.attachments) > 0,
            msg.command and msg.command.name in HIGH_COMPLEXITY_CMDS,
            msg.session_turn_count > 10,
            self._contains_question_chain(msg.content),
        ]
        score = sum(signals)
        if score == 0:
            return ComplexityLevel.LOW
        if score <= 2:
            return ComplexityLevel.MEDIUM
        return ComplexityLevel.HIGH

COMPLEXITY_TO_MODEL = {
    ComplexityLevel.LOW:    LLMConfig(provider="anthropic", model="claude-haiku-4-5-20251001"),
    ComplexityLevel.MEDIUM: LLMConfig(provider="anthropic", model="claude-sonnet-4-6"),
    ComplexityLevel.HIGH:   LLMConfig(provider="anthropic", model="claude-opus-4-6"),
}
```

### À implémenter

- [ ] `CommandParser` + `CommandContext`
- [ ] Table de routing commandes → (agent_id, pool_id)
- [ ] `ComplexityEstimator` avec signaux configurables
- [ ] `COMPLEXITY_TO_MODEL` mapping dans config
- [ ] Intégration dans `Router.dispatch()`
- [ ] Upgrade dynamique possible si l'agent détecte en cours de génération qu'il a besoin de plus de puissance

---

## #memory-isolation — Isolation + Métadonnées

### Problème

Sans partition stricte par `user_id`, un bug ou une requête mal construite pourrait retourner des souvenirs d'un autre utilisateur. Sans métadonnées, impossible de faire du housekeeping (purge, stats, audit).

### Schéma MemoryEntry étendu

```python
class MemoryEntry:
    # --- Identité ---
    id: UUID
    user_id: str            # ← partition key ABSOLUE, jamais omis

    # --- Sessions ---
    session_id_created: str
    session_id_modified: str

    # --- Contenu ---
    level: MemoryLevel      # L1 → L5
    content: str
    embedding: bytes        # sqlite-vec (L4 uniquement)
    tags: list[str]

    # --- Métadonnées ---
    created_at: datetime
    updated_at: datetime
    count_usage: int        # incrémenté à chaque retrieve
    count_edits: int        # incrémenté à chaque write/update
    confidence: float       # score de fiabilité (0.0 → 1.0)
    ttl: datetime | None    # expiry auto (L1/L2)
    source: str             # "user" | "agent" | "system"
```

### Règle d'isolation SQL (non négociable)

```sql
-- Toute requête mémoire doit inclure user_id :
SELECT * FROM memory
WHERE user_id = :user_id        -- isolation absolue
  AND level IN (3, 4)           -- scope demandé
  AND (ttl IS NULL OR ttl > datetime('now'))
ORDER BY count_usage DESC, updated_at DESC
LIMIT 20;
```

**Jamais de requête globale sans filtre `user_id`.** Même pour les stats, aggréger par user.

### Stockage par niveau

| Niveau | Isolation |
|--------|-----------|
| L1 Working | `dict` en mémoire, scopé par `pool_id` |
| L2 Session | Store keyed by `(user_id, session_id)` |
| L3 Episodic | `~/.lyra/memory/episodic/{user_id}/YYYY-MM-DD/` — user_id dans le path |
| L4 Semantic | SQLite, `WHERE user_id = ?` obligatoire sur toutes les requêtes |
| L5 Procedural | Global (skills = capacités agent, pas de données user) |

### Mise à jour des compteurs

```python
async def retrieve(self, user_id: str, query: str, level: MemoryLevel) -> list[MemoryEntry]:
    entries = await self._search(user_id, query, level)
    for entry in entries:
        await self._increment_usage(entry.id)  # count_usage + 1
    return entries

async def write(self, user_id: str, content: str, level: MemoryLevel, session_id: str) -> MemoryEntry:
    existing = await self._find_similar(user_id, content)
    if existing:
        existing.content = content
        existing.count_edits += 1
        existing.updated_at = datetime.utcnow()
        existing.session_id_modified = session_id
        await self._save(existing)
        return existing
    return await self._create(user_id, content, level, session_id)
```

### Issue #83 — Extension

L'issue existante `#83` (Three-Layer Memory System) doit être étendue pour inclure :
- [ ] Schéma `MemoryEntry` avec tous les champs métadonnées ci-dessus
- [ ] Contrainte SQL `user_id` sur toutes les requêtes L4
- [ ] Structure de path `{user_id}/` pour L3
- [ ] `count_usage` + `count_edits` incrémentés automatiquement
- [ ] TTL auto-purge pour L1/L2
- [ ] Endpoint stats par user (usage, taille, dernière activité)

---

## Priorités suggérées

| Issue | Priorité | Taille | Dépendances |
|-------|----------|--------|-------------|
| `#auth` | **P0** | S | — |
| `#routing` | **P0** | M | `#auth` |
| `#memory-isolation` | **P1** | M (extend #83) | — |
| `#commands` | **P1** | M | `#routing` |

`#auth` en premier — tout le reste s'appuie sur `TrustLevel` dans le `Message`.
