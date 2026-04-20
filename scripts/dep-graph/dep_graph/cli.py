"""dep-graph CLI — unified entry point.

Subcommands:
  fetch     Refresh GitHub cache (gh.json)
  build     Render HTML from layout + cache
  audit     Report label drift vs layout
  validate  Check layout.json against schema

Common flags:
  --layout PATH   (default: lyra-v2-dependency-graph.layout.json)
  --cache  PATH   (default: sibling of layout with .gh.json suffix)
  --out    PATH   (default: sibling of layout with .html/.gh.json suffix)
  --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_LAYOUT_REL = ".roxabi/forge/lyra/visuals/lyra-v2-dependency-graph.layout.json"
DEFAULT_LAYOUT = Path.home() / _LAYOUT_REL


def _resolve_paths(
    args: argparse.Namespace,
) -> tuple[Path, Path, Path]:
    """Return (layout_path, cache_path, out_path)."""
    layout_path = Path(args.layout)
    stem = layout_path.stem.removesuffix(".layout")
    base_dir = layout_path.parent

    cache_path = Path(args.cache) if args.cache else base_dir / f"{stem}.gh.json"
    out_path = Path(args.out) if args.out else base_dir / f"{stem}.html"
    return layout_path, cache_path, out_path


def cmd_fetch(args: argparse.Namespace) -> int:
    from .fetch import run_fetch

    layout_path, cache_path, _ = _resolve_paths(args)
    # For fetch, --out (if given) overrides the cache path.
    out_path = Path(args.out) if args.out else cache_path
    return run_fetch(layout_path, out_path, verbose=args.verbose)


def cmd_build(args: argparse.Namespace) -> int:
    from .build import BuildPaths, run_build

    layout_path, cache_path, out_path = _resolve_paths(args)
    bak_path = out_path.with_suffix(".html.bak")

    return run_build(
        BuildPaths(layout_path, cache_path, out_path, bak_path),
        no_validate=args.no_validate,
        verbose=args.verbose,
    )


def cmd_audit(args: argparse.Namespace) -> int:
    from .audit import run_audit

    layout_path, cache_path, _ = _resolve_paths(args)
    return run_audit(layout_path, cache_path, verbose=args.verbose)


def cmd_migrate(args: argparse.Namespace) -> int:
    from .migrate import run_migrate

    return run_migrate(Path(args.layout), verbose=args.verbose)


def cmd_validate(args: argparse.Namespace) -> int:
    from .schema import LayoutValidationError, validate_layout

    layout_path = Path(args.layout)
    if not layout_path.exists():
        print(f"ERROR: {layout_path} not found", file=sys.stderr)
        return 1
    try:
        validate_layout(layout_path)
        print("Schema validation passed.")
        return 0
    except LayoutValidationError as exc:
        print(f"SCHEMA ERROR at {exc.path}: {exc.message}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dep-graph",
        description="GitHub-driven dependency graph generator.",
    )
    parser.add_argument(
        "--layout",
        default=str(DEFAULT_LAYOUT),
        metavar="PATH",
        help="Path to layout.json (default: %(default)s)",
    )
    parser.add_argument(
        "--cache",
        default=None,
        metavar="PATH",
        help="Path to gh.json cache (default: sibling of layout with .gh.json suffix)",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Output path (default: sibling of layout with .html/.gh.json suffix)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch
    p_fetch = subparsers.add_parser("fetch", help="Refresh GitHub cache (gh.json)")
    p_fetch.add_argument("--layout", default=str(DEFAULT_LAYOUT), metavar="PATH")
    p_fetch.add_argument("--out", default=None, metavar="PATH")
    p_fetch.add_argument("--verbose", "-v", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch)

    # build
    p_build = subparsers.add_parser("build", help="Render HTML from layout + cache")
    p_build.add_argument("--layout", default=str(DEFAULT_LAYOUT), metavar="PATH")
    p_build.add_argument("--cache", default=None, metavar="PATH")
    p_build.add_argument("--out", default=None, metavar="PATH")
    p_build.add_argument(
        "--no-validate", action="store_true", help="Skip schema validation before build"
    )
    p_build.add_argument("--verbose", "-v", action="store_true")
    p_build.set_defaults(func=cmd_build)

    # audit
    p_audit = subparsers.add_parser("audit", help="Report label drift vs layout")
    p_audit.add_argument("--layout", default=str(DEFAULT_LAYOUT), metavar="PATH")
    p_audit.add_argument("--cache", default=None, metavar="PATH")
    p_audit.add_argument("--verbose", "-v", action="store_true")
    p_audit.set_defaults(func=cmd_audit)

    # migrate
    p_migrate = subparsers.add_parser(
        "migrate", help="Migrate layout to multi-repo format"
    )
    p_migrate.add_argument(
        "--layout",
        default=str(DEFAULT_LAYOUT),
        metavar="PATH",
        help="Path to layout.json (default: %(default)s)",
    )
    p_migrate.add_argument("--verbose", "-v", action="store_true")
    p_migrate.set_defaults(func=cmd_migrate)

    # validate
    p_validate = subparsers.add_parser(
        "validate", help="Check layout.json against schema"
    )
    p_validate.add_argument("--layout", default=str(DEFAULT_LAYOUT), metavar="PATH")
    p_validate.add_argument("--verbose", "-v", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
