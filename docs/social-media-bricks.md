# Briques élémentaires — Social Media Automation

> **Légende** : ✅ Existe | 🔨 Partiel | ❌ Manquant
> Dernière mise à jour : 2026-03-07

---

## Couche 1 — Identité / Persona

| Brique | Statut | Note |
|--------|--------|------|
| Persona config (nom, bio, valeurs, niche, ton) | ❌ | JSON/YAML par persona |
| Avatar generator (photo de profil IA) | ❌ | SD / DALL-E / Flux |
| Header image generator | ❌ | |
| Style guide (vocabulaire, règles d'écriture) | ❌ | Prompt système par persona |
| Voice profile (lien vers voicecli) | ✅ | voicecli.toml |
| Persona context injector (injecte persona dans chaque LLM call) | ❌ | |

---

## Couche 2 — Création de compte

| Brique | Statut | Note |
|--------|--------|------|
| Browser automator | 🔨 | Playwright existe (twitter scraper 2ndBrain) |
| Phone verifier (SMS virtuel) | ❌ | Twilio / TextNow / 5sim |
| Email manager (comptes dédiés) | ❌ | |
| Proxy manager (résidentiel) | ❌ | |
| Account config store (credentials chiffrés) | ❌ | |
| Account creator (X / LinkedIn / IG / YT) | ❌ | 1 par plateforme |

---

## Couche 3 — Chauffage de compte

| Brique | Statut | Note |
|--------|--------|------|
| Behavior simulator (délais humains, scroll) | ❌ | |
| Follow manager (suivre comptes niche) | ❌ | |
| Like manager (liker posts niche) | ❌ | |
| Feed browser (parcourir le feed, dwell time) | ❌ | |
| Warmup scheduler (montée progressive) | ❌ | Plan sur 2-6 semaines |
| Shadowban detector | ❌ | |

---

## Couche 4 — Génération de contenu

| Brique | Statut | Note |
|--------|--------|------|
| Topic picker (depuis knowledge base) | 🔨 | KB existe, picker à coder |
| Trend scraper (trending niche) | ❌ | X API / Google Trends |
| Text generator (post / thread / article) | ✅ | Claude |
| Format adapter (même contenu → X / LinkedIn / YT / IG) | ❌ | |
| Hashtag generator | ❌ | |
| Image generator | ❌ | Flux / SDXL / DALL-E |
| Image-to-post composer (texte + image) | ❌ | |
| Audio generator | ✅ | voiceCLI |
| Video generator (texte + voix + visuels) | ❌ | MoviePy / Remotion |
| Thumbnail generator | ❌ | |
| Caption generator (sous-titres vidéo) | 🔨 | Whisper dans voiceCLI |

---

## Couche 5 — Scheduling / Publication

| Brique | Statut | Note |
|--------|--------|------|
| Content queue (file d'attente de posts) | ❌ | SQLite simple |
| Content calendar | ❌ | |
| Optimal timing engine (quand poster) | ❌ | |
| X / Twitter poster | ❌ | tweepy + API v2 |
| LinkedIn poster | ❌ | LinkedIn API |
| Instagram poster | ❌ | Graph API |
| YouTube uploader | ❌ | YouTube Data API v3 |

---

## Couche 6 — Engagement / Réaction

| Brique | Statut | Note |
|--------|--------|------|
| Reply reader (lire les réponses) | ❌ | |
| Reply generator (Claude → réponse contextuelle) | ✅ | Claude |
| Reply poster | ❌ | par plateforme |
| Like responder (liker les réponses reçues) | ❌ | |
| DM reader + responder | ❌ | |
| Mention monitor | ❌ | |
| Trend monitor (sujets chauds niche) | ❌ | |

---

## Couche 7 — Analytics

| Brique | Statut | Note |
|--------|--------|------|
| Performance tracker (vues, likes, follows) | ❌ | |
| Best content analyzer | ❌ | Feed → content strategy |
| Follower growth tracker | ❌ | |
| Sentiment analyzer | ❌ | |
| A/B test manager | ❌ | |

---

## Couche 8 — Infrastructure transverse

| Brique | Statut | Note |
|--------|--------|------|
| Credential store (chiffré, par compte) | 🔨 | Vault partiel |
| Rate limiter (respecter les quotas API) | ❌ | |
| Retry / error handler | 🔨 | Circuit breaker dans 2ndBrain |
| Session manager (tokens, refresh) | ❌ | |
| Anti-detection layer (headers, user-agents, timing) | ❌ | |
| Audit logger | ❌ | |

---

## Bilan

| Statut | Nombre |
|--------|--------|
| ✅ Existe | **4** |
| 🔨 Partiel | **5** |
| ❌ Manquant | **~40** |

---

## Priorité de construction

| # | Brique | Débloque |
|---|--------|---------|
| 1 | **Persona config** | Tout le reste a une identité |
| 2 | **X poster** (tweepy + API v2) | Premier contenu en production |
| 3 | **Topic picker** | Branch knowledge base → pipeline |
| 4 | **Format adapter** | 1 contenu → 4 plateformes |
| 5 | **Content queue** | Scheduling sans blocage |
