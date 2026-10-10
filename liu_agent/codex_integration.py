"""Task-scoped Codex lifecycle assistance over the existing HEX runtime.

Wire contract: https://learn.chatgpt.com/docs/hooks (checked 2026-10-08).
Hooks are a guardrail, not a complete execution boundary. They never create
generation permission, run a model, infer a remote result, or mark work complete.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shlex
import sys

from .io import ROOT, AgentError, atomic_text, digest, now, read_json, sha, write_json
from .run_lock import _metadata_guard


FORMAT = 'hex.codex-integration.v1'
STATE_FORMAT = 'hex.codex-session.v1'
SKILL_FORMAT = 'hex.codex-skill-installation.v1'
GENERATION_TOOLS = ('image_gen.imagegen', 'image_gen__imagegen')
EVENTS = ('UserPromptSubmit', 'SessionStart', 'PreToolUse', 'PostToolUse', 'Stop', 'Interrupt')
_ERRORS = (AgentError, OSError, ValueError, TypeError, KeyError, AttributeError)
_MAX_INPUT = 16 * 1024 * 1024


def repository_root() -> Path:
    # A standalone source package owns its own root Skill. Do not mistake an
    # unrelated parent's generic AGENTS.md for this repository's root.
    if (ROOT / '.agents' / 'skills' / 'hex' / 'SKILL.md').is_file():
        return ROOT
    parent = ROOT.parent
    if ROOT.name == 'hex-agent' and (parent / '.agents' / 'skills' / 'hex' / 'SKILL.md').is_file():
        return parent
    return ROOT


def default_config() -> Path:
    return repository_root() / '.codex' / 'hex-integration.json'


def _object(path: Path) -> dict:
    value = read_json(path)
    if not isinstance(value, dict):
        raise AgentError('HEX 集成文件必须为 JSON 对象：' + str(path))
    return value


def configuration(path: Path) -> dict:
    path = Path(path).resolve()
    value = _object(path)
    if value.get('format') != FORMAT or value.get('runtime_root') != str(ROOT.resolve()):
        raise AgentError('HEX 集成配置不属于当前工程；在当前 checkout 重新运行 codex install')
    for name in ('repository_root', 'state_dir', 'python_executable', 'entry_script'):
        if not isinstance(value.get(name), str) or not Path(value[name]).is_absolute():
            raise AgentError('HEX 集成配置缺少有效绝对路径：' + name)
    if value['repository_root'] != str(repository_root().resolve()):
        raise AgentError('HEX 集成配置的仓库绑定不一致；重新安装当前 checkout')
    roots = value.get('allowed_run_roots')
    if not isinstance(roots, list) or not roots or any(
            not isinstance(root, str) or not Path(root).is_absolute() for root in roots):
        raise AgentError('HEX 集成配置缺少显式 allowed_run_roots')
    names = value.get('generation_tools')
    if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
        raise AgentError('HEX generation_tools 配置无效')
    for key in ('max_stagnant_stops', 'max_continuations'):
        if type(value.get(key)) is not int or not 1 <= value[key] <= 50:
            raise AgentError('HEX 接续限制必须为 1 至 50 的整数：' + key)
    return {**value, 'config_path': str(path)}


def _windows_launch(parts: list[str]) -> tuple[str, str]:
    # An unquoted, ASCII outer command works with cmd.exe and PowerShell. Paths
    # are literals inside the readable script, not cmd environment expansions.
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    script = '& ' + ' '.join(quote(value) for value in parts) + '; exit $LASTEXITCODE'
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded, script


def _hook_matchers(guarded_tools: list[str]) -> dict:
    return {'SessionStart': '^(startup|resume|clear|compact)$',
            'PreToolUse': '^(?:' + '|'.join(map(re.escape, guarded_tools)) + ')$',
            'PostToolUse': '^(?:' + '|'.join(map(re.escape, [*guarded_tools, 'Bash', 'exec_command'])) + ')$'}


def _hook_handlers(command: str, windows: str) -> dict:
    handlers = {}
    for event in EVENTS:
        handler = {'type': 'command', 'command': command, 'commandWindows': windows,
                   'timeout': 3 if event == 'Interrupt' else 10,
                   'statusMessage': 'HEX task check'}
        if event in ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse'):
            handler['additionalContextLimit'] = 1200
        handlers[event] = handler
    return handlers


def _project_hook_ownership(repo: Path, folder: Path) -> dict:
    """Recognize only the shipped portable launcher in this checkout's hooks.

    A fresh clone, or the bridge's first-run config, has no installer ownership
    metadata. Keep this launcher portable across Git checkouts, and recognize
    it when merging customized installations without a second controller.
    Do not infer ownership from mutable JSON.
    """
    if folder != repo / '.codex' or not (folder / 'hooks/hex_repo_bridge.py').is_file():
        return {}
    command = 'python3 "$(git rev-parse --show-toplevel)/.codex/hooks/hex_repo_bridge.py"'
    windows = ('powershell.exe -NoProfile -NonInteractive -Command "'
               "$r=(git rev-parse --show-toplevel).Trim(); $p=Join-Path $r 'hex-agent/.venv/Scripts/python.exe'; "
               "if (Test-Path -LiteralPath $p) { & $p (Join-Path $r '.codex/hooks/hex_repo_bridge.py'); "
               "exit $LASTEXITCODE } else { Write-Error 'HEX virtual environment missing; run bootstrap when available'; "
               'exit 2 }"')
    return {'owned_handlers': _hook_handlers(command, windows),
            'owned_matchers': _hook_matchers(list(GENERATION_TOOLS)),
            'windows_launch_script': windows.split('-Command "', 1)[1][:-1]}


def _merge_hooks(existing: dict, handlers: dict, previous: dict, matchers: dict,
                 portable: dict | None = None) -> dict:
    merged = deepcopy(existing)
    groups = merged.setdefault('hooks', {})
    if not isinstance(groups, dict):
        raise AgentError('已有 hooks.json 的 hooks 字段无效；未覆盖原文件')
    for event, rows in list(groups.items()):
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise AgentError('已有 hook matcher 组无效：' + event)
        retained = []
        for row in rows:
            children = row.get('hooks')
            if not isinstance(children, list) or any(not isinstance(child, dict) for child in children):
                raise AgentError('已有 hook handlers 无效：' + event)
            owned = [handler for handler in (previous.get('owned_handlers', {}).get(event),
                     (portable or {}).get('owned_handlers', {}).get(event), handlers.get(event))
                     if isinstance(handler, dict)]
            expected_matchers = (previous.get('owned_matchers', {}).get(event),
                                 (portable or {}).get('owned_matchers', {}).get(event), matchers.get(event))
            kept = []
            for child in children:
                exact = row.get('matcher') in expected_matchers and child in owned
                # Changing an installed handler's timeout, matcher or command
                # variant must not silently create two HEX controllers. A
                # shared launch command identifies it even after such edits.
                same_launcher = any(any(child.get(key) and child.get(key) == handler.get(key)
                                         for key in ('command', 'commandWindows')) for handler in owned)
                if same_launcher and not exact:
                    raise AgentError('已安装 HEX hook 有本地修改；未覆盖或追加重复定义：' + event)
                if not exact:
                    kept.append(child)
            children = kept
            if children:
                retained.append({**row, 'hooks': children})
        groups[event] = retained
    for event, handler in handlers.items():
        group = {'hooks': [handler]}
        if event in matchers:
            group['matcher'] = matchers[event]
        groups.setdefault(event, []).append(group)
    return merged


def _json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    backup = path.with_name(path.name + '.hex-backup-' + sha(raw)[:12])
    if not backup.exists():
        atomic_text(backup, raw.decode('utf-8'), newline='')
    return backup


def install(*, codex_dir: Path | None = None, skills_dir: Path | None = None,
            run_roots: list[Path] | None = None, generation_tools: list[str] | None = None,
            dry_run: bool = False) -> dict:
    """Prepare/merge only HEX-owned files; never edit Codex trust or permissions."""
    runtime, repo = ROOT.resolve(), repository_root().resolve()
    folder = (codex_dir or repo / '.codex').resolve()
    config_path, hooks_path = folder / 'hex-integration.json', folder / 'hooks.json'
    previous = _object(config_path) if config_path.exists() else {}
    if previous and previous.get('format') != FORMAT:
        raise AgentError('hex-integration.json 已有非本集成内容；未覆盖')
    existing = _object(hooks_path) if hooks_path.exists() else {}
    script, entry = runtime / 'scripts' / 'codex_hook.py', runtime / 'scripts' / 'hex_entry.py'
    if not script.is_file() or not entry.is_file():
        raise AgentError('当前安装缺少 HEX scripts；请在完整源码工程运行 codex install')
    # Resolving a venv's Python symlink selects the base interpreter and loses
    # its dependency environment. Preserve the interpreter's absolute spelling.
    parts = [str(Path(sys.executable).absolute()), str(script), '--config', str(config_path)]
    command = shlex.join(parts)
    windows, readable_windows = _windows_launch(parts)
    guarded_tools = (list(dict.fromkeys([*GENERATION_TOOLS, *(generation_tools or [])]))
                     if generation_tools is not None else previous.get('generation_tools', list(GENERATION_TOOLS)))
    if not isinstance(guarded_tools, list) or any(not isinstance(tool, str) or not tool for tool in guarded_tools):
        raise AgentError('generation_tools 配置无效；未覆盖原文件')
    matchers = _hook_matchers(guarded_tools)
    handlers = _hook_handlers(command, windows)
    portable = _project_hook_ownership(repo, folder)
    # Tracked project hooks must not become machine-specific after install.
    # The portable launcher selects the checkout's .venv, so an installation
    # using another interpreter must retain its explicit executable binding.
    # Compare absolute spellings, not resolved venv symlinks.
    preferred = runtime / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    project_interpreter = os.path.normcase(parts[0]) == os.path.normcase(str(preferred))
    portable_install = bool(portable and matchers == portable['owned_matchers'] and project_interpreter)
    if portable_install:
        handlers = portable['owned_handlers']
        readable_windows = portable['windows_launch_script']
    config = {'format': FORMAT, 'repository_root': str(repo), 'runtime_root': str(runtime),
              'python_executable': parts[0], 'entry_script': str(entry),
              'state_dir': previous.get('state_dir', str(runtime / '.runtime' / 'codex')),
              'allowed_run_roots': list(dict.fromkeys([str(runtime), *[
                  str(Path(root).resolve()) for root in run_roots or []]])) if run_roots is not None
                  else previous.get('allowed_run_roots', [str(runtime)]),
              'generation_tools': guarded_tools,
              'max_stagnant_stops': previous.get('max_stagnant_stops', 2),
              'max_continuations': previous.get('max_continuations', 12),
              'owned_handlers': handlers, 'owned_matchers': matchers, 'windows_launch_script': readable_windows,
              'trust': 'not_inspected', 'official_reference': 'https://learn.chatgpt.com/docs/hooks'}
    merged = _merge_hooks(existing, handlers, previous, matchers, portable)
    # Preserve all bytes when unchanged, and retain a checkout's uniform CRLF
    # style when changing hooks so customized -> portable migration stays clean.
    hooks_raw = hooks_path.read_bytes() if hooks_path.is_file() else b''
    hooks_text = hooks_raw.decode('utf-8') if merged == existing else _json_text(merged)
    if merged != existing and b'\r\n' in hooks_raw and b'\n' not in hooks_raw.replace(b'\r\n', b''):
        hooks_text = hooks_text.replace('\n', '\r\n')
    writes = {hooks_path: hooks_text,
              config_path: _json_text(config)}
    skill_destination = None
    if skills_dir is not None:
        source = repo / '.agents' / 'skills' / 'hex'
        skill_destination = Path(skills_dir).resolve() / 'hex'
        if source.resolve() == skill_destination or not (source / 'SKILL.md').is_file():
            raise AgentError('请从仓库根 hex Skill 安装到独立的技能目录')
        marker = skill_destination / 'installation.json'
        prior = _object(marker) if marker.is_file() else {}
        if skill_destination.exists() and any(skill_destination.iterdir()) and prior.get('format') != SKILL_FORMAT:
            raise AgentError('目标已有未由 HEX 管理的同名 Skill；保留原文件，请指定其它 skills-dir')
        owned_files = {}
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise AgentError('不复制 Skill 中的符号链接：' + str(path))
            if not path.is_file() or path.name == 'installation.json':
                continue
            relative = path.relative_to(source).as_posix()
            target = skill_destination / relative
            if target.is_file() and sha(target.read_bytes()) != prior.get('owned_files', {}).get(relative):
                raise AgentError('已安装 Skill 有本地修改；未覆盖：' + str(target))
            raw = path.read_bytes()
            owned_files[relative] = sha(raw)
            writes[target] = raw.decode('utf-8')
        installation = {key: config[key] for key in ('runtime_root', 'repository_root', 'python_executable', 'entry_script')}
        installation.update(format=SKILL_FORMAT, config_path=str(config_path), owned_files=owned_files)
        writes[marker] = _json_text(installation)
    changed = {path: body for path, body in writes.items()
               if not path.is_file() or path.read_bytes() != body.encode('utf-8')}
    backups = []
    if not dry_run:
        for path, body in changed.items():
            backup = _backup(path)
            if backup:
                backups.append(str(backup))
            # Hashes and reinstall comparisons bind the exact UTF-8 bytes.
            # Windows newline translation would mutate copied Skill content
            # and make an unchanged installation look locally modified.
            atomic_text(path, body, newline='')
    return {'format': 'hex.codex-install.v1', 'state': 'preview' if dry_run else 'configured',
            'config': str(config_path), 'hooks': str(hooks_path),
            'skill': str(skill_destination) if skill_destination else None,
            'changed_files': [str(path) for path in changed], 'backups': backups,
            'hook_launcher': 'project_portable' if portable_install else 'machine_local',
            'hook_trust': 'not_inspected', 'host_behavior': 'not_tested',
            'next': '在本机 Codex 的 /hooks 审阅当前定义；确认事件实际执行后再认定自动监督生效。',
            'notice': '未修改平台信任、权限或生成授权。配置完成不等于 hooks 已启用；云端命令 hooks 支持按宿主核对。' +
                      ('' if portable_install else '当前 hooks 绑定本机解释器与路径，不应作为跨机器配置提交。')}


def prompt_intent(prompt: str, *, bound: bool = False) -> str:
    """Conservative activation only. Semantic creation remains the host's job."""
    text = re.sub(r'```[\s\S]*?```|`[^`]*`', '', prompt).strip()
    text = re.sub(r'[“「『][^”」』]*[”」』]', '', text)
    if re.search(r'(?:不(?:要)?(?:再)?(?:使用|用|启用|让)|(?:无需|别)(?:使用|用|启用|让)?)\s*hex(?![a-z0-9_])'
                 r'|\b(?:without|don\W?t use)\s+hex\b', text, re.I):
        return 'disabled'
    if re.search(r'^(?:请|先|现在)?\s*(?:停止|暂停|取消|停一下|别继续|stop\b|cancel\b)', text, re.I):
        return 'paused'
    discussion = re.search(r'代码|源码|仓库|架构|单测|安装|\b(?:hooks?|bug|code|repo|skill)\b', text, re.I)
    visual = re.search(r'图|海报|分镜|摄影|画|提示词|改色|构图|配色|视觉|设计稿|\b(?:image|poster|photo|storyboard|prompt)\b', text, re.I)
    development = re.search(
        r'(?:修改|修复|排查|优化|重构|实现|开发|编写|测试|升级|写|改).{0,24}'
        r'(?:代码|源码|仓库|模块|脚本|单测|安装器|\b(?:hooks?|bug|code|repo|skill)\b)', text, re.I)
    if development:
        return 'unrelated'
    if discussion and not visual:
        return 'unrelated'
    if re.match(r'^(?:请)?(?:教我|如何|怎么|为什么|解释|讲解).{0,35}(?:用|使用|让)\s*hex', text, re.I):
        return 'unrelated'
    if re.search(r'^(?:请|那就|好的|好)?\s*(?:继续|接着|恢复|按.{0,20}(?:反馈|要求).{0,8}改|continue\b|resume\b)', text, re.I):
        return 'resume' if bound else 'activate' if re.search(r'hex(?![a-z0-9_])', text, re.I) else 'unrelated'
    trigger = re.search(r'(?:\$hex(?![a-z0-9_])|(?:使用|用|让|启用)\s*hex(?![a-z0-9_])'
                        r'|(?:走|按)\s*hex\s*(?:的)?(?:完整|全)?流程|\buse\s+hex\b)', text, re.I)
    if bound and trigger and re.search(r'(?:继续|接着|恢复|按.{0,20}(?:反馈|要求).{0,8}改)', text):
        return 'resume'
    if trigger and (visual or re.search(r'完整流程|全流程', text)):
        return 'activate'
    if re.fullmatch(r'(?:请\s*)?(?:用\s*hex|使用\s*hex|\$hex)[。.!！\s]*', text, re.I):
        return 'entry_only'
    if bound and re.search(r'^(?:按刚才|保持|保留|把.{0,20}改成|这张|再改|颜色|机位|光线|构图)', text):
        return 'resume'
    return 'unrelated'


