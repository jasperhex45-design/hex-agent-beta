"""Install only the pinned CPU embedding runtime in a project-local venv."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from liu_agent.native_runtime import knowledge_python

RUNTIME = ROOT / '.runtime' / 'knowledge'
MODEL = RUNTIME / 'model'
MANIFEST = Path(__file__).with_name('native_model_manifest.json')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if not args.verify_only:
        MODEL.mkdir(parents=True, exist_ok=True)
    for row in manifest['files']:
        target = MODEL / row['file']
        if target.exists() and sha(target) == row['sha256'] and target.stat().st_size == row['bytes']:
            continue
        if args.verify_only:
            raise SystemExit('Missing or changed model file: ' + row['file'])
        legacy_name = 'README.md' if row['file'] == 'BAAI-MODEL-CARD.md' else row['file']
        legacy = ROOT / '.runtime/ragflow/deployment/models/bge-small-zh-v1.5' / legacy_name
        temporary = target.with_suffix(target.suffix + '.part')
        if legacy.is_file() and sha(legacy) == row['sha256']:
            shutil.copyfile(legacy, temporary)
        else:
            print('Downloading pinned file: ' + row['file'], flush=True)
            with urllib.request.urlopen(row['url'], timeout=60) as response, temporary.open('wb') as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
        if temporary.stat().st_size != row['bytes'] or sha(temporary) != row['sha256']:
            raise SystemExit('Model integrity check failed: ' + row['file'])
        temporary.replace(target)
    python = knowledge_python(ROOT)
    if not args.verify_only:
        if not python.exists():
            subprocess.run([sys.executable, '-m', 'venv', str(RUNTIME / 'venv')], check=True)
        lock = Path(__file__).with_name('native-requirements.lock.txt')
        requirements = lock if lock.is_file() else Path(__file__).with_name('native-requirements.txt')
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(requirements)], check=True)
        installed = subprocess.check_output([str(python), '-m', 'pip', 'freeze'], text=True)
        (RUNTIME / 'requirements-installed.txt').write_text(installed, encoding='utf-8')
        (RUNTIME / 'model-source.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if not python.is_file():
        raise SystemExit('Missing native embedding venv')
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    print(json.dumps({'model_files_verified': len(manifest['files']),
                      'model_bytes': sum(row['bytes'] for row in manifest['files']),
                      'python': str(python), 'runtime': 'CPUExecutionProvider',
                      'network_at_query': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
