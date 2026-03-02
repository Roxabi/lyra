# Voice Pipeline — Architecture & Décisions

> Lyra status: **P1 — À explorer, POC requis avant implémentation**
> Créé: 2026-03-02

---

## Décision fondamentale : STT-TTS vs STS

### STS — Speech-to-Speech (bout-en-bout)
Un seul modèle : vocal entrant → vocal sortant directement.
- Exemples : Moshi (Kyutai), GPT-4o Realtime, Gemini Live
- Latence : 200-500ms
- **Verdict : écarté** — on perd toute l'intelligence de Lyra (mémoire, skills, raisonnement). Acceptable pour un assistant conversationnel générique, pas pour Lyra.

> Note : `voicecli listen` utilise déjà Moshi localement — mais c'est micro local uniquement, pas remote.

### STT → LLM → TTS (retenu)
Pipeline en trois étapes :
1. **STT** : vocal → texte (faster-whisper + VAD)
2. **LLM** : texte → réponse texte streaming (Claude)
3. **TTS** : texte → audio chunked (voicecli qwen-fast)

Latence actuelle (séquentiel naïf) : 8-15s
Latence cible (pipeline streaming) : **1.5-2.5s perçue**

On garde toute l'intelligence : mémoire, skills, raisonnement complexe.

---

## Optimisation principale : streaming en pipeline

### Principe

Au lieu d'attendre la fin de chaque étape :

```
Claude stream → phrase 1 prête → TTS phrase 1 → envoi chunk 1
               → phrase 2 prête → TTS phrase 2 → envoi chunk 2
               ...
```

L'utilisateur entend la première phrase (~1.5s) pendant que Claude génère encore la suite.

### Implémentation

```python
async def voice_response_pipeline(user_audio: bytes) -> AsyncIterator[bytes]:
    # 1. STT (streaming avec VAD)
    text = await stt.transcribe(user_audio)

    # 2. Pré-classification émotionnelle (voir section suivante)
    instruct = await classify_emotional_tone(text)

    # 3. Buffer 2-3 phrases puis pipeline
    buffer = []
    async for token in llm.stream(text):
        buffer.append(token)
        sentence = "".join(buffer)
        if is_sentence_end(sentence):
            audio_chunk = await tts.generate(sentence, instruct=instruct)
            yield audio_chunk
            buffer = []
```

---

## Problème de cohérence émotionnelle

### Le problème
Si chaque phrase est envoyée à voicecli sans instruct partagé, chaque appel conditionne le modèle indépendamment → incohérence : ton grave sur une phrase, aigu sur la suivante, rythme qui change.

### Solution : instruct global dérivé de l'input

**Avant** d'appeler le LLM, classifier le registre émotionnel du message entrant :

```python
TONE_MAP = {
    "technique":   "Posée, analytique, légèrement enthousiaste sur les sujets techniques",
    "affectif":    "Chaleureuse, attentionnée, douce",
    "urgent":      "Directe, calme, efficace",
    "ludique":     "Légère, espiègle, avec des petits rires intérieurs",
    "neutre":      "Débit mesuré, posée, intellectuelle",
}

async def classify_emotional_tone(user_message: str) -> str:
    # Classification légère (~100ms) — heuristique ou SLM 1B
    category = await tone_classifier.classify(user_message)
    return TONE_MAP[category]
```

Cet instruct est injecté en frontmatter de **tous** les chunks TTS de la réponse → cohérence garantie de la première à la dernière phrase.

**Complément** : buffer les 2-3 premières phrases de la réponse LLM pour affiner l'instruct si nécessaire (+800ms sur le premier chunk, mais cohérence renforcée).

### voicecli gère déjà ça via le frontmatter

Le frontmatter d'un script `.md` propage `personality`, `speed`, `emotion` à tous les segments :

```markdown
---
emotion: "Posée, analytique, légèrement enthousiaste"
speed: "Débit mesuré"
---

Première phrase...

Deuxième phrase...
```

En génération streaming, on génère un `.md` avec frontmatter global + une section par phrase → cohérence native.

---

## Interface live : options pour "parler à Lyra"

### Option A — Discord voice channel (recommandée court terme)

Discord est déjà dans la stack Lyra prévue. `discord.py` supporte les voice channels :
- `discord.sinks` pour capturer l'audio du micro utilisateur
- VAD pour détecter la fin de phrase
- Pipeline STT→LLM→TTS→play dans le salon

**Avantage** : pas de nouvelle infra, dans la roadmap de toute façon.
**Inconvénient** : round-trip Discord + latence perçue ~2-3s.

### Option B — Interface web WebSocket

FastAPI + WebSocket côté Machine 1 :
- Micro navigateur → stream PCM → STT en temps réel
- Claude streaming → TTS streaming → audio dans le navigateur
- Latence : 1-2s. Le plus fluide techniquement.

**Avantage** : latence minimale, contrôle total.
**Inconvénient** : UI à construire.

### Option C — LiveKit (infra WebRTC)

LiveKit = serveur WebRTC open-source auto-hébergé sur Machine 1.
Ce n'est **pas un modèle** — c'est le tuyau de transport audio/vidéo en temps réel (comme un serveur d'appel vidéo). On y branche les modèles de son choix.

`livekit-agents` SDK : pipelines voix ↔ LLM prêts à l'emploi, intégration native Whisper + Claude + TTS.

**Avantage** : multi-canal (web + mobile + Discord), qualité production, communauté active.
**Inconvénient** : overhead d'infra supplémentaire.

---

## Recommandation

| Horizon | Solution | Raison |
|---------|----------|--------|
| Court terme (P2) | Discord voice + faster-whisper + voicecli | Dans la roadmap, composants déjà disponibles |
| Long terme (P3) | LiveKit sur Machine 1 | Infra propre multi-canal, qualité production |

---

## POC requis avant implémentation

### POC 1 — Pipeline STT→LLM→TTS streaming

**Objectif** : mesurer la latence réelle du pipeline bout-en-bout.

Critères go/no-go :
- Premier chunk audio en < 2.5s après fin de la phrase utilisateur
- Cohérence émotionnelle sur 5 réponses consécutives (écoute humaine)
- Pas de silence perceptible entre les chunks

**Scope** : script Python standalone, pas intégré au hub. Telegram ou terminal.

### POC 2 — Discord voice bot

**Objectif** : valider la capture audio + pipeline dans un salon Discord.

Critères go/no-go :
- Capture propre sans feedback
- VAD détecte correctement la fin de phrase (< 500ms de délai)
- Latence perçue < 3s

**Scope** : bot Discord minimal, un salon, un utilisateur.

---

## Stack technique (non finale, à confirmer par POC)

- **STT** : `faster-whisper` + `silero-vad` (VAD local, léger)
- **LLM** : Claude streaming (API Anthropic) — déjà en place
- **TTS** : `voicecli qwen-fast` + frontmatter global pour cohérence émotionnelle
- **Transport court terme** : `discord.py` voice
- **Transport long terme** : LiveKit + `livekit-agents`
- **Classification émotionnelle** : heuristique simple d'abord, SLM 1B en P3

---

## Lien avec la roadmap

- **P2** : POC 1 (pipeline streaming) en parallèle de la migration Telegram → Lyra hub
- **P2** : POC 2 (Discord voice) une fois le hub stable
- **P3** : LiveKit si Discord voice valide l'usage

> **Prérequis** : hub Phase 1 fonctionnel + adaptateur Telegram stable.
> Ne pas commencer les POCs avant que le hub tourne en production.