def _scope(config: dict, session_id: str, workspace: Path) -> tuple[Path, dict]:
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 400:
        raise AgentError('需要真实 Codex session_id；不猜测最近会话')
    scope = {'session_id': session_id, 'repository_root': config['repository_root'],
             'workspace': str(Path(workspace).resolve())}
    return Path(config['state_dir']) / 'sessions' / digest(scope), scope


def _load_state(folder: Path, scope: dict) -> dict:
    path = folder / 'session.json'
    if not path.is_file():
        return {'format': STATE_FORMAT, 'scope': scope, 'mode': 'idle', 'turn_active': False,
                'run': None, 'event_keys': [], 'continuations': 0, 'stagnant_stops': 0}
    state = _object(path)
    if state.get('format') != STATE_FORMAT or state.get('scope') != scope:
        raise AgentError('Codex 会话绑定与当前工程或工作目录不一致')
    return state


def _resolve_run(config: dict, run: Path) -> Path:
    run = Path(run).resolve()
    if not any(run.is_relative_to(Path(root).resolve()) for root in config['allowed_run_roots']):
        raise AgentError('run 不在本集成允许的输出目录；需由用户指定 codex install --run-root 后再绑定')
    return run


def _bind(config: dict, state: dict, run: Path, *, automatic: bool = False) -> None:
    from .workflow import read_packet
    from .provenance import verified_sources
    run = _resolve_run(config, run)
    packet = read_packet(run)
    sources = verified_sources(packet.get('input_sources'))
    if state.get('run') == str(run):
        _bound_packet(config, state)
        return  # Binding the same task again is not progress or new permission.
    if automatic and not any(row['actor'] == 'user' and sha(row['text'].encode('utf-8')) ==
                             state.get('pending_prompt_sha256') for row in sources):
        raise AgentError('prepare 输出未绑定本轮原始用户消息，不能自动接管其它任务')
    state.update(run=str(run), packet_created_at=packet.get('created_at'),
                 source_prefix_count=len(sources), source_prefix_digest=digest(sources),
                 bound_packet_digest=packet['digest'], mode='active', turn_active=True,
                 pending_prompt_sha256=None, pending_has_task=False,
                 checkpoint=None, stagnant_stops=0, continuations=0)


