"""Execute one frozen CLI dispatch; recovery queries never submit another image.

Receipts document this host's actual subprocess, not provider attestation or
visual acceptance. A durable submission marker is written before the call.
"""
from pathlib import Path
import json
import subprocess
import time
import uuid

from . import host
from .io import AgentError, digest, now, read_json, sha, within, write_json
from .pippit_connector import TOOL_NAME, build_cli_arguments
from .run_lock import dispatch_lock


def _attempt(run: Path, request_id: str) -> dict:
    attempt = next((a for a in host.read_dispatches(run)
                    if a['request']['id'] == request_id), None)
    if attempt is None or attempt['request']['tool'] != TOOL_NAME:
        raise AgentError('未找到对应的小云雀 dispatch；不能直接提交')
    return attempt


def _directory(run: Path, request_id: str) -> Path:
    directory = within(run, f'pippit/{request_id}')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cli(path: Path) -> str:
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() in ('.cmd', '.bat', '.ps1', '.sh'):
        raise AgentError('请提供已核实的 CLI 可执行文件路径，不接受 shell 脚本')
    return str(path)


def _read_state(directory: Path, attempt: dict) -> dict | None:
    path = directory / 'execution.json'
    if not path.exists():
        evidence = ('actual-arguments.json', 'submit-argv.json', 'submit-response.json')
        if any((directory / name).exists() for name in evidence) or any(directory.glob('query-*')):
            raise AgentError('CLI 执行状态丢失但已有调用证据；核对原提交，禁止重发')
        if (directory / 'diagnostic.json').exists():
            diagnostic = _record(directory / 'diagnostic.json')
            if (not isinstance(diagnostic, dict) or diagnostic.get('phase') != 'prepare'
                    or diagnostic.get('submission_boundary_crossed') is not False
                    or diagnostic.get('request_id') != attempt['request']['id']):
                raise AgentError('CLI 执行状态丢失且无法证明未提交；禁止重发')
        return None
    state = _record(path)
    phases = ('submitting', 'submitted', 'outcome_unknown', 'query_failed', 'pending', 'completed', 'failed')
    if (not isinstance(state, dict) or state.get('format') != 'hex.pippit-execution.v1'
            or state.get('state') not in phases):
        raise AgentError('CLI 执行状态格式或阶段无效；核对原提交，禁止重发')
    if (state.get('request_id') != attempt['request']['id'] or
            state.get('request_digest') != attempt['request_digest'] or
            state.get('arguments_digest') != digest(attempt['request']['arguments'])):
        raise AgentError('CLI 执行记录与冻结调用不一致；停止恢复')
    if _record(directory / 'actual-arguments.json') != attempt['request']['arguments']:
        raise AgentError('CLI 实参记录已变化；停止恢复')
    # Submission response is saved before updating state, so a crash between
    # those writes can recover IDs without repeating the paid command.
    ids = [state.get(key) for key in ('thread_id', 'run_id')]
    if any(value is not None for value in ids) and not all(_valid_id(value) for value in ids):
        raise AgentError('CLI 原任务标识缺失或无效；禁止查询或重发')
    response_path = directory / 'submit-response.json'
    response = _record(response_path) if response_path.exists() else None
    if response_path.exists() and not isinstance(response, dict):
        raise AgentError('CLI 提交回执格式无效；禁止查询或重发')
    recovered = {}
    has_ids = _attach_ids(recovered, response)
    if any(value is not None for value in ids):
        if not has_ids or any(state[key] != recovered[key] for key in ('thread_id', 'run_id')):
            raise AgentError('CLI 原任务标识与保存的提交回执不一致；禁止查询或重发')
    elif has_ids:
        state.update(recovered)
    return state


def _record(path: Path):
    try:
        return read_json(path)
    except (OSError, ValueError, RecursionError) as exc:
        raise AgentError('CLI 保存的执行证据缺失或损坏；核对原提交，禁止重发') from exc


def _valid_id(value) -> bool:
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and len(value) <= 500 and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _attach_ids(state: dict, response: dict) -> bool:
    if not isinstance(response, dict):
        return False
    ids = {key: response.get(key) for key in ('thread_id', 'run_id')}
    if not all(_valid_id(value) for value in ids.values()):
        return False
    state.update(ids)
    link = response.get('web_thread_link')
    if isinstance(link, str):
        state['web_thread_link'] = link
    state['state'] = 'submitted'
    return True


