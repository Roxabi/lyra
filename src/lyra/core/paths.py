from importlib.resources import files
from pathlib import Path

PLUGINS_DIR: Path = Path(str(files("lyra.commands")))
