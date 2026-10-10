"""The fixed, project-local native embedding interpreter for this host.

Runtime discovery never falls back to PATH or a user-configured executable. A
Windows venv copied to Linux (or the reverse) must be installed for the host.
This module uses only the standard library so the installer can import it before
installing Hex's own dependencies.
"""
from __future__ import annotations

from pathlib import Path
import sys


def knowledge_python(root: Path, *, platform: str | None = None) -> Path:
    platform = sys.platform if platform is None else platform
    relative = 'Scripts/python.exe' if platform == 'win32' else 'bin/python'
    return root / '.runtime' / 'knowledge' / 'venv' / relative
