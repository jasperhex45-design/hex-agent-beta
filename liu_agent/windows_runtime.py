"""Windows entry routing to existing Hex contracts, with no second runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .io import ROOT, AgentError, read_json
from .native_runtime import knowledge_python

CONFIG = ROOT / 'configs/knowledge-windows.json'


def _has_option(args, flag):
    return any(arg == flag or arg.startswith(flag + '=') for arg in args)


def creation_arguments(args, *, windows=None):
    """Preserve explicit choices and existing-run actions verbatim."""
    args = list(args)
    if windows is None:
        windows = sys.platform == 'win32'
    index = 0
    while index < len(args) and args[index].startswith('--'):
        name = args[index].split('=', 1)[0]
        if name not in ('--kb', '--state', '--format'):
            return args
        index += 1 if '=' in args[index] else 2
    if index >= len(args) or args[index] not in ('prepare', 'run'):
        return args
    if not _has_option(args, '--workflow-profile'):
        args += ['--workflow-profile', 'full-flow-v1']
    if windows and not _has_option(args, '--execution-profile'):
        args += ['--execution-profile', 'knowledge-assisted-v1']
        if not _has_option(args, '--knowledge-config'):
            args += ['--knowledge-config', str(CONFIG)]
    return args


def runtime_status():
    def exists(relative):
        return (ROOT / relative).is_file()
    return {
        'format': 'hex.windows-runtime.v1', 'system': sys.platform,
        'entry': 'Hex.ps1 / python -m hex_agent' if sys.platform == 'win32' else 'python -m hex_agent',
        'stages': {
            'understand_and_design': 'current_host_using_existing_Need_and_Plan',
            'knowledge': {'backend': 'native_cpu_onnx',
                          'environment_present': knowledge_python(ROOT).is_file(),
                          'python': str(knowledge_python(ROOT)),
                          'model_present': exists('.runtime/knowledge/model/model.onnx'),
                          'new_windows_tasks': 'knowledge-assisted-v1', 'config': str(CONFIG)},
            'generate': 'existing_dispatch_and_executors_with_original_authorization',
            'check_text': {'environment_present': exists('.runtime/ocr/venv/Scripts/python.exe'),
                           'invocation': 'workflow requirements/check; full-flow requires explicit text applicability'},
            'visual_review': 'current_host_views_real_image; review prepares isolated input only',
            'feedback': 'existing_candidate_bound_user_feedback',
            'offline_research': {
                'gepa_environment_present': exists('.runtime/research/Scripts/python.exe'),
                'promptfoo_present': exists('.runtime/promptfoo/node_modules/promptfoo/package.json'),
                'model_optimization': 'not_configured_or_verified',
                'production_method_promotion': 'not_enabled'},
        },
        'background_services_required': [], 'ragflow_deployed': False,
        'official_dynamic_workflows_installed': False,
        'workflow_control': 'Hex existing checkpoints, full-flow completion gates and bounded local drive',
        'network_used': False, 'generation_calls': 0,
        'note': 'Presence checks only; runtime evidence is separate. No visual quality claim.',
    }


def _research(args):
    parser = argparse.ArgumentParser(prog='hex-agent research')
    sub = parser.add_subparsers(dest='action', required=True)
    verify = sub.add_parser('verify', help='Installed engine/CLI fixtures; not model learning')
    verify.add_argument('--component', choices=['all', 'gepa', 'promptfoo', 'versions'], default='all')
    verify.add_argument('--out', type=Path, required=True)
    evaluate = sub.add_parser('evaluate', help='Run promptfoo on existing Hex evidence without models')
    evaluate.add_argument('--bindings', type=Path, required=True)
    evaluate.add_argument('--out', type=Path, required=True)
    ns = parser.parse_args(args)
    python = ROOT / '.runtime/research/Scripts/python.exe'
    if not python.is_file():
        raise AgentError('本地研究环境缺失；没有启动模型或安装')
    if ns.out.exists():
        raise AgentError('研究输出须为新目录，不能覆盖已有报告')
    out = ns.out.resolve()
    if any((parent / 'packet.json').exists() or (parent / 'acceptance.json').exists()
           for parent in (out, *out.parents)):
        raise AgentError('研究输出不能位于已有任务目录内')
    if ns.action == 'verify':
        out.mkdir(parents=True, exist_ok=False)
        return subprocess.run([str(python), str(ROOT / 'scripts/runtime/research_verify.py'),
                               ns.component, '--out', str(out)], check=False).returncode
    from .research_adapters.promptfoo_adapter import export_config
    bindings = read_json(ns.bindings)
    if not isinstance(bindings, list) or not bindings:
        raise AgentError('bindings 必须是非空的既有任务绑定列表')
    for row in bindings:
        for key in ('run', 'comparison_dir'):
            if row.get(key) and out.is_relative_to(Path(row[key]).resolve()):
                raise AgentError('研究报告须在原任务或比较目录之外')
    package = ROOT / '.runtime/promptfoo/node_modules/promptfoo/package.json'
    if not package.is_file() or not shutil.which('node'):
        raise AgentError('本地 promptfoo 或 Node 缺失；未安装任何内容')
    metadata = read_json(package)
    binary = package.parent / metadata['bin']['promptfoo']
    if not binary.is_file():
        raise AgentError('本地 promptfoo 程序不完整')
    out.mkdir(parents=True, exist_ok=False)
    config = out / 'promptfoo-config.json'
    export_config(bindings, config)
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONPATH=str(ROOT),
               PROMPTFOO_PYTHON=str(python), PROMPTFOO_DISABLE_TELEMETRY='1',
               PROMPTFOO_DISABLE_UPDATE='1', PROMPTFOO_CONFIG_DIR=str(out / 'local-state'),
               NO_COLOR='1', CI='1')
    return subprocess.run([shutil.which('node'), str(binary), 'eval', '-c', str(config),
                           '--no-cache', '--no-progress-bar', '--no-table',
                           '-o', str(out / 'results.json')], env=env, check=False).returncode


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args or args[0] == 'runtime':
            print(json.dumps(runtime_status(), ensure_ascii=False, indent=2))
            return 0
        if args[0] == 'codex':
            from .codex_integration import main as codex_main
            return codex_main(args[1:])
        if args[0] == 'workflow':
            from .workflow_policy import main as workflow_main
            return workflow_main(args[1:])
        if args[0] == 'references':
            from .reference_catalog import main as references_main
            return references_main(args[1:])
        if args[0] == 'pinterest':
            from .pinterest_source import main as pinterest_main
            return pinterest_main(args[1:])
        if args[0] in ('check', 'review', 'review-record'):
            from .task_review import main as review_main
            command = {'check': 'check', 'review': 'prepare-vlm', 'review-record': 'validate-review'}[args[0]]
            return review_main([command, *args[1:]])
        if args[0] == 'car':
            from .car_photography import main as car_main
            rest = args[1:]
            if rest and rest[0] == 'prepare' and not _has_option(rest, '--workflow-profile'):
                rest += ['--workflow-profile', 'full-flow-v1']
            if rest and rest[0] == 'prepare' and sys.platform == 'win32' and not _has_option(rest, '--knowledge-config'):
                rest += ['--knowledge-config', str(CONFIG)]
            return car_main(rest)
        if args[0] == 'research' and len(args) > 1 and args[1] in ('verify', 'evaluate'):
            return _research(args[1:])
        from .cli import main as core_main
        return core_main(creation_arguments(args))
    except (AgentError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
