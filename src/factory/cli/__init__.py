"""factory CLI subpackage — unified Typer apps and console-script entry points."""

from factory.cli.main import (
    agent_app,
    agent_main,
    factory_app,
    factory_main,
    main,
)

__all__ = [
    "agent_app",
    "agent_main",
    "factory_app",
    "factory_main",
    "main",
]
