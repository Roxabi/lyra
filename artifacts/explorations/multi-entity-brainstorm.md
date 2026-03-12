# Multi-Entity Brainstorming — Méthode

> Faire interagir N entités IA distinctes avec des voix différentes,
> chacune lisant les vrais échanges et y réagissant.
> Sortie : un MP3 unique envoyé sur Telegram.

---

## Concept

Pas de simulation. Chaque entité est une vraie instance Claude CLI avec :
- Son propre **system prompt** (personnalité, angle, contraintes)
- Sa propre **voix Qwen** (frontmatter TTS par entité)
- La **lecture de l'historique réel** avant chaque réponse

Le script orchestre les tours, génère l'audio de chaque intervention,
concatène tout dans l'ordre, et envoie le MP3 final sur Telegram.

---

## Architecture

```
Round 1 :  Lyra → Axiom → Vera      (positions initiales)
Round 2 :  Lyra → Axiom → Vera      (réactions croisées)
Round 3 :  Lyra → Axiom → Vera      (convergence)

Pour chaque tour :
  1. get_response()   → Claude CLI   → texte (~80-100 mots)
  2. generate_audio() → voicecli     → WAV (frontmatter par entité)

Fin :
  3. ffmpeg concat    → WAV unique
  4. ffmpeg convert   → MP3 192k
  5. curl             → Telegram sendAudio
```

---

## Définition d'une entité

Chaque entité a 3 blocs :

```python
{
    "name": "Vera",
    "voice": "Sohee",           # pour les logs

    # Personnalité TTS — injectée en frontmatter markdown
    "tts": {
        "language": "French",
        "voice": "Sohee",
        "accent": "Accent français avec une expressivité chaleureuse",
        "personality": "Voix féminine, enthousiaste et créative...",
        "speed": "Débit vivant et varié, accélérations sur les idées excitantes",
        "emotion": "Enthousiasme authentique, curiosité pétillante",
        "segment_gap": 250,
    },

    # Personnalité LLM — injectée en system prompt Claude CLI
    "system": "Tu es Vera, une entité IA créative et disruptive..."
}
```

**Règle** : `tts` contrôle comment elle *parle*. `system` contrôle ce qu'elle *pense*.

---

## Voix Qwen disponibles

| Voix | Profil suggéré |
|------|---------------|
| Ono_Anna | Intellectuelle, posée, légèrement réservée — bonne en français |
| Sohee | Féminin, doux — bonne en français ✅ (remplace Serena) |
| Dylan | Masculin, direct, incisif — acceptable en français |
| Vivian | Féminin, affirmé |
| Eric | Masculin, posé |
| Ryan | Masculin, neutre (défaut) |
| Aiden | Masculin, jeune |
| Uncle_Fu | Masculin, grave |
| Serena | ⚠️ Accent anglais prononcé — éviter pour contenu français |

---

## Points techniques clés

### 1. Nested session — CLAUDECODE

Claude Code bloque les sous-processus Claude CLI par défaut.
Fix : filtrer la variable d'environnement avant le subprocess.

```python
env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
subprocess.run(["claude", "--print", ...], env=env)
```

Source : même pattern que `telegram_bot/core/claude_pool.py`.

### 2. Frontmatter .md par entité + flag `--voice`

`plain = true` dans voicecli.toml ignore les directives per-section,
mais le **frontmatter est toujours appliqué** pour les champs comme `personality`, `accent`, etc.

**⚠️ Bug voicecli** ([issue #11](https://github.com/Roxabi/voiceCLI/issues/11)) :
le champ `voice` du frontmatter est ignoré si voicecli.toml définit déjà une voix par défaut.
Fix : passer `--voice` en flag CLI (priorité absolue).

```python
cmd = ["voicecli", "generate"]
if voice:
    cmd += ["--voice", voice]
cmd.append(str(md_file))
subprocess.run(cmd, cwd=str(VOICECLI_DIR))
```

Le `.md` par tour contient le frontmatter complet de l'entité (personnalité, accent, émotion, speed) :

```python
fm_lines = ["---"]
for k, v in tts_config.items():
    if isinstance(v, str):
        fm_lines.append(f'{k}: "{v}"')
    else:
        fm_lines.append(f"{k}: {v}")
fm_lines.append("---")
fm_lines.append("")
fm_lines.append(text)
md_file.write_text("\n".join(fm_lines))
```

### 3. Détection des WAVs générés

voicecli nomme les fichiers `{stem}_{engine}_{voice}_{lang}_{date}_{time}_001.wav`.
Le script snapshot le timestamp avant génération et cherche les nouveaux fichiers :

```python
ts_before = time.time()
# ... voicecli generate ...
new_wavs = sorted(
    [p for p in OUTPUT_DIR.glob(f"{stem}*.wav") if p.stat().st_mtime >= ts_before],
    key=lambda p: p.name,
)
```

### 4. Concaténation ffmpeg

```bash
# Liste des fichiers dans l'ordre des tours
file '/path/turn_000_lyra_....wav'
file '/path/turn_001_axiom_....wav'
...

ffmpeg -f concat -safe 0 -i list.txt -c copy combined.wav
ffmpeg -i combined.wav -codec:a libmp3lame -b:a 192k combined.mp3
```

### 5. Envoi Telegram

```bash
curl -X POST "https://api.telegram.org/bot{TOKEN}/sendAudio" \
  -F chat_id={CHAT_ID} \
  -F audio=@combined.mp3 \
  -F title="Brainstorming — Lyra × Axiom × Vera" \
  -F performer="Lyra, Axiom, Vera"
```

Credentials lus depuis `~/.claude/.env` :
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_ID`

---

## Script

`~/projects/voiceCLI/TTS/texts_in/brainstorm_entities.py`

Lancer depuis `~/projects/voiceCLI/` :

```bash
cd ~/projects/voiceCLI && python3 TTS/texts_in/brainstorm_entities.py
```

---

## Paramètres à ajuster

| Paramètre | Valeur actuelle | Description |
|-----------|----------------|-------------|
| `ROUNDS` | 3 | Nombre de tours (3 rounds × 3 entités = 9 turns) |
| `ENTITIES` | Lyra, Axiom, Vera | Ajouter/modifier les entités |
| `max_tokens` | 250 | Longueur max par réponse Claude |
| `timeout` | 60s | Timeout par appel CLI |

---

## Créer un nouveau brainstorming

1. Copier le script : `cp brainstorm_entities.py brainstorm_new_topic.py`
2. Modifier `ENTITIES` (noms, voix, system prompts, tts config)
3. Modifier le `user_msg` initial dans `get_response()` (le sujet + contexte de départ)
4. Lancer : `python3 brainstorm_new_topic.py`

### Bonnes pratiques pour le system prompt

Toujours ancrer les entités dans la **réalité du projet** au moment du brainstorm :

```
CONTEXTE RÉEL :
- Ce qu'on a (acquis)
- Ce qu'on n'a pas encore (manques)
- Les contraintes non-négociables
- L'enjeu existentiel (urgence, stakes)

QUESTION DU BRAINSTORM :
Une question concrète et bornée dans le temps.
```

Sans ce contexte, les entités débattent dans l'abstrait
et supposent une situation plus avancée que la réalité.

---

*Créé le 2026-03-07 — mis à jour le 2026-03-07 (Sohee pour Vera, fix voice CLI, contexte survie).*
