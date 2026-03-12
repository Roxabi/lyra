# claude-qmd-sessions — Session Memory via Semantic Search

> Source: https://github.com/wbelk/claude-qmd-sessions
> Tier: 2 (Reference)
> Local clone: ~/projects/external_repo/memory/claude-qmd-sessions/
> Visual: [claude-qmd-sessions-architecture.html](./claude-qmd-sessions-architecture.html)

## Summary

claude-qmd-sessions is a Claude Code skill that converts raw JSONL session transcripts into clean, searchable markdown files and indexes them in [qmd](https://github.com/tobi/qmd) for semantic + keyword search via MCP. It hooks into Claude Code's lifecycle (PreCompact, SessionEnd, SessionStart) to automatically keep the index up to date and restore context after compaction or in fresh sessions.

**Core pipeline:** JSONL transcripts → markdown files (organized by project/date/slug) → qmd index (BM25 + vector embeddings) → MCP retrieval (deep_search, search, get, multi_get).

The system solves the "context loss" problem: when Claude Code compacts context or starts a new session, prior conversations are lost. This tool preserves them as searchable documents and can automatically reload recent conversation turns.

## Key Components

| File | Role | Lines |
|------|------|-------|
| `hook.js` | Entry point for all Claude Code hooks. Reads stdin JSON (session_id, hook_event_name, source, cwd), branches on event type, orchestrates conversion and context restoration. | ~121 |
| `convert-sessions.js` | Bulk or scoped JSONL-to-markdown conversion. Reads `~/.claude/projects/`, extracts user/assistant text, outputs organized markdown. Two modes: full scan (idempotent) and `--session` (always overwrite). | ~321 |
| `lib.js` | Shared utilities: `readConfig()`, `isEmbedRunning()` (pgrep guard), `qmdAvailable()`, `runUpdateAndEmbed()`, `collectRecentTurns()`, `loadClaudeMd()`, `extractTurnsFromFile()`, `cwdToProject()`. | ~162 |
| `refresh.js` | Manual context restoration via `/qmd-sessions refresh`. Outputs CLAUDE.md files + recent turns to stdout. | ~28 |
| `SKILL.md` | Interactive setup wizard (8 steps): output dir, conversion, verification, Bun/qmd install, collection, embeddings, MCP server, hooks, CLAUDE.md guidance. | ~246 |
| `config.json` | Persisted configuration: `outputDir` (markdown destination), `loadContextOnStartup` flag. | ~3 |

## Memory Pipeline

### Stage 1: Raw Session Data
- Location: `~/.claude/projects/{project-dir}/`
- Format: JSONL (one line per message), with subagent directories (`{sessionId}/subagents/*.jsonl`)
- Compact files are excluded (`*compact*`)

### Stage 2: Hook Triggers
Four hook events configured in `~/.claude/settings.json`:
- **PreCompact**: Fires before Claude Code compacts context. Converts current session to markdown, updates qmd index (update + embed).
- **SessionEnd**: Fires when session exits. Same as PreCompact: convert + index.
- **SessionStart (compact/resume/clear)**: After compaction or resume. Converts session, then outputs CLAUDE.md files + recent turns (up to 50 exchanges / 14K chars) to stdout as `additionalContext` for Claude to ingest.
- **SessionStart (startup)**: Fresh session. Loads CLAUDE.md files. If `loadContextOnStartup` is enabled, also outputs recent turns.

All hooks read stdin JSON: `{ session_id, hook_event_name, source, cwd }`.

### Stage 3: JSONL → Markdown Conversion
`convert-sessions.js` performs the extraction:
1. **Metadata peek**: Reads first 32KB via `peekFields()` using regex — fast extraction of sessionId, slug, date, cwd without parsing the entire file.
2. **Full parse**: Iterates JSONL lines, calls `extractText()` on user/assistant messages.
3. **Content filtering**: Only `type: "text"` content blocks are extracted. `tool_use`, `tool_result`, and `thinking` blocks are implicitly skipped. `<system-reminder>` tags are stripped.
4. **Output format**: Markdown with YAML-like header (date, project, branch, session ID) followed by `## User` / `## Claude` turn sections.
5. **Subagent handling**: Subagent files get `## Task` / `## Subagent` labels and include parent session slug reference.
6. **File naming**: `{project}/{date}-{slug}-{sessionId8}.md` for sessions, `{date}-{parentSlug}-sub-{agentId12}.md` for subagents.
7. **Project naming**: Derived from CWD path (last 2 segments joined by `-`), with directory name fallback.

### Stage 4: Indexing (qmd)
`lib.runUpdateAndEmbed()` orchestrates:
1. `qmd update` — scans the `claude-sessions` collection files into the qmd index (120s timeout)
2. `isEmbedRunning()` — `pgrep -f "qmd.*embed"` check. If another process is embedding, skip.
3. `qmd embed` — generates vector embeddings using local model (~300MB download on first run, ~640MB reranker + ~1.1GB query expansion model downloaded on first query)

### Stage 5: Retrieval (qmd MCP Server)
Configured in `~/.claude.json` as MCP server (`qmd mcp`, stdio transport):
- `mcp__qmd__deep_search` — semantic search with automatic query expansion (default for content queries)
- `mcp__qmd__search` — BM25 keyword search (fast, exact terms)
- `mcp__qmd__get` — full document by path or docid
- `mcp__qmd__multi_get` — glob-based batch retrieval (useful for date queries since BM25 tokenizes dates on hyphens)

### Stage 6: Context Restoration
`lib.collectRecentTurns()` restores conversation context:
1. Walks `outputDir`, finds `*.md` files (excludes subagents via `indexOf('sub')`)
2. Sorts: CWD-matching project files first (descending), then all others (descending)
3. Splits markdown on `## User|Claude|System` headings to get individual turns
4. Collects turns from most recent files, walking backwards within each file
5. Stops at 100 turns (50 exchanges) or 14,000 characters
6. Outputs with header: `[Context restored: ~N exchanges from M sessions]`

## Relevance for Lyra

### Direct Mapping to Lyra's Memory Levels

| claude-qmd-sessions | Lyra Level | Notes |
|----------------------|------------|-------|
| `collectRecentTurns()` output | L0 (Working memory) | Injected into context window on session start |
| JSONL session files | L1 (Session memory) | Raw multi-turn state, not yet consolidated |
| Converted markdown files | L2 (Episodic memory) | Dated, immutable session records |
| qmd index (BM25 + embeddings) | L3 (Semantic memory) | Hybrid search over all sessions |
| CLAUDE.md guidance | L4 (Procedural) | Persistent instructions/preferences |

### Consolidation Flow Alignment
The pipeline maps directly to Lyra's consolidation chain:
- **L1 → L2**: `convert-sessions.js` converts raw session JSONL (L1) into dated markdown (L2). This is the session → episodic consolidation.
- **L2 → L3**: `qmd update + embed` indexes episodic markdown into a searchable semantic store (L3). This is the episodic → semantic consolidation.
- **L3 → L0**: `collectRecentTurns()` and qmd MCP queries pull from L3/L2 back into the working context (L0).

### Hooks Pattern Reusability
The lifecycle hook pattern is highly relevant. Lyra needs equivalent triggers:
- **Bus events** replacing Claude Code hooks: `session.compact`, `session.end`, `session.start`
- **Consolidation triggers**: on session end, consolidate working → session → episodic. On nightly batch, consolidate episodic → semantic.
- **Context restoration**: on channel reconnect or session resume, reload recent context from episodic/semantic stores.

## Actionable Patterns

### 1. Two-Phase Metadata Extraction
The `peekFields()` pattern (read first 32KB, regex for metadata) is excellent for batch processing. Lyra should adopt this for session JSONL scanning — avoid full file parsing when only metadata is needed for routing/skipping decisions.

```
Pattern: peekFields(path) → { sessionId, slug, date, cwd }
Lyra equivalent: peek_session_meta(path) → SessionMeta dataclass
```

### 2. Pgrep Concurrency Guard
Simple but effective: `pgrep -f "qmd.*embed"` before starting embedding. For Lyra's nightly batch consolidation, a similar pattern prevents concurrent embedding processes. However, Lyra should use `asyncio.Lock` per pool instead of process-level pgrep since it runs in a single process.

### 3. CWD-Prioritized Context Restoration
`collectRecentTurns()` sorts by CWD match first, then recency. This is a smart relevance heuristic for Lyra's context restoration: prioritize turns from the same channel/pool before cross-pool turns. The max turns (100) and max chars (14,000) caps are reasonable defaults for context window budgeting.

### 4. Idempotent Bulk + Incremental Modes
Two conversion modes: bulk (skip existing, idempotent) and `--session` (always overwrite current). Lyra's consolidation should follow the same pattern:
- **Nightly batch**: scan all sessions, skip already-consolidated
- **Session end**: always consolidate the current session immediately

### 5. System Tag Stripping
`stripSystemTags()` removes `<system-reminder>` tags. Lyra's consolidation pipeline should strip all system/framework metadata before storing episodic memory — only preserve user-meaningful content.

### 6. Turn-Level Granularity for Context Restoration
Rather than loading entire session files, `extractTurnsFromFile()` splits on `## Role` headings and collects individual turns. This allows mixing turns from multiple sessions within a character budget. Lyra should adopt this for L0 restoration — fill the context window with the most relevant turns, not necessarily complete sessions.

### 7. Hook Output Protocol
The hook outputs structured JSON: `{ systemMessage, hookSpecificOutput: { hookEventName, additionalContext } }`. This separates user-visible status from Claude-ingested context. Lyra's bus events should use a similar separation: status/logging vs. context injection.

## Risks & Limitations

### Not Applicable to Lyra
1. **Node.js / Bun dependency**: Entire codebase is Node.js. Lyra is Python-only. Patterns must be reimplemented, not ported.
2. **qmd as external dependency**: Lyra already has its own hybrid search (BM25 + sqlite-vec). No need for qmd. The embedding model and MCP server are qmd-specific.
3. **File-based architecture**: Intermediate markdown files on disk. Lyra's L2 (episodic) uses dated Markdown files too, but L3 (semantic) goes directly into SQLite. No need for a separate qmd collection layer.
4. **Claude Code hook format**: The stdin JSON protocol and `~/.claude/settings.json` hook configuration are Claude Code-specific. Lyra uses bus events.

### Limitations of the Approach
1. **No summarization/compression**: Sessions are stored verbatim (minus tool blocks). No condensation step. For Lyra, episodic → semantic consolidation should include summarization to reduce storage and improve retrieval quality.
2. **No entity/concept extraction**: Raw text is indexed directly. Lyra should extract entities, decisions, and key concepts during consolidation for structured retrieval.
3. **Subagent exclusion from context restoration**: `collectRecentTurns()` skips subagent files (`indexOf('sub')` check). This loses potentially valuable research context. Lyra should decide per-pool whether subagent/child task context is relevant.
4. **Fixed context budget**: 100 turns / 14K chars is hardcoded. Lyra should make this configurable per channel adapter (Telegram has different constraints than CLI).
5. **No semantic relevance in context restoration**: `collectRecentTurns()` uses recency + project match only. It does not query the semantic index to find the most relevant past turns for the current task. Lyra should combine recency with semantic similarity for smarter context restoration.
6. **Synchronous blocking**: All hooks block until complete, including `qmd embed` (120s timeout). Lyra's consolidation must be async (aiosqlite + asyncio).
7. **No deduplication of content**: If a session is compacted and resumed, the same conversation could be indexed multiple times across continuation files. Lyra should deduplicate by session ID + turn index.

## Priority

**Phase 1** — The consolidation pipeline pattern (session → episodic → semantic) directly maps to Lyra's Phase 1 scope (L0 + L3). Key patterns to adopt immediately:

1. Session-end trigger for consolidation (bus event equivalent of SessionEnd hook)
2. JSONL → structured markdown conversion (adapt for Lyra's session JSONL format)
3. Two-phase metadata extraction (peekFields pattern)
4. CWD/pool-prioritized context restoration with character budgeting
5. Idempotent bulk + incremental modes for consolidation

**Not Phase 1**: qmd integration, MCP server, embedding model management (Lyra has its own sqlite-vec setup). Summarization and entity extraction are Phase 2 enhancements to the basic consolidation pipeline.
