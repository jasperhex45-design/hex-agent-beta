"""Build a public beta source bundle, excluding developer and task data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]

# The runtime resolves knowledge, schemas and authored examples beside the source.
# Historical analyses, personal libraries and generated runs are not dependencies.
SOURCE_TREES = {
    'liu_agent': {'.py'},
    'hex_agent': {'.py'},
    'cl_hex': {'.py'},
    'schemas': {'.json'},
    'knowledge/core': {'.json'},
    'knowledge/design': {'.json'},
    'knowledge/models': {'.json'},
    'knowledge/model-prompts': {'.md'},
    '.agents/skills/cl-hex-car-photography': {'.md'},
    '.agents/skills/liu-aesthetic-agent': {'.md'},
    '.agents/skills/liu-aesthetic-judgment': {'.md'},
    '.agents/skills/hex': {'.md', '.yaml'},
    '.agents/skills/hex-work-learning': {'.md'},
}
ENTRY_FILES = {'.agents/skills/hex/SKILL.md', '.agents/skills/hex/agents/openai.yaml'}
REQUIRED_FILES = {
    'README.md', 'CONTRIBUTING.md', 'docs/RUNTIME.md', 'LICENSE',
    'THIRD_PARTY_NOTICES.md', 'pyproject.toml', 'requirements.txt',
    'liu_agent/__main__.py', 'hex_agent/__main__.py', 'cl_hex/__main__.py',
    'examples/demo-brief.json', 'examples/demo-need.json',
    'examples/demo-solution.json', 'scripts/release_local.py',
    'scripts/verify_package.py', 'Hex.ps1',
    'scripts/hex_entry.py', 'scripts/codex_hook.py',
    'configs/knowledge-windows.json', 'scripts/runtime/native_worker.py',
    'scripts/bootstrap.ps1', 'scripts/bootstrap.sh',
    'docs/FEEDBACK_CREATION_CONTEXT.md', 'docs/ESSENTIAL_CREATION_FEEDBACK.md',
    '.agents/skills/hex-work-learning/SKILL.md', 'docs/WORK_LEARNING.md', 'docs/INSTALL.md',
}
# These are runtime instructions, not a copy of the development workspace.
# A fresh tester should not receive historical checks, outputs or private examples.
PUBLIC_FILES = REQUIRED_FILES | {
    'CHANGELOG.md', '.env.example', '.gitignore', '.gitattributes',
    '.github/ISSUE_TEMPLATE/feedback.yml',
    'knowledge/color_source_mapping.json', 'knowledge/candidates/card.template.json',
    'knowledge/research-entrypoints.md',
    'examples/edit-brief.template.json',
    'examples/vehicle-brief.json',
    'scripts/bootstrap.ps1', 'scripts/bootstrap.sh', 'scripts/demo.ps1',
    'configs/knowledge-local.example.json',
    'scripts/runtime/native_install.py', 'scripts/runtime/native_verify.py',
    'scripts/runtime/native-requirements.txt', 'scripts/runtime/native-requirements.lock.txt',
    'scripts/runtime/native_model_manifest.json',
    'scripts/runtime/ocr_runtime.py', 'scripts/runtime/ocr_models.py',
    'scripts/runtime/ocr-requirements.txt', 'scripts/runtime/ocr-run.ps1',
    'docs/CAR_PHOTOGRAPHY_MVP.md', 'docs/SOURCE_AND_REVIEW_INPUTS.md',
    'docs/SOURCES_AND_PROVENANCE.md',
    'docs/KNOWLEDGE_AND_UPDATES.md',
    'docs/WINDOWS_RUNTIME.md',
    'docs/ESSENTIAL_CREATION_FEEDBACK.md',
    'docs/HOST_PORTABILITY.md', 'docs/HEX_CORE_METHOD.md',
    'scripts/build_skill_package.py', 'scripts/skill_bundle_entry.py',
}
EXCLUDED_PARTS = {
    '__pycache__', '.git', '.venv', '.state', '.runtime', 'build', 'dist',
    'outputs', 'inputs', 'evidence', 'experiments', 'local-library',
    'tests', 'reports', 'archive', 'research',
}


def _ordinary_path(path: Path, root: Path) -> bool:
    """Reject symlinks/junctions, including a link in any selected parent."""
    return path.resolve().is_relative_to(root) and path.resolve() == path.absolute()


def selected_files(root: Path = ROOT) -> list[Path]:
    root = root.resolve()
    files = set()
    for relative in PUBLIC_FILES:
        path = root / relative
        if path.is_file() and _ordinary_path(path, root):
            files.add(path)
    for relative, suffixes in SOURCE_TREES.items():
        tree = root / relative
        if not tree.is_dir() or not _ordinary_path(tree, root):
            continue
        # Walk only selected trees; never traverse excluded private directories.
        for current, directories, names in os.walk(tree, followlinks=False):
            current = Path(current)
            directories[:] = [name for name in directories
                              if name not in EXCLUDED_PARTS and not name.startswith('.')
                              and not name.endswith('.egg-info')
                              and _ordinary_path(current / name, root)]
            for name in names:
                path = current / name
                if (not name.startswith('.') and path.suffix in suffixes
                        and path.is_file() and _ordinary_path(path, root)):
                    files.add(path)
    missing = REQUIRED_FILES - {p.relative_to(root).as_posix() for p in files}
    if missing:
        raise ValueError('Missing required product files: ' + ', '.join(sorted(missing)))
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def build_release(root: Path = ROOT, output_dir: Path | None = None, *,
                  source_commit: str | None = None) -> dict:
    root = root.resolve()
    files = selected_files(root)
    entries = {path.relative_to(root).as_posix(): path for path in files}
    # The repository discovers the main Skill at its outer root. A standalone
    # source package carries the same entry beside its own runtime instead.
    # Never scan an unrelated ancestor or follow a linked/private source tree.
    for relative in sorted(ENTRY_FILES - entries.keys()):
        parent_source = root.parent / relative
        if (root.name != 'hex-agent' or not parent_source.is_file()
                or not _ordinary_path(parent_source, root.parent)):
            if relative.endswith('/agents/openai.yaml'):
                continue  # Optional display metadata for hosts that consume it.
            raise ValueError('Missing required HEX entry file: ' + relative)
        entries[relative] = parent_source
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]*', version):
        raise ValueError('Invalid release version')
    if source_commit is not None and not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise ValueError('Invalid source commit')
    name = f'Hex-Agent-v{version}-public-beta'
    destination = Path(output_dir) if output_dir is not None else root.parent / 'deliverables'
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f'{name}.zip'
    manifest = {
        'version': version,
        'scope': 'product_source_only',
        'channel': 'public_beta',
        'source_commit': source_commit,
        'notice': '公开 Beta 源码包；不含测试集、实验产物、私人图库、历史实图或任务记录。公开访问不改变现有许可，文件摘要不证明审美或工具可用性。',
        'files': [],
    }
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative, path in sorted(entries.items()):
            data = path.read_bytes()
            manifest['files'].append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest()})
            archive.writestr(f'{name}/{relative}', data)
        archive.writestr(f'{name}/MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    result = {'zip': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'files': len(entries) + 1, 'bytes': output.stat().st_size}
    output.with_suffix('.sha256.txt').write_text(result['sha256'] + '  ' + output.name + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--source-commit', help='Exact source commit used for this bundle')
    args = parser.parse_args()
    print(json.dumps(build_release(output_dir=args.output_dir,
                                   source_commit=args.source_commit), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

