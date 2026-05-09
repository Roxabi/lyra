# Fumadocs meta.json validation — 2026-05-09

## Site location

No Fumadocs site found in this repository. `/home/mickael/projects/lyra/package.json`
contains only a bun dashboard script — no Next.js, no `fumadocs-*` dep. `roxabi-site`
exists but is an empty repo (only `.git` and `.gitignore`). The `meta.json` files in
`docs/architecture/adr/` are authored for a future Fumadocs site, not a currently
buildable one.

**Conclusion:** No build target exists today; the files are speculative/forward-looking.

## Separator syntax check

**Official Fumadocs syntax** (confirmed from `fumadocs.dev` docs and source):
- Plain separator: `"---"`
- Labeled separator: `"---Label---"`
- Labeled + icon: `"---[Icon]Label---"`

Source: https://www.fumadocs.dev/docs/headless/page-conventions
Source: https://github.com/fuma-nama/fumadocs/blob/dev/apps/docs/content/docs/headless/page-conventions.mdx

**What we wrote:**

```json
"---Messaging & NATS---",
"---LLM, Streaming & Agents---",
"---Adapters---",
...
```

**Verdict: COMPATIBLE**

The `---Label---` pattern is exactly the documented syntax for labeled sidebar
separators. Fumadocs renders them as visual section dividers with the label text.
No error or warning is expected.

## Orphan check

0 orphan entries.

All 46 page slugs in `meta.json` map 1-to-1 to `.mdx` files in
`docs/architecture/adr/`. Diff between actual filesystem and `pages[]` is empty.

- Slugs in `pages[]`: 46
- `.mdx` files in directory: 46 (excluding `archive/` subdirectory)
- Separators: 10 (ignored by check)

## Build check

No build attempted. No Fumadocs/Next.js site exists in this repo or `roxabi-site`.
Build is not possible without a site entrypoint. This is expected given the current
state of `roxabi-site` (empty repo).

## Recommendation

Keep as-is. The `---Label---` separator syntax is officially supported by Fumadocs.
Zero orphans. When a Fumadocs site is eventually wired up to consume these `.mdx` files,
the `meta.json` will render correctly with 10 labeled section headers grouping the 46
ADRs by domain. No changes required.
