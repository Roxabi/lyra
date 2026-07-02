"""Tests for the _classify_drift shell function in deploy/lib/deploy-common.sh.

Fingerprint format:
  <git_head>:<unit_sha>:<auth_sha>:<voicecli_head>:<staging-svc-digest>:<staging-digest>
  - field 0 (git_head) ALONE   → code-only (git advanced but no artifact changed; converge.sh
                                  resolves inert docs/CI commits via _code_change_is_inert and
                                  src/packages/apps-dashboard/brand-only commits via
                                  _code_change_is_image_carried — restart deferred to the
                                  post-autoupdate digest converge).
                                  git_head + any other field → structural.
  - field 1 (unit_sha)         → structural
  - field 2 (auth_sha)         → auth
  - field 3 (voicecli_head)    → structural
  - field 4 (staging-svc-digest) → structural
  - field 5 (staging-digest)   → structural

Legacy 4-field stamps are normalized to :none:none before comparison.

The function exits 0 in all cases; callers branch on stdout.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_COMMON = REPO_ROOT / "deploy" / "lib" / "deploy-common.sh"

_SVC = "4f9b3264aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001"
_STG = "6a7d28bcaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001"
_FP = f"a:b:c:d:{_SVC}:{_STG}"


def _classify(last: str, current: str) -> str:
    """Invoke _classify_drift via bash and return the stripped stdout."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source {DEPLOY_COMMON} && _classify_drift "{last}" "{current}"',
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "XDG_RUNTIME_DIR": os.environ.get(
                "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
            ),
        },
        timeout=10,
    )
    assert result.returncode == 0, (
        f"_classify_drift exited {result.returncode}; stderr={result.stderr!r}"
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    "last, current, expected",
    [
        # 1. Identical fingerprints → no drift
        (_FP, _FP, "none"),
        # 2. No prior stamp (sentinel "none") → structural (full converge)
        ("none", _FP, "structural"),
        # 3. Only auth field (index 2) differs → auth-only reload
        (_FP, f"a:b:X:d:{_SVC}:{_STG}", "auth"),
        # 4. Field 0 (git_head) ALONE differs → code-only (converge.sh then does a paths git-diff:
        #    docs/CI-only commit → skip; runtime path or undecidable → structural, fail-safe).
        (_FP, f"A:b:c:d:{_SVC}:{_STG}", "code-only"),
        # 5. Field 1 (unit_sha) differs → structural
        (_FP, f"a:B:c:d:{_SVC}:{_STG}", "structural"),
        # 6. Field 3 (voicecli_head) differs → structural
        (_FP, f"a:b:c:D:{_SVC}:{_STG}", "structural"),
        # 7. Field 4 (staging-svc digest) differs → structural
        (
            _FP,
            f"a:b:c:d:deadbeefaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001:{_STG}",
            "structural",
        ),
        # 8. Field 5 (staging digest) differs → structural
        (
            _FP,
            f"a:b:c:d:{_SVC}:deadbeefaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0002",
            "structural",
        ),
        # 9. Auth + structural both differ → structural dominates
        (_FP, f"A:b:X:d:{_SVC}:{_STG}", "structural"),
        # 10. None-sentinel guard — non-tautological legacy-normalized case
        (
            "none",
            "none:none:X:none:none:none",
            "structural",
        ),
        # 11. Field-count guard: 7-field fingerprint → structural (fail-safe)
        (_FP, f"{_FP}:extra", "structural"),
        # 12. Legacy 4-field stamp vs 6-field current (image fields added) → structural
        ("a:b:c:d", _FP, "structural"),
        # 13. Legacy equal after normalization (images still none) → none
        ("a:b:c:d", "a:b:c:d:none:none", "none"),
    ],
    ids=[
        "equal_fingerprints→none",
        "no_prior_stamp→structural",
        "only_auth_differs→auth",
        "field0_git_only_differs→code_only",
        "field1_unit_differs→structural",
        "field3_voice_differs→structural",
        "field4_image_svc_differs→structural",
        "field5_image_stg_differs→structural",
        "auth_and_structural_both_differ→structural_dominates",
        "none_sentinel_nontautological→structural",
        "field_count_7_fields→structural",
        "legacy_4field_stamp→structural",
        "legacy_4field_equal_normalized→none",
    ],
)
def test_classify_drift(last: str, current: str, expected: str) -> None:
    """_classify_drift returns the correct drift category for each case."""
    assert _classify(last, current) == expected


def _fp_git(git_head: str) -> str:
    """6-field fingerprint whose only meaningful field is git_head (rest fixed)."""
    return f"{git_head}:u:a:v:{_SVC}:{_STG}"