def bind_run(config: dict, session_id: str, workspace: Path, run: Path) -> dict:
    folder, scope = _scope(config, session_id, workspace)
    folder.mkdir(parents=True, exist_ok=True)
    with _metadata_guard(folder):
        state = _load_state(folder, scope)
        _bind(config, state, run)
        write_json(folder / 'session.json', state)
    return {'state': 'bound', 'run': state['run'], 'scope': scope,
            'notice': '绑定不生成图片、不增加授权或重置任务预算。'}


def _bound_packet(config: dict, state: dict) -> tuple[Path, dict]:
    from .workflow import read_packet
    from .provenance import verified_sources
    run = _resolve_run(config, Path(state['run']))
    packet = read_packet(run)
    sources = verified_sources(packet.get('input_sources'))
    count = state['source_prefix_count']
    if packet.get('created_at') != state['packet_created_at'] or digest(sources[:count]) != state['source_prefix_digest']:
        raise AgentError('已绑定任务被替换或原消息前缀改变；不能静默切换到另一任务')
    return run, packet


def _inspect(run: Path, **kwargs) -> dict:
    from .run_status import inspect_run
    return inspect_run(run, **kwargs)


def _context(event: str, text: str) -> dict:
    return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text[:6000]}}


def _deny(reason: str) -> dict:
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                   'permissionDecisionReason': reason[:2500]}}


