#!/usr/bin/env bash
# Fail if any two pytest ``testpaths`` files resolve to the same bare module
# name under ``--import-mode=importlib``.
#
# Why: the root ``pyproject.toml`` sets ``--import-mode=importlib``. Under that
# mode pytest imports a test file via the parent chain of ``__init__.py``
# files — if an ``__init__.py`` is present, the module name is fully
# qualified (e.g. ``tests.nats.test_sanitize``); if it is absent, the module
# name is the file's bare basename (e.g. ``test_sanitize``).
#
# Two bare-basename files with the same name register under the same key in
# ``sys.modules``; the second import silently reuses the first and its
# assertions never run.
#
# Root ``tests/`` has a complete ``__init__.py`` chain, so its files are
# always fully qualified — same-basename files inside it are safe. The
# non-root testpaths (``packages/*/tests``) deliberately have no
# ``__init__.py`` (see ``packages/roxabi-contracts/tests/conftest.py``
# docstring for rationale), so they feed into the bare-basename pool. This
# guard checks that pool for duplicates.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

TESTPATHS=(
    "tests"
    "packages/roxabi-nats/tests"
    "packages/roxabi-contracts/tests"
)

for p in "${TESTPATHS[@]}"; do
    [ -d "$p" ] || { echo "testpath not found: $p" >&2; exit 2; }
done

# A test file resolves to a bare basename iff its directory has no
# ``__init__.py``. Collect only those files — they are the population at
# risk of collision.
BARE_FILES=$(
    find "${TESTPATHS[@]}" -type f -name 'test_*.py' \
        | while IFS= read -r f; do
            [ -f "$(dirname "$f")/__init__.py" ] || printf '%s\n' "$f"
          done
)

DUPES=$(
    printf '%s\n' "$BARE_FILES" \
        | awk -F/ 'NF > 0 { print $NF "\t" $0 }' \
        | sort \
        | awk -F'\t' '
            { count[$1]++; paths[$1] = paths[$1] $2 "\n" }
            END {
                for (b in count) {
                    if (count[b] > 1) {
                        printf("duplicate bare basename: %s\n%s", b, paths[b])
                    }
                }
            }
          '
)

if [ -n "$DUPES" ]; then
    echo "Duplicate bare test basenames across pytest testpaths:" >&2
    echo "Files without __init__.py resolve to bare module names under" >&2
    echo "--import-mode=importlib; duplicates shadow each other in sys.modules." >&2
    echo >&2
    printf '%s\n' "$DUPES" >&2
    echo "Rename one (prefix with package name), or add __init__.py to a" >&2
    echo "parent directory to force a fully-qualified module path." >&2
    exit 1
fi
