"""Five Seconds Hack — source package.

Import this package to access all bot subsystems:
  config, state, sonar, llm, graph, render, server, main.
"""

import tomllib
from pathlib import Path

with open(Path(__file__).parent.parent / "pyproject.toml", "rb") as _f:
    __version__ = tomllib.load(_f)["project"]["version"]
