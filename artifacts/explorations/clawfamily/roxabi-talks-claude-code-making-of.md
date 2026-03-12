# Making-of — roxabi-talks-claude-code.html

## Contexte

Présentation **self-contained** (1 seul fichier HTML, ~1570 lignes) créée le **10 mars 2026** via Telegram.
Prompt initial : *"Dans Roxabi talks. Tu saurais me préparer un talks sur les claws. Prépare moi d'abord le storytelling, séquence de sujet, slide etc"*

Session Telegram : `38208e8c-5127-4b43-a1b0-e52b45e3378f`
Fichier : `~/.agent/diagrams/roxabi-talks-claude-code.html`

---

## Démarche

### 1. Prompt unique → génération complète

Pas d'itérations multiples. Un seul prompt a déclenché :
1. **Storytelling** — structuration narrative en 15 slides avec arc narratif (contexte → shift → technique → retour d'expérience → futur → CTA)
2. **Design** — choix de la palette, typographie, layout
3. **Code** — génération du HTML/CSS/JS complet
4. **Ouverture** — le fichier a été ouvert dans le browser directement

### 2. Skill utilisé

**`visual-explainer:generate-slides`** — skill du plugin `visual-explainer` (roxabi-plugins) qui génère des slide decks HTML standalone. À ne pas confondre avec `frontend-slides` qui est un skill séparé (animations riches, conversion PPTX). Le skill :
- Prend un sujet + contexte
- Structure le storytelling en slides
- Génère un fichier HTML unique avec CSS + JS embarqués
- Ouvre le résultat dans le navigateur

### 3. Connaissance du contexte

Le bot Telegram avait accès au CLAUDE.md du projet 2ndBrain, ce qui lui a permis de :
- Connaître l'architecture réelle (bot, knowledge DB, skills, voice)
- Injecter des exemples concrets (les vrais skills `/agenda`, `/knowledge`, `/voice`...)
- Référencer le vrai setup (supervisord, CLI pool, sqlite-vec)
- Utiliser le branding Roxabi

---

## Forme

### Design system

| Élément | Choix |
|---------|-------|
| **Palette** | Dark theme — `#0D0D0D` bg, violet `#7C3AED` accent primaire, cyan `#06B6D4` accent secondaire |
| **Typographie** | Space Grotesk (titres/corps) + JetBrains Mono (code/données) — Google Fonts |
| **Layout** | Full viewport slides (`100vw × 100vh`), absolute positioning |
| **Grille** | Background grid subtile violet 4% opacity sur certaines slides |

### Composants visuels récurrents

- **Cards** — `.card`, `.card-violet`, `.card-cyan`, `.card-warning`, `.card-danger` avec bordures colorées
- **Pills** — badges arrondis avec icônes SVG inline
- **Code blocks** — terminal-style avec dots rouge/jaune/vert et syntax highlighting CSS
- **Stat cards** — chiffres géants avec gradient violet→cyan
- **Warning cards** — bordure gauche orange/rouge, icône + titre + description
- **Divider** — barre 48px gradient violet→cyan

### Diagrammes SVG inline

4 diagrammes SVG dessinés à la main dans le HTML :
1. **Slide 4** — Hub-and-spoke : Claude Code au centre, spokes vers filesystem/git/bash/APIs/web/MCP
2. **Slide 6** — Arbre de worktrees : main → 3 branches avec agents séparés
3. **Slide 8** — Multi-agents : orchestrateur central + 5 agents spécialisés (architect, backend, frontend, tester, security)
4. **Slide 10** — Architecture 2ndBrain complète : Telegram → Bot Core → Knowledge DB / Skills / Voice TTS / Claude Code / CLAUDE.md

### Animations CSS (zéro JS pour les animations)

| Animation | Usage |
|-----------|-------|
| `float-up` | Révélation des éléments au chargement de slide (cascade avec `delay-1` à `delay-7`) |
| `blink` | Curseur terminal (cover + outro) |
| `glow-pulse` | Halo violet sur éléments importants |
| `draw-line` | Lignes SVG qui se dessinent progressivement (hub-and-spoke) |
| `scale-in` | Apparition avec zoom |
| `slide-right` | Glissement latéral |

### Navigation (JS)

- **Clavier** : ←/→, ↑/↓, Space, PageUp/PageDown
- **Clic** : moitié droite = next, moitié gauche = prev
- **Touch/swipe** : détection de swipe >50px
- **Fullscreen** : touche F ou bouton
- **Progress bar** : barre gradient en bas, largeur proportionnelle
- **Compteur** : `1 / 15` en bas à droite
- **Transitions** : slide horizontale avec opacity (500ms ease)

---

## Contenu — 15 slides

### Arc narratif

```
Accroche (1-3) → Technique (4-9) → Expérience (10-11) → Honnêteté (12-13) → Vision (14-15)
```

| # | Titre | Type | Contenu clé |
|---|-------|------|-------------|
| 1 | **Cover** | Accroche | "Claude Code" + curseur animé + "L'IA qui code AVEC toi" |
| 2 | **Contexte** | Timeline | Copilot (2021) → ChatGPT (2023) → Claude Code (2025) — d'outil passif à agent actif |
| 3 | **Le Shift** | Citation | "Ce n'est plus un assistant. C'est un collaborateur." + pills capacités |
| 4 | **Architecture** | Technique | Hub-and-spoke SVG + 3 modes (Normal/Plan/Auto) |
| 5 | **CLAUDE.md** | Technique | Split code highlight + explications — mémoire persistante du projet |
| 6 | **Worktrees** | Technique | Arbre SVG 3 branches + "3 agents, 3 branches, 0 conflit" |
| 7 | **Skills** | Technique | Grille 3×3 de skills + code SKILL.md + marketplace Roxabi |
| 8 | **Multi-agents** | Technique | SVG orchestrateur + 5 agents + pills (parallèle/isolé/agrégé) |
| 9 | **Hooks** | Technique | Code settings.json + types de hooks + cas d'usage (lint/audit/Slack) |
| 10 | **2ndBrain** | Démo | Architecture complète SVG : Telegram → Bot → Knowledge/Skills/Voice/Claude |
| 11 | **Résultats** | Stats | 3 prompts / 15 skills / 1 semaine — "Le temps n'est plus passé à chercher" |
| 12 | **Pièges** | Warning | 4 cartes : context window, coût API, sur-engineering, sécurité |
| 13 | **Limites** | Honnêteté | 3 limites : pas de mémoire longue, pas de vision runtime, latence gros repos |
| 14 | **Futur** | Vision | Timeline 2025-2027 + "Le code n'est plus écrit, il est négocié" |
| 15 | **Outro** | CTA | 3 étapes (install, CLAUDE.md, /dev) + "Questions ?" |

### Choix éditoriaux

- **Français** — public francophone visé
- **Ton direct** — phrases courtes, citations percutantes
- **Honnêteté** — 2 slides dédiées aux pièges et limites (pas juste du hype)
- **Concret** — exemples tirés du vrai setup (2ndBrain, skills réels, architecture réelle)
- **Durée** — calibré pour ~30 min de talk live

---

## Résumé technique

| Aspect | Détail |
|--------|--------|
| **Fichier** | 1 HTML, ~1570 lignes, ~25K tokens |
| **Dépendances** | Google Fonts uniquement (offline après chargement) |
| **CSS** | ~685 lignes (custom properties, animations, responsive) |
| **JS** | ~85 lignes (navigation, transitions, fullscreen, touch) |
| **SVG** | 4 diagrammes inline |
| **Skill** | `visual-explainer:generate-slides` (output dans `~/.agent/diagrams/`) |
| **Temps de génération** | ~12 min (une seule passe) |
| **Itérations** | 0 — prompt unique → résultat final |
