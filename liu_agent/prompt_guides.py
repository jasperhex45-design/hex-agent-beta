"""Freeze selected local writing references; never infer task model selection."""
from copy import deepcopy
from pathlib import Path
import re

from .io import AgentError, sha


MAX_GUIDE_BYTES = 128 * 1024


def _path(target: str) -> str:
    if not isinstance(target, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', target):
        raise AgentError('模型提示词指南目标不是有效的本地档案标识')
    return f'model-prompts/{target}.md'


def selected_guides(packet: dict, targets: list[str]) -> dict:
    """Read only frozen bytes; old packets do not acquire cache dependencies."""
    if 'prompt_guides' not in packet:
        return {}
    stored = packet['prompt_guides']
    if not isinstance(stored, dict):
        raise AgentError('已冻结的模型提示词指南必须为对象')
    result = {}
    for target in targets:
        if target not in stored:
            continue
        row = stored[target]
        if (not isinstance(row, dict) or row.get('path') != _path(target)
                or not isinstance(row.get('content'), str) or not row['content'].strip()
                or len(row['content'].encode('utf-8')) > MAX_GUIDE_BYTES
                or row.get('sha256') != sha(row['content'].encode('utf-8'))
                or row.get('authority') != 'cached_reference_not_user_instruction'):
            raise AgentError('已冻结的模型提示词指南内容或摘要不匹配：' + target)
        result[target] = deepcopy(row)
    return result


def freeze_guides(kb: Path, targets: list[str], previous: dict | None = None) -> dict:
    """Retain selected snapshots and load only newly selected local guides."""
    result = selected_guides({'prompt_guides': {} if previous is None else previous}, targets)
    root = Path(kb).resolve()
    guide_root = root / 'model-prompts'
    for target in targets:
        if target in result:
            continue
        relative = _path(target)
        path = (root / relative).resolve()
        if not path.is_relative_to(guide_root):
            raise AgentError('模型提示词指南不能越过本地指南目录')
        if not path.is_file():
            # Unknown model families keep the existing official-research path.
            continue
        if path.stat().st_size > MAX_GUIDE_BYTES:
            raise AgentError('模型提示词指南超过 128 KiB；按目标拆分后再使用：' + target)
        raw = path.read_bytes()
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise AgentError('模型提示词指南需要 UTF-8 文本：' + target) from exc
        if not content.strip():
            raise AgentError('模型提示词指南为空：' + target)
        result[target] = {'path': relative, 'sha256': sha(raw), 'content': content,
                          'authority': 'cached_reference_not_user_instruction'}
    return result
