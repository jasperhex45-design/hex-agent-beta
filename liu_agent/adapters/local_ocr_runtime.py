"""Run the installed, local CPU OCR environment for explicit frozen checks.

The main Hex environment never imports Paddle. The existing runtime owns model
verification and inference; this bridge owns process, source and result binding.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from time import perf_counter

from ..io import AgentError, digest, read_json, sha
from .ocr_review import _requirements, review_text


ROOT = Path(__file__).resolve().parents[2]


def validate_ocr_requirements(contract: dict, requirements: list) -> list:
    if not isinstance(requirements, list) or any(not isinstance(row, dict) for row in requirements):
        raise AgentError('OCR要求须为JSON列表，每项显式绑定已有criterion_id与exact_text')
    try:
        snapshot = json.loads(json.dumps(requirements, ensure_ascii=False, allow_nan=False))
        return _requirements(contract, snapshot)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise AgentError('OCR要求格式无效；须沿冻结检查提供文字与可选原画布区域') from exc


def run_local_ocr(image: Path, contract: dict, requirements: list, *,
                  contract_path: Path, expected_candidate_sha256: str | None = None,
                  timeout_seconds: float = 180, project_root: Path | None = None) -> dict:
    """Make at most one subprocess call; never install, download, or retry.

    ``contract_path`` must be the original frozen contract, not an OCR-specific
    replacement. Empty requirements return before probing the optional runtime.
    """
    # Round-trip a snapshot: callers cannot change the requirements mid-call.
    requirements = validate_ocr_requirements(contract, requirements)
    if not requirements:
        result = review_text(image, contract, [], engine=None)
        result['local_process'] = {'state': 'not_started', 'calls': 0, 'elapsed_seconds': 0.0}
        return result
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise AgentError('OCR进程超时须为正的有限秒数')
    contract_path, image = Path(contract_path).resolve(), Path(image).resolve()
    source_contract = contract_path.read_bytes()
    if read_json(contract_path) != contract:
        raise AgentError('OCR须读取原任务的同一份冻结合同')
    candidate_sha = sha(image.read_bytes())
    if expected_candidate_sha256 and candidate_sha != expected_candidate_sha256:
        raise AgentError('启动OCR前候选图片已变化')
    root = Path(project_root or ROOT).resolve()
    python = root / '.runtime' / 'ocr' / 'venv' / 'Scripts' / 'python.exe'
    runner = root / 'scripts' / 'runtime' / 'ocr_runtime.py'
    result = review_text(image, contract, requirements, engine=None)
    result.update(candidate_sha256=candidate_sha, requirements_digest=digest(requirements),
                  contract_file=str(contract_path))
    process = {'state': 'not_started', 'calls': 0, 'elapsed_seconds': 0.0,
               'device_requested': 'cpu', 'timeout_seconds': timeout_seconds,
               'network_policy': 'existing runner denies socket connect and DNS; local verified weights only'}
    result['local_process'] = process
    if not python.is_file() or not runner.is_file():
        result['reason'] = '本机OCR运行环境缺失，未执行文字核对；请修复现有安装'
        return result
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    started = perf_counter()
    try:
        # Keep scratch with the private runtime; Windows sandbox temp roots can
        # be unreadable to the real-user runtime. It is removed after the call.
        with tempfile.TemporaryDirectory(prefix='hex-ocr-check-', dir=root / '.runtime' / 'ocr') as temporary:
            scratch = Path(temporary)
            req_file, out_file = scratch / 'requirements.json', scratch / 'result.json'
            req_file.write_text(json.dumps(requirements, ensure_ascii=False), encoding='utf-8')
            command = [str(python), str(runner), 'review', '--image', str(image),
                       '--contract', str(contract_path), '--requirements', str(req_file), '--out', str(out_file)]
            process.update(state='started', calls=1)
            # Unknown inference consumption must not be relabeled zero on a
            # killed or failed process. A valid child receipt supplies the count.
            result['usage']['model_calls'] = None
            completed = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                       timeout=timeout_seconds, check=False,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            process.update(state='exited', exit_code=completed.returncode)
            if not out_file.is_file():
                result.update(state='failed', reason='本地OCR进程未产生有效回执；未重试')
                return result
            child = read_json(out_file)
            if not isinstance(child, dict) or child.get('contract_digest') != contract['digest']:
                raise AgentError('OCR回执的冻结合同绑定不符')
            if child.get('candidate_sha256') not in (None, candidate_sha):
                raise AgentError('OCR回执的候选图片绑定不符')
            if sha(image.read_bytes()) != candidate_sha or contract_path.read_bytes() != source_contract:
                raise AgentError('OCR期间原图或冻结合同发生变化，不能绑定结果')
            if child.get('state') == 'completed':
                runtime = child.get('runtime_evidence') or {}
                if (child.get('candidate_sha256') != candidate_sha or
                        runtime.get('device') != 'cpu' or runtime.get('network_attempts_blocked') != 0 or
                        child.get('usage', {}).get('model_calls') != 1 or completed.returncode != 0):
                    raise AgentError('OCR回执缺少成功的CPU本地运行证据')
                if [item.get('criterion_id') for item in child.get('items', [])] != [
                        item['criterion_id'] for item in requirements]:
                    raise AgentError('OCR回执未完整覆盖本次明确文字要求')
                process.update(device_observed='cpu', network_attempts_blocked=0,
                               external_calls_by_adapter=0)
            elif child.get('state') not in {'failed', 'unavailable'}:
                raise AgentError('OCR回执状态无效，不能作为完成记录')
            result = {**child, 'candidate_sha256': candidate_sha,
                      'requirements_digest': digest(requirements), 'contract_file': str(contract_path),
                      'local_process': process}
    except subprocess.TimeoutExpired:
        process['state'] = 'timed_out'
        result.update(state='failed', reason='本地OCR进程超时并已停止；本次识别用量未知，未重试')
    except (OSError, ValueError, KeyError, TypeError, AgentError) as exc:
        result.update(state='failed', reason='本地OCR运行或来源核验失败：' + str(exc))
    finally:
        process['elapsed_seconds'] = perf_counter() - started
    return result
