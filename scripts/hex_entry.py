"""Portable Skill entry into this exact checkout, including installed Skills."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from liu_agent.windows_runtime import main

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
