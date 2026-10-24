"""Windows entry routing to existing Hex contracts, with no second runtime."""
from __future__ import annotations

import json
import sys

from .io import ROOT, AgentError
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
        },
        'background_services_required': [], 'ragflow_deployed': False,
        'official_dynamic_workflows_installed': False,
        'workflow_control': 'Hex existing checkpoints, full-flow completion gates and bounded local drive',
        'network_used': False, 'generation_calls': 0,
        'note': 'Presence checks only; runtime evidence is separate. No visual quality claim.',
    }

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
        from .cli import main as core_main
        return core_main(creation_arguments(args))
    except (AgentError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