def _entry_context(config: dict, state: dict) -> str:
    scope = state['scope']
    folder, _ = _scope(config, scope['session_id'], Path(scope['workspace']))
    parts = [config['python_executable'], config['entry_script'], 'codex', 'bind',
             '--config', config['config_path'], '--session-id', scope['session_id'],
             '--workspace', scope['workspace'], '--run', '<真实 run 绝对路径>']
    command = ('& ' + ' '.join("'" + part.replace("'", "''") + "'" for part in parts)
               if sys.platform == 'win32' else shlex.join(parts))
    pending = ('当前仅确认 HEX 入口；先从实际对话确认视觉目标，目标不明时询问一句，不虚构创作任务。\n'
               if not state.get('pending_has_task') else '')
    return (pending + '本轮明确调用 HEX。先加载 hex Skill，用真实用户消息和素材沿当前 prepare 创建任务；'
            '已有任务则核对原 run 后恢复。保持用户请求的交付类型。'
            '\n原始触发消息保存于：' + str(folder / 'user-request.txt') +
            '\n成功 prepare 会尝试自动绑定；未自动绑定时执行：' + command +
            '\n不要直接调用生成工具或只口头宣称接管。绑定和接续不增加生成许可。')


def _status_context(config: dict, state: dict) -> str:
    run, _ = _bound_packet(config, state)
    status = _inspect(run)
    return 'HEX 已绑定任务。\n' + _json_text({'run': str(run), 'stage': status.get('stage'),
            'next_action': status.get('next_action'), 'workflow_completion': status.get('workflow_completion'),
            'blockers': status.get('blockers', [])})[:5000] + '\n按当前下一步继续；实际判断由宿主完成，进度以任务证据为准。'


