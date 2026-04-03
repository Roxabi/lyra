# Production Supervisor Programs

This directory contains supervisor configs for **production only** (roxabituwer, RTX 3080 10GB).

## Intentionally absent (local-only)

| Program | Reason |
|---------|--------|
| `imagecli_gen` | Requires >10GB VRAM, not available on RTX 3080 |
| `forge` | Local dev tooling only (diagram server) |
| `idna` | Local dev tooling only |

These programs run via the root supervisor hub (`~/projects/conf.d/`) on the local machine only.
