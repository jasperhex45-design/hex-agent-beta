"""Absolute-path Codex command hook; no CWD or shell interpolation dependency."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')

from liu_agent.codex_integration import main

if __name__ == '__main__':
    raise SystemExit(main(['hook', *sys.argv[1:]]))