def _continue(state: dict, config: dict, checkpoint: str, reason: str, *, waiting: bool = False) -> dict:
    state['stagnant_stops'] = state.get('stagnant_stops', 0) + 1 if state.get('checkpoint') == checkpoint else 0
    state['checkpoint'] = checkpoint
    limit = 1 if waiting else config['max_stagnant_stops']
    if state['stagnant_stops'] >= limit or state.get('continuations', 0) >= config['max_continuations']:
        state.update(mode='paused', turn_active=False, pause_reason='no_progress_limit')
        return {'systemMessage': 'HEX 接续达到有限停滞上限；保留原任务，请准确报告当前等待或阻塞，不宣称完成。'}
    state['continuations'] = state.get('continuations', 0) + 1
    reason = 'HEX 当前任务接续：' + reason[:4500] + '\n完成后重新读取任务状态；不扩大授权，不重发未知请求。'
    state['continuation_prompt_sha256'] = sha(reason.encode('utf-8'))
    return {'decision': 'block', 'reason': reason}


def _query_action(action: dict) -> bool:
    """Only known read-only recovery commands can continue a pending request."""
    supported = {'query', 'query-comfy', 'query-pippit'}
    command = action.get('command')
    if isinstance(command, str):
        return command in supported
    # Status may expose the invocation as an argv array. Restrict it to a
    # recognized HEX entry, not a query-looking token in arbitrary shell code.
    if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
        return False
    if len(command) >= 4 and command[1:3] in (['-m', 'hex_agent'], ['-m', 'liu_agent']):
        return command[3] in supported
    if len(command) >= 3 and Path(command[1]).name == 'hex_entry.py':
        return command[2] in supported
    return bool(command and command[0] in supported)


def _returned_receipt_action(state: dict, run: Path, status: dict) -> tuple[str, dict] | None:
    """A witnessed return permits one reconciliation, never another submission."""
    observed = state.get('last_tool_observation')
    if (not isinstance(observed, dict) or observed.get('run') != str(run)
            or status.get('stage') != 'awaiting_result'
            or status.get('next_action', {}).get('command') != 'record-result'):
        return None
    # stop-generation forbids new submissions, not registration of an already
    # witnessed original return. The same request/session/argument checks and
    # one-reconciliation limit still apply; observation is not a result receipt.
    from .host import read_dispatches
    attempts = read_dispatches(run)
    pending = [row for row in attempts if row['status'] == 'pending'
               and row['request']['id'] == observed.get('request_id')]
    if len(pending) != 1 or any(row['status'] == 'outcome_unknown' for row in attempts):
        return None
    request = pending[0]['request']
    invocation = observed.get('tool_use_id')
    if not isinstance(invocation, str) or not invocation:
        raise AgentError('已观察工具返回缺少原 tool_use_id，不能接续登记')
    folder = run / 'codex-hook-observations' / sha(invocation.encode('utf-8'))
    receipt = _object(folder / 'observation.json')
    actual = _object(folder / 'actual-arguments.json')
    if (digest(receipt) != observed.get('receipt_digest') or receipt.get('scope') != state['scope']
            or receipt.get('request_id') != request['id'] or receipt.get('tool_use_id') != invocation
            or receipt.get('tool_name') != request['tool']
            or receipt.get('arguments_digest') != digest(request['arguments'])
            or actual.get('request_id') != request['id'] or actual.get('arguments') != request['arguments']
            or receipt.get('completion_authority') is not False):
        raise AgentError('工具返回观察与当前会话、请求或实参不一致，不能接续登记')
    return digest({'returned_receipt': observed}), {
        'command': 'record-result', 'owner': 'host_model', 'request_ids': [request['id']],
        'tool_use_id': invocation, 'actual_arguments_file': str(folder / 'actual-arguments.json'),
        'description': '本会话的原工具调用已有返回，当前仅缺真实回执登记。核对原 tool_use_id 的实际输出，'
                       '沿 record-result 如实登记 completed/failed/outcome_unknown；没有图片就不伪造 completed。'
                       '这只允许一次核对与登记，不创建 dispatch，不重新调用生成工具，不把 hook 观察当作完成。'}


