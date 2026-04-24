from importlib.resources import files
from pathlib import Path

PLUGINS_DIR: Path = Path(str(files(__name__)))

__all__ = ["PLUGINS_DIR"]