def _inert(repo: Path, last_git: str, cur_git: str, env: dict) -> bool:
    """True iff _code_change_is_inert exits 0 (inert → safe to skip) for this repo + range."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source {DEPLOY_COMMON} >/dev/null 2>&1; FACTORY_DIR="{repo}"; '
            f'_code_change_is_inert "{_fp_git(last_git)}" "{_fp_git(cur_git)}"',
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def test_code_change_is_inert(tmp_path: Path) -> None:
    """Fail-safe negative allowlist: only pure docs/tests/CI/artifacts commits are inert (skip);
    any runtime path OR an undecidable diff is NOT inert (→ full converge, never under-restart)."""
    repo = tmp_path / "factory"
    repo.mkdir()

    # Isolate from the real repo: strip inherited git env so a pre-push hook's GIT_DIR cannot
    # redirect these git ops at ~/projects/roxabi-factory (memory: git fixture GIT_DIR leak).
    env = {**os.environ}
    for v in subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True
    ).stdout.split():
        env.pop(v, None)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, env=env, capture_output=True
        )

    def head() -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "README.md").write_text("base\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = head()

    # inert commit: only docs/, *.md, .github/, tests/
    (repo / "docs").mkdir()
    (repo / "docs" / "x.md").write_text("d\n")
    (repo / ".github").mkdir()
    (repo / ".github" / "wf.yml").write_text("w\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "t.py").write_text("t\n")
    git("add", "-A")
    git("commit", "-qm", "docs+ci+tests")
    inert_head = head()

    # runtime commit: touches src/
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("code\n")
    git("add", "-A")
    git("commit", "-qm", "src change")
    runtime_head = head()

    # deploy commit: touches deploy/
    (repo / "deploy").mkdir()
    (repo / "deploy" / "converge.sh").write_text("#\n")
    git("add", "-A")
    git("commit", "-qm", "deploy change")
    deploy_head = head()

    assert _inert(repo, base, inert_head, env) is True, (
        "docs/ci/tests-only must be inert"
    )
    assert _inert(repo, base, runtime_head, env) is False, (
        "src/ change must NOT be inert"
    )
    assert _inert(repo, inert_head, deploy_head, env) is False, (
        "deploy/ change must NOT be inert"
    )
    # undecidable → fail-safe NOT inert
    assert _inert(repo, "none", inert_head, env) is False, (
        "missing last git_head → not inert"
    )
    assert _inert(repo, base, "none", env) is False, (
        "missing current git_head → not inert"
    )


def _image_carried(repo: Path, last_git: str, cur_git: str, env: dict) -> bool:
    """True iff _code_change_is_image_carried exits 0 (skip pre-image restart)."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source {DEPLOY_COMMON} >/dev/null 2>&1; FACTORY_DIR="{repo}"; '
            f'_code_change_is_image_carried "{_fp_git(last_git)}" "{_fp_git(cur_git)}"',
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def test_code_change_is_image_carried(tmp_path: Path) -> None:
    """Image-carried allowlist: src/packages/apps-dashboard/brand (± inert paths) → defer the
    restart to the post-autoupdate digest converge; any host-carried path (deploy/, tools/,
    lockfiles, apps/ outside dashboard/, …) or an undecidable diff → full converge now (same
    fail-safe direction as _code_change_is_inert)."""
    repo = tmp_path / "factory"
    repo.mkdir()

    # Same git-env isolation as test_code_change_is_inert (pre-push GIT_DIR leak).
    env = {**os.environ}
    for v in subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True
    ).stdout.split():
        env.pop(v, None)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, env=env, capture_output=True
        )

    def head() -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "README.md").write_text("base\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = head()

    # image-carried commit: src/ + packages/ + apps/dashboard/ + brand/, mixed with inert docs/
    for d in ("src", "packages", "apps/dashboard", "brand", "docs"):
        (repo / d).mkdir(parents=True)
    (repo / "src" / "app.py").write_text("code\n")
    (repo / "packages" / "lib.py").write_text("lib\n")
    (repo / "apps" / "dashboard" / "ui.tsx").write_text("ui\n")
    (repo / "brand" / "theme.css").write_text("css\n")
    (repo / "docs" / "x.md").write_text("d\n")
    git("add", "-A")
    git("commit", "-qm", "image-carried + docs")
    image_head = head()

    # apps/ OUTSIDE dashboard/ ships in NO tracked image → must NOT be image-carried
    (repo / "apps" / "artifacts").mkdir()
    (repo / "apps" / "artifacts" / "shot.png").write_text("png\n")
    git("add", "-A")
    git("commit", "-qm", "apps artifact")
    apps_other_head = head()

    # host-carried commit: deploy/ config (bind-mounted, needs a real converge)
    (repo / "deploy").mkdir()
    (repo / "deploy" / "acl-matrix.json").write_text("{}\n")
    git("add", "-A")
    git("commit", "-qm", "deploy change")
    deploy_head = head()

    # host-carried commit: root lockfile (host tooling reads the checkout directly)
    (repo / "uv.lock").write_text("lock\n")
    git("add", "-A")
    git("commit", "-qm", "lockfile change")
    lock_head = head()

    # host-carried negatives from the function's own doc comment — each as an
    # ISOLATED single-file commit so no allowed path can mask the negative.
    host_carried_heads: dict[str, tuple[str, str]] = {}
    for rel in ("Dockerfile", "tools/x.sh", "scripts/x.py", "Makefile"):
        prev = head()
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n")
        git("add", "-A")
        git("commit", "-qm", f"host-carried {rel}")
        host_carried_heads[rel] = (prev, head())

    assert _image_carried(repo, base, image_head, env) is True, (
        "src/packages/apps-dashboard/brand (+docs) must be image-carried → skip"
    )
    assert _image_carried(repo, image_head, apps_other_head, env) is False, (
        "apps/ outside dashboard/ ships in no image → must force a converge now"
    )
    assert _image_carried(repo, image_head, deploy_head, env) is False, (
        "deploy/ change must force a converge now"
    )
    assert _image_carried(repo, deploy_head, lock_head, env) is False, (
        "uv.lock change must force a converge now"
    )
    assert _image_carried(repo, base, lock_head, env) is False, (
        "mixed image-carried + host-carried range must force a converge now"
    )
    for rel, (prev, cur) in host_carried_heads.items():
        assert _image_carried(repo, prev, cur, env) is False, (
            f"{rel} is host-carried (ships in no tracked image) → must force a converge now"
        )
    # undecidable → fail-safe: converge now
    assert _image_carried(repo, "none", image_head, env) is False, (
        "missing last git_head → converge"
    )
    assert _image_carried(repo, base, "none", env) is False, (
        "missing current git_head → converge"
    )