def _stop(config: dict, state: dict, event: dict) -> dict:
    if not state.get('turn_active') or state.get('mode') in ('paused', 'idle') or event.get('permission_mode') == 'plan':
        return {}
    if not state.get('run'):
        return _continue(state, config, 'needs_binding', _entry_context(config, state)) if state.get('pending_has_task') else {}
    run, _ = _bound_packet(config, state)
    status = _inspect(run)
    action = status.get('next_action') or {}
    completion = status.get('workflow_completion') or {}
    if status.get('blockers'):
        return {'systemMessage': 'HEX 当前任务有真实阻塞：' + '；'.join(map(str, status['blockers']))[:2000]}
    if (completion.get('complete') is True and status.get('stage') == 'exported') or (completion.get('applicable') is not True and
            status.get('stage') in ('completed', 'awaiting_feedback', 'exported')):
        return {}
    state['last_stop_hook_active'] = event.get('stop_hook_active') is True
    if action.get('requires_user_input') is True:
        return {}
    # Kernel pending cannot distinguish a host reply that has not yet been
    # registered. Only this session's validated PostToolUse observation adds
    # the limited ability to inspect/register that original reply once.
    returned = _returned_receipt_action(state, run, status)
    if returned is not None:
        checkpoint, reconciliation = returned
        return _continue(state, config, checkpoint, 'run=' + str(run) + '\n' + _json_text(reconciliation), waiting=True)
    if action.get('executable') is not True:
        return {}
    waiting = action.get('wait_for_existing_request') is True
    if waiting and not _query_action(action):
        return {}
    checkpoint = digest({'stage': status.get('stage'), 'action': action,
                         'completion': completion, 'budget': status.get('budget'),
                         'candidates': status.get('candidates')})
    return _continue(state, config, checkpoint, 'run=' + str(run) + '\n' + _json_text(action), waiting=waiting)


def _generation_pre(config: dict, state: dict, event: dict) -> dict:
    if not state.get('run'):
        return _deny('当前 HEX 创作尚未绑定真实任务；先 prepare 和 codex bind，再沿 dispatch 执行。')
    from .host import read_dispatches
    from .generation_authorization import submission_guard
    run, _ = _bound_packet(config, state)
    attempts = read_dispatches(run)
    pending = [row for row in attempts if row['status'] == 'pending']
    if any(row['status'] == 'outcome_unknown' for row in attempts):
        return _deny('当前 HEX 请求结果未知；查询或登记原请求，不重新生成。')
    if len(pending) != 1:
        return _deny('未找到唯一的 HEX pending dispatch；先按当前任务状态准备合法调用。')
    attempt, invocation = pending[0], event.get('tool_use_id')
    request = attempt['request']
    if request['tool'] != event['tool_name'] or request['arguments'] != event.get('tool_input'):
        return _deny('工具名称或实参与 HEX 冻结 dispatch 不一致；读取原始 arguments，不改写调用。')
    if not isinstance(invocation, str) or not invocation:
        return _deny('工具调用缺少 tool_use_id，无法可靠核对调用前记录。')
    with submission_guard(run, attempt):
        status = _inspect(run, _dispatch_lock_held=True)
        if status.get('blockers'):
            return _deny('HEX 调用证据未通过核验：' + '；'.join(map(str, status['blockers']))[:1800])
        path = run / 'codex-hook-observations' / 'claims.json'
        claims = _object(path) if path.is_file() else {'format': 'hex.codex-tool-claims.v1', 'requests': {}}
        claim = {'session_scope': state['scope'], 'tool_use_id': invocation,
                 'tool_name': event['tool_name'], 'arguments_digest': digest(event['tool_input'])}
        previous = claims['requests'].get(request['id'])
        if previous and previous != claim:
            return _deny('该 dispatch 已对应一次工具调用，结果未完成登记；核对原回执，不重发。')
        if not previous:
            claims['requests'][request['id']] = claim
            write_json(path, claims)
    state['last_dispatch_claim'] = {'run': str(run), 'request_id': request['id'], **claim}
    return {}


