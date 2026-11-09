from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def resource_path(*parts: str) -> str:
    """Путь к ресурсу с учётом упаковки PyInstaller (sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, *parts)
    root = Path(__file__).resolve().parent.parent
    return str(root.joinpath(*parts))
