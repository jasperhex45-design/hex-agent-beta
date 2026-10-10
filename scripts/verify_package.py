"""Verify shipped-file hashes without importing any model, making calls, or changing files."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'MANIFEST.json').read_text(encoding='utf-8'))
bad=[]
for item in manifest['files']:
    path=(root/item['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        bad.append(item['path']+': missing/invalid')
    elif hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
        bad.append(item['path']+': changed')
print(json.dumps({'checked':len(manifest['files']), 'mismatches':bad,
                  'note':'Hashes verify shipped bytes, not authorship, aesthetic quality, or model access.'},ensure_ascii=False,indent=2))
raise SystemExit(1 if bad else 0)