def _generation_post(config: dict, state: dict, event: dict) -> dict:
    from .host import read_dispatches
    run, _ = _bound_packet(config, state)
    claims_path = run / 'codex-hook-observations' / 'claims.json'
    claims = _object(claims_path) if claims_path.is_file() else {'requests': {}}
    matches = [(ident, row) for ident, row in claims['requests'].items()
               if row['session_scope'] == state['scope'] and row['tool_use_id'] == event.get('tool_use_id')]
    if len(matches) != 1:
        return _context('PostToolUse', '此生成工具结果缺少可对应的 HEX 调用前观察；不要伪造已绑定结果。核对原任务与真实回执。')
    ident, claim = matches[0]
    attempts = read_dispatches(run)
    attempt = next((row for row in attempts if row['request']['id'] == ident), None)
    if (not attempt or claim['tool_name'] != event['tool_name'] or
            event.get('tool_input') != attempt['request']['arguments']):
        raise AgentError('工具返回时实参与 HEX dispatch 不一致；不能登记 completed')
    folder = run / 'codex-hook-observations' / sha(event['tool_use_id'].encode('utf-8'))
    args = folder / 'actual-arguments.json'
    response = event.get('tool_response')
    receipt = {'format': 'hex.codex-tool-observation.v1', 'request_id': ident,
               'tool_use_id': event['tool_use_id'], 'tool_name': event['tool_name'],
               'scope': state['scope'], 'arguments_digest': digest(event['tool_input']),
               'response_digest': digest(response),
               'is_error': response.get('isError') if isinstance(response, dict) else None,
               'authority': 'local_hook_observation_not_remote_execution_verification',
               'completion_authority': False}
    if (folder / 'observation.json').is_file():
        if _object(folder / 'observation.json') != receipt:
            raise AgentError('同一工具调用返回了冲突观察；保留原记录并核对真实结果')
    else:
        write_json(args, {'request_id': ident, 'arguments': event['tool_input'],
                         'origin': 'codex_hook_observed_tool_input'})
        write_json(folder / 'observation.json', receipt)
    state['last_tool_observation'] = {'run': str(run), 'request_id': ident,
                                     'tool_use_id': event['tool_use_id'], 'receipt_digest': digest(receipt)}
    return _context('PostToolUse', 'HEX 已记录本次实际工具参数：' + str(args) +
                    '\n使用真实输出与原 request-id 沿 record-result 登记，再读取下一步。'
                    '此观察不等于图片已登记、视觉复核完成或用户接受；未知结果不重发。')


def _prepare_response(config: dict, event: dict) -> dict | None:
    if event.get('tool_name') not in ('Bash', 'exec_command'):
        return None
    args = event.get('tool_input')
    command = args.get('command', args.get('cmd', '')) if isinstance(args, dict) else ''
    # A narrow recognizer, not a shell security policy. Compound shells and
    # ambiguous output require explicit bind instead of guessing a recent run.
    if not isinstance(command, str) or re.search(r'[;|\n\r]', command):
        return None
    try:
        words = shlex.split(command.replace('\\', '/'))
    except ValueError:
        return None
    if words and words[0] == '&':
        words = words[1:]
    python = bool(words and re.fullmatch(r'python(?:\d+(?:\.\d+)*)?(?:\.exe)?', Path(words[0]).name, re.I))
    if python and len(words) >= 4 and words[1:3] in (['-m', 'hex_agent'], ['-m', 'liu_agent']):
        tail = words[3:]
    elif python and len(words) >= 3 and Path(words[1]).name == 'hex_entry.py':
        actual = (Path(event['cwd']) / words[1]).resolve()
        if actual != Path(config['entry_script']).resolve():
            return None
        tail = words[2:]
    elif words and Path(words[0]).name.lower() in ('hex.ps1', 'hex-agent', 'hex-agent.exe'):
        tail = words[1:]
    else:
        return None
    if not tail or tail[0] not in ('prepare', 'run', 'car') or tail[0] == 'car' and tail[1:2] != ['prepare']:
        return None
    response = event.get('tool_response')
    if not isinstance(response, dict) or response.get('isError') or response.get('exit_code') not in (None, 0):
        return None
    value = response
    output = value.get('output', value.get('stdout'))
    if isinstance(output, str):
        try:
            value = json.loads(output)
        except ValueError:
            return None
    if (isinstance(value, dict) and not value.get('error') and isinstance(value.get('run'), str)
            and Path(value['run']).is_absolute()):
        return value
    return None