def next_action(run: Path, request_id: str) -> dict:
    """Inspect the original submission without creating files or invoking CLI."""
    run = run.resolve()
    attempt = _attempt(run, request_id)
    state = _read_state(within(run, 'pippit/' + request_id), attempt)
    result = {'command': None, 'description': '核对原提交记录；禁止重发', 'request_ids': [request_id]}
    if attempt['status'] == 'pending' and state is None:
        return {**result, 'command': 'execute-pippit',
                'description': '使用已核实的 CLI 执行此尚未提交的冻结调用'}
    if attempt['status'] in ('pending', 'outcome_unknown') and state and state.get('thread_id'):
        return {**result, 'command': 'query-pippit',
                'description': '使用保存的 thread_id/run_id 查询原任务；禁止重新生成'}
    return result


def _diagnostic(directory: Path, state: dict, phase: str, error_type=None,
                return_code=None, duration=0.0, submission_boundary_crossed=True):
    # Closed categories only: never include provider/exception text or argv.
    state['diagnostic'] = {
        'phase': phase, 'return_code': return_code, 'error_type': error_type,
        'request_id': state['request_id'], 'duration': duration,
        'retry_policy': ('query_same_task' if phase in ('query', 'download')
                         else 'never_resubmit' if phase == 'submit'
                         else 'correct_local_error'),
        'submission_boundary_crossed': submission_boundary_crossed,
    }
    write_json(directory / 'diagnostic.json', state['diagnostic'])


def _classify(directory: Path, state: dict, error_type: str, phase=None):
    previous = state['diagnostic']
    _diagnostic(directory, state, phase or previous['phase'], error_type,
                previous['return_code'], previous['duration'],
                previous['submission_boundary_crossed'])


