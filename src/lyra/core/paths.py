from importlib.resources import files
from pathlib import Path

# metadata-only lookup — not a Python import; importlinter does not trace this
PLUGINS_DIR: Path = Path(str(files("lyra.commands")))