def handle_event(config: dict, event: dict) -> dict:
    name = event.get('hook_event_name')
    if name not in EVENTS:
        return {}
    workspace = event.get('cwd')
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise AgentError('Codex hook 缺少有效 cwd')
    folder, scope = _scope(config, event.get('session_id'), Path(workspace))
    folder.mkdir(parents=True, exist_ok=True)
    with _metadata_guard(folder):
        state = _load_state(folder, scope)
        state['last_event'] = {'name': name, 'turn_id': event.get('turn_id'), 'at': now()}
        result = {}
        if name == 'Interrupt':
            state.update(mode='paused', turn_active=False, pause_reason='user_interrupt')
        elif name == 'UserPromptSubmit':
            prompt = event.get('prompt')
            if not isinstance(prompt, str):
                raise AgentError('UserPromptSubmit 缺少原始 prompt')
            prompt_hash = sha(prompt.encode('utf-8'))
            key = digest([name, event.get('turn_id'), prompt_hash])
            if prompt_hash == state.get('continuation_prompt_sha256'):
                result = _context(name, '这是 HEX 对当前任务的接续，不是新的用户生成许可。')
            elif key not in state['event_keys']:
                intent = prompt_intent(prompt, bound=bool(state.get('run')))
                state['turn_id'] = event.get('turn_id')
                state['event_keys'] = (state['event_keys'] + [key])[-80:]
                if intent in ('disabled', 'unrelated'):
                    state['turn_active'] = False
                elif intent == 'paused':
                    state.update(mode='paused', turn_active=False, pause_reason='user_prompt')
                elif intent == 'resume':
                    state.update(mode='active', turn_active=True, stagnant_stops=0, continuations=0, checkpoint=None)
                    result = _context(name, _status_context(config, state))
                else:
                    state.update(run=None, mode='pending', turn_active=True, pending_prompt_sha256=prompt_hash,
                                 pending_has_task=intent == 'activate', stagnant_stops=0, continuations=0,
                                 checkpoint=None)
                    atomic_text(folder / 'user-request.txt', prompt)
                    result = _context(name, _entry_context(config, state))
        elif name == 'SessionStart' and state.get('turn_active'):
            result = _context(name, _status_context(config, state) if state.get('run') else _entry_context(config, state))
        elif name == 'Stop':
            result = _stop(config, state, event)
        elif name in ('PreToolUse', 'PostToolUse') and state.get('turn_active'):
            tool = event.get('tool_name')
            if tool in config['generation_tools']:
                if name == 'PreToolUse':
                    try:
                        result = _generation_pre(config, state, event)
                    except _ERRORS as exc:
                        result = _deny('HEX 调用核对受阻：' + str(exc))
                elif state.get('run'):
                    result = _generation_post(config, state, event)
            elif name == 'PostToolUse' and state.get('mode') == 'pending':
                response = _prepare_response(config, event)
                if response:
                    _bind(config, state, Path(response['run']), automatic=True)
                    if response.get('digest') not in (None, state['bound_packet_digest']):
                        raise AgentError('prepare 回执摘要与当前 packet 不一致')
                    result = _context(name, _status_context(config, state))
        write_json(folder / 'session.json', state)
        return result


def session_status(config: dict, session_id: str, workspace: Path) -> dict:
    folder, scope = _scope(config, session_id, workspace)
    state = _load_state(folder, scope)
    result = {'format': 'hex.codex-session-status.v1', 'binding': state,
              'hook_trust': 'not_inspected', 'host_behavior': 'observed_event_only' if state.get('last_event') else 'not_tested'}
    if state.get('run'):
        run, _ = _bound_packet(config, state)
        result['task'] = _inspect(run)
    return result


def pause(config: dict, session_id: str, workspace: Path) -> dict:
    folder, scope = _scope(config, session_id, workspace)
    folder.mkdir(parents=True, exist_ok=True)
    with _metadata_guard(folder):
        state = _load_state(folder, scope)
        state.update(mode='paused', turn_active=False, pause_reason='explicit_pause')
        write_json(folder / 'session.json', state)
    return {'state': 'paused', 'run': state.get('run'),
            'notice': '停止自动接续；不取消远程请求、不退还预留。未知请求仍只查原编号。'}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='hex-agent codex')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('install', help='合并可审阅的本机集成；不改 hook 信任')
    command.add_argument('--codex-dir', type=Path)
    command.add_argument('--skills-dir', type=Path)
    command.add_argument('--run-root', type=Path, action='append')
    command.add_argument('--generation-tool', action='append')
    command.add_argument('--dry-run', action='store_true')
    for name in ('hook', 'bind', 'status', 'pause'):
        command = commands.add_parser(name)
        command.add_argument('--config', type=Path, default=default_config())
        if name != 'hook':
            command.add_argument('--session-id', required=name != 'status')
            command.add_argument('--workspace', type=Path)
        if name == 'bind':
            command.add_argument('--run', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'install':
            result = install(codex_dir=args.codex_dir, skills_dir=args.skills_dir,
                             run_roots=args.run_root, generation_tools=args.generation_tool,
                             dry_run=args.dry_run)
        else:
            config = configuration(args.config)
            if args.command == 'hook':
                raw = sys.stdin.read(_MAX_INPUT + 1)
                if len(raw.encode('utf-8')) > _MAX_INPUT:
                    raise AgentError('hook 输入超过 16 MiB；未复制图像或猜测结果')
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise AgentError('hook 输入必须为 JSON 对象')
                result = handle_event(config, event)
            elif args.command == 'status' and not args.session_id:
                result = {'config': config, 'hook_trust': 'not_inspected', 'host_behavior': 'not_tested',
                          'next': '用 --session-id 和 --workspace 查看该会话的真实 hook 观察；/hooks 查看平台信任。'}
            else:
                workspace = (args.workspace or Path.cwd()).resolve()
                if args.command == 'bind':
                    result = bind_run(config, args.session_id, workspace, args.run)
                elif args.command == 'pause':
                    result = pause(config, args.session_id, workspace)
                else:
                    result = session_status(config, args.session_id, workspace)
        print(_json_text(result), end='')
        return 0
    except _ERRORS as exc:
        if args.command == 'hook':
            print(_json_text({'systemMessage': 'HEX hook 未完成核对：' + str(exc)[:2000]}), end='')
            return 0
        print(_json_text({'error': str(exc), 'state': 'blocked'}), file=sys.stderr, end='')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