def _call(argv: list[str], timeout: int, directory: Path, state: dict, phase: str) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(argv, shell=False, capture_output=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _diagnostic(directory, state, phase,
                    'timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'os_error',
                    duration=round(time.monotonic() - started, 6),
                    submission_boundary_crossed=(phase != 'submit' or
                                                 isinstance(exc, subprocess.TimeoutExpired)))
        raise AgentError('CLI 调用未取得确定回执；不能重发生图') from exc
    _diagnostic(directory, state, phase,
                'nonzero_exit' if result.returncode != 0 else None,
                result.returncode, round(time.monotonic() - started, 6))
    try:
        response = json.loads(result.stdout.lstrip('\ufeff'))
    except (ValueError, TypeError) as exc:
        _classify(directory, state, 'invalid_json')
        raise AgentError('CLI 未返回有效 JSON；保留原调用，不重发') from exc
    if not isinstance(response, dict):
        _classify(directory, state, 'invalid_response')
        raise AgentError('CLI 回执格式无效；保留原调用，不重发')
    # Do not expose stderr (which can contain local authentication details).
    response['_cli_returncode'] = result.returncode
    return response


def _save(directory: Path, state: dict) -> dict:
    state['updated_at'] = now()
    write_json(directory / 'execution.json', state)
    return state


def _unknown(run: Path, request_id: str, directory: Path, state: dict, reason: str) -> dict:
    state.update(state='outcome_unknown', problem=reason)
    _save(directory, state)
    attempt = _attempt(run, request_id)
    if attempt['status'] in ('pending', 'outcome_unknown'):
        host.record_result(run, request_id, 'outcome_unknown', None, reason)
    return state


def execute(run: Path, request_id: str, cli_path: Path, *, timeout: int = 45) -> dict:
    """At most one generate-image subprocess for this dispatch."""
    run = run.resolve()
    attempt = _attempt(run, request_id)
    directory = _directory(run, request_id)
    with dispatch_lock(directory):
        attempt = _attempt(run, request_id)
        state = _read_state(directory, attempt)
        if state is not None:
            if state['state'] == 'submitting' and not state.get('thread_id'):
                return _unknown(run, request_id, directory, state,
                                '先前提交中断，结果未知；核对现有任务，禁止重新生成')
            return _save(directory, state)
        if attempt['status'] != 'pending':
            raise AgentError('只有尚未提交的 pending dispatch 可执行；未知或已结束调用不能重发')
        request = attempt['request']
        for ref in request['images_in_order']:
            if sha(within(run, ref['file']).read_bytes()) != ref['sha256']:
                raise AgentError('参考图在 dispatch 后发生变化；未提交')
        try:
            cli = _cli(cli_path)
        except AgentError:
            _diagnostic(directory, {'request_id': request_id}, 'prepare',
                        'invalid_executable', submission_boundary_crossed=False)
            raise
        try:
            argv = build_cli_arguments(cli, request['arguments'])
        except (AgentError, ValueError, TypeError):
            _diagnostic(directory, {'request_id': request_id}, 'prepare',
                        'invalid_arguments', submission_boundary_crossed=False)
            raise
        from .generation_authorization import submission_guard
        submit_error = None
        with submission_guard(run, attempt):
            write_json(directory / 'actual-arguments.json', request['arguments'])
            write_json(directory / 'submit-argv.json', argv)
            state = {'format': 'hex.pippit-execution.v1', 'request_id': request_id,
                     'request_digest': attempt['request_digest'],
                     'arguments_digest': digest(request['arguments']),
                     'cli_path': cli, 'state': 'submitting', 'started_at': now()}
            _save(directory, state)
            try:
                response = _call(argv, timeout, directory, state, 'submit')
            except AgentError as exc:
                submit_error = str(exc)
        if submit_error is not None:
            return _unknown(run, request_id, directory, state, submit_error)
        write_json(directory / 'submit-response.json', response)
        if not _attach_ids(state, response):
            if not state['diagnostic']['error_type']:
                _classify(directory, state, 'missing_task_ids')
            return _unknown(run, request_id, directory, state,
                            '提交未返回完整任务标识；请核对服务端已有任务，不能重发')
        if response.get('error_message') or response['_cli_returncode'] != 0:
            if not state['diagnostic']['error_type']:
                _classify(directory, state, 'provider_error')
            state['problem'] = '提交同时返回错误与任务标识；仅查询原任务核对'
        return _save(directory, state)


def query(run: Path, request_id: str, *, timeout: int = 45) -> dict:
    """Query/download once using persisted IDs, then bind the actual result."""
    run = run.resolve()
    attempt = _attempt(run, request_id)
    directory = _directory(run, request_id)
    with dispatch_lock(directory):
        attempt = _attempt(run, request_id)
        state = _read_state(directory, attempt)
        if not state or not state.get('thread_id') or not state.get('run_id'):
            raise AgentError('没有可查询的完整任务标识；核对已有回执，不能重新生成')
        if attempt['status'] in ('completed', 'failed'):
            state.update(state=attempt['status'], output=attempt.get('output'))
            return _save(directory, state)
        download = within(directory, f'query-{uuid.uuid4().hex}')
        download.mkdir()
        try:
            cli = _cli(Path(state['cli_path']))
        except AgentError:
            _diagnostic(directory, state, 'query', 'invalid_executable')
            state.update(state='query_failed', problem='查询用 CLI 不可用；只恢复原任务查询')
            return _save(directory, state)
        argv = [cli, 'query-result',
                '--thread-id', state['thread_id'], '--run-id', state['run_id'],
                '--download-dir', str(download)]
        write_json(download / 'argv.json', argv)
        try:
            response = _call(argv, timeout, directory, state, 'query')
        except AgentError as exc:
            state.update(state='query_failed', problem=str(exc))
            return _save(directory, state)
        write_json(download / 'response.json', response)
        state['last_query'] = str(download.relative_to(directory))
        for key in ('thread_id', 'run_id'):
            if response.get(key) != state[key]:
                _classify(directory, state, 'task_id_mismatch')
                state.update(state='query_failed', problem='查询回执任务标识不一致，未登记结果')
                return _save(directory, state)
        # Error payloads are not success, even if exit status is zero. Query
        # failures remain recoverable on the same IDs (e.g. download failure).
        if response.get('error_message') or response['_cli_returncode'] != 0:
            if not state['diagnostic']['error_type']:
                _classify(directory, state, 'query_error')
            state.update(state='query_failed', problem='CLI 查询或下载报错；只重查本任务')
            return _save(directory, state)
        if type(response.get('completed')) is not bool:
            _classify(directory, state, 'invalid_completion')
            state.update(state='query_failed', problem='查询未返回有效完成状态；不能推断生成进度')
            return _save(directory, state)
        if not response['completed']:
            state.update(state='pending', problem=None)
            return _save(directory, state)
        images = response.get('images')
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
            _classify(directory, state, 'invalid_images', 'download')
            state.update(state='query_failed', problem='任务结束但未取得唯一图片；核对服务端结果')
            return _save(directory, state)
        try:
            output = images[0].get('output_path')
            if not isinstance(output, str) or not output:
                raise AgentError('回执缺少下载文件')
            image = within(download, output)
            if not image.is_file() or image.stat().st_size == 0:
                raise AgentError('下载结果为空或不存在')
            result = host.record_result(run, request_id, 'completed', image,
                                        'pippit CLI 实际提交与同任务查询回执；不代表视觉验收',
                                        directory / 'actual-arguments.json')
        except (AgentError, OSError, ValueError):
            _classify(directory, state, 'download_error', 'download')
            state.update(state='query_failed', problem='下载文件无效、不存在或超出本次目录；只重查本任务')
            return _save(directory, state)
        state.update(state='completed', output=result['output'], problem=None)
        return _save(directory, state)
