#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [ ! -x .venv/bin/python ]; then
    python3 -c 'import sys; assert sys.version_info >= (3,11), "Python >=3.11 required"'
    python3 -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3,11), "Python >=3.11 required"'
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m hex_agent doctor
.venv/bin/python -m hex_agent validate
printf '%s\n' 'HEX installed. Local runtime and configuration checks passed.' 'Next: import the HEX-Skill zip from Releases into your host (e.g. Codex) and say "用 HEX ..."; run commands: docs/RUNTIME.md.'
