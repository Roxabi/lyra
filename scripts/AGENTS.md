# AGENTS.md — scripts/

## Role

**Platform orchestration** (bash) and **domain operational tooling** for this repo.

| Kind | Language | Examples |
|------|----------|----------|
| Orchestration | **bash only** | `qg`, `check-*-drift.sh` |
| Domain ops entrypoint | **bash** → Python | `check_inbox_prefix.sh` → `.py` |
| Domain ops implementation | **Python** | `gen_nkeys.py`, `render_acl_*.py`, `check_grants.py` |
| Bootstrap | **bash** in `tools/` | `dev-setup.sh` (installs yq, uv sync, hooks) |

## vs `tools/`

| | `scripts/` | `tools/` |
|---|------------|----------|
| **What** | Run the factory (ACL, CI scanners, qg runner) | Quality gates invoked **by** `stack.yml` / `qg` |
| **Who calls** | Makefile, `factory-acl`, pre-push drift | `scripts/qg run` for all `stack.yml` gates (incl. ACL scanners in `ci` stage) |
| **dev-core** | Repo-specific | Canonical pattern from dev-core plugin |

Rule: new **quality gate** → implementation in `tools/`, declaration in `stack.yml`.  
New **ACL/deploy scanner** → `scripts/` (bash entry + Python if needed).

See `docs/ops/quality-gates.md` and `CONTRIBUTING.md` § Language & layout.

---

## Inventory (`scripts/`)

### Orchestration (bash)

| File | Purpose |
|------|---------|
| `qg` | Quality gate runner — reads `.claude/stack.yml` via yq |
| `check-qg-conf-drift.sh` | Drift guard for generated `tools/qg.conf` |
| `check-acl-specs-drift.sh` | Rendered ACL spec vs source |
| `check-acl-authconf-drift.sh` | Authconf vs matrix |

### Domain ops — persistent (Python + bash entry where CI calls directly)

| Entry | Implementation | Purpose |
|-------|----------------|---------|
| `check_inbox_prefix.sh` | `check_inbox_prefix.py` | Inbox prefix uses `identity_name` API |
| `check_subject_literals.sh` | `check_subject_literals.py` | Subject literals resolve (matrix + contracts) |
| — | `check_grants.py` | ACL grant coverage (`factory-acl check grants`) |
| — | `check_acl_matrix_retired.py` | Matrix lifecycle fields (`factory-check-acl-retired`) |
| — | `check_request_reply_flows.py` | Request-reply flow parity (`factory-check-flows`) |
| — | `gen_nkeys.py` | NKey provisioning (deploy / `make nats-*`) |
| — | `render_acl_spec.py`, `render_acl_parity.py` | ACL spec render pipeline |

Shared Python modules (`_loader.py`, `_renderer.py`, `_nk.py`, `_modes.py`, `_effective.py`, `_acl_models.py`) support the ACL/nkeys tooling above — not standalone entrypoints.

### One-offs / evidence (do not treat as gates)

| File | Notes |
|------|-------|
| `goal-1771-*.sh`, `goal-1771-plan-slice.py` | Issue #1771 dashboard evidence — ephemeral |
| `goal-fleet-obs-evidence.sh` | Fleet observability evidence capture |
| `backfill_soul_documents.py` | Data migration — run manually with intent |
| `blob-reconcile.py` | Blobstore reconciliation — operator tool |

When adding a new persistent scanner, prefer a `.sh` wrapper if CI/Makefile invokes it directly; keep parsing in `.py`.