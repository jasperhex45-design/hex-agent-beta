"""Explicit host-recorded image grants, separate from consumption and budgets.

Digests bind the local evidence and detect inconsistency; they are not a platform
signature. This module neither infers permission from prose nor sends requests.
"""
import json
from contextlib import contextmanager
from pathlib import Path

from .io import AgentError, digest, read_json, write_json
from .provenance import InputSources, require_sources, verified_sources
from .run_lock import dispatch_lock


AUTHORIZATION_FILE = 'generation-authorization.json'
STOP_FILE = 'generation-stop.json'
LEGACY_FORMAT = 'liu.generation-authorization.v1'
FORMAT = 'liu.generation-authorization.v2'
ORIGIN = 'host_recorded_not_platform_authenticated'
_HOST_ORIGINS = {'host_transcription', 'host_message_record'}
_RECORD_FIELDS = {'format', 'packet_digest', 'input_sources_digest', 'origin',
                  'max_images', 'source_refs', 'history', 'digest'}
_V2_FIELDS = _RECORD_FIELDS | {'sources', 'bindings'}
_ENTRY_FIELDS = {'source_id', 'quote', 'origin', 'locator', 'source_digest',
                 'max_images', 'previous_digest', 'digest'}


def _sealed(value: dict) -> dict:
    return {**value, 'digest': digest(value)}


def _verify_seal(value: dict, fields: set[str], label: str) -> None:
    if (not isinstance(value, dict) or set(value) != fields
            or value.get('digest') != digest({k: v for k, v in value.items() if k != 'digest'})):
        raise AgentError(label + '结构或摘要不一致')


def _packet_sources(run: Path, packet: dict | None = None) -> tuple[dict, list[dict]]:
    from .workflow import read_packet
    current = read_packet(run)
    if packet is not None and packet != current:
        raise AgentError('授权检查所用packet与当前任务不一致')
    require_sources(current)
    return current, verified_sources(current['input_sources'])


def _grant(message: dict) -> int:
    if message['actor'] != 'user' or message['origin'] not in _HOST_ORIGINS:
        raise AgentError('新生图授权必须来自宿主记录的用户消息，不能使用Agent提案或历史导入')
    grant = message.get('generation_grant')
    if (not isinstance(grant, dict) or set(grant) != {'max_images'}
            or type(grant['max_images']) is not int or grant['max_images'] not in (1, 2)):
        raise AgentError('消息缺少明确的generation_grant；生图累计上限须为整数1或2，不能从原文推断')
    return grant['max_images']


def _entry(message: dict, previous_digest: str) -> dict:
    return _sealed({'source_id': message['id'], 'quote': message['text'],
                    'origin': message['origin'], 'locator': message['locator'],
                    'source_digest': digest(message), 'max_images': _grant(message),
                    'previous_digest': previous_digest})


def _require_new_message_location(source: dict, earlier: list[dict]) -> None:
    # Renaming a source ID cannot turn the same recorded user message into a
    # second grant, including when rebinding a new packet or reading old seals.
    if any(row['locator'] == source['locator'] for row in earlier):
        raise AgentError('生图授权不得改名重放同一消息定位；新增许可须来自不同的原始消息')


def _validate_v1(record: dict, packet: dict, messages: list[dict]) -> dict:
    _verify_seal(record, _RECORD_FIELDS, '生图授权')
    if record['format'] != LEGACY_FORMAT or record['origin'] != ORIGIN:
        raise AgentError('未知生图授权格式或来源')
    if record['packet_digest'] != packet['digest']:
        raise AgentError('生图授权绑定旧packet；需关联新任务版本，不能改写旧授权')
    if record['input_sources_digest'] != packet['input_sources']['digest']:
        raise AgentError('生图授权消息来源已变化')
    history = record['history']
    if not isinstance(history, list) or not 1 <= len(history) <= 2:
        raise AgentError('生图授权历史无效')
    by_id = {message['id']: (index, message) for index, message in enumerate(messages)}
    previous_digest, previous_max, previous_index = '', 0, -1
    refs = []
    for row in history:
        _verify_seal(row, _ENTRY_FIELDS, '生图授权历史')
        if not isinstance(row['source_id'], str) or row['source_id'] not in by_id:
            raise AgentError('生图授权引用的消息不存在')
        index, source = by_id[row['source_id']]
        _require_new_message_location(source, messages[:index])
        maximum = _grant(source)
        if index <= previous_index or maximum <= previous_max:
            raise AgentError('生图授权历史必须使用新增消息且累计上限严格增加')
        if type(row['max_images']) is not int or row != _entry(source, previous_digest):
            raise AgentError('生图授权历史与用户消息不一致，不能改写来源、引文或额度')
        refs.append({'source_id': source['id'], 'quote': source['text']})
        previous_digest, previous_max, previous_index = row['digest'], maximum, index
    if (type(record['max_images']) is not int or record['max_images'] != previous_max
            or record['source_refs'] != refs):
        raise AgentError('生图授权当前上限或来源引用与历史不一致')
    return record


def _sources(rows: list[dict]) -> dict:
    return _sealed({'format': 'liu.input-sources.v1', 'messages': rows})


def _append_sources(previous: dict, incoming: list[dict]) -> dict:
    rows = list(verified_sources(previous))
    by_id = {row['id']: row for row in rows}
    seen = set()
    for message in incoming:
        if message['id'] in seen:
            raise AgentError('许可消息来源ID重复')
        seen.add(message['id'])
        if message['id'] in by_id:
            if message != by_id[message['id']]:
                raise AgentError('许可消息只允许追加；不能改写已有来源')
            continue
        if message['in_reply_to'] and message['in_reply_to'] not in by_id:
            raise AgentError('许可消息引用的原始消息不存在')
        rows.append(message)
        by_id[message['id']] = message
    result = _sources(rows)
    verified_sources(result)
    return result


def _binding(packet: dict, sources: dict) -> dict:
    return {'packet_digest': packet['digest'], 'input_sources': packet['input_sources'],
            'source_count': len(verified_sources(sources)), 'sources': sources}


def _validate_v2(record: dict) -> dict:
    _verify_seal(record, _V2_FIELDS, '生图授权')
    if record['format'] != FORMAT or record['origin'] != ORIGIN:
        raise AgentError('未知生图授权格式或来源')
    if not isinstance(record['sources'], dict):
        raise AgentError('生图授权来源快照结构无效')
    rows = verified_sources(record['sources'])
    by_id = {message['id']: (index, message) for index, message in enumerate(rows)}
    history, bindings = record['history'], record['bindings']
    if (not isinstance(history, list) or not 1 <= len(history) <= 100
            or not isinstance(bindings, list) or len(bindings) != len(history)):
        raise AgentError('生图授权历史或绑定无效')
    previous_digest, previous_max, previous_index, previous_count = '', 0, -1, 0
    previous_binding = None
    refs = []
    for row, binding in zip(history, bindings):
        _verify_seal(row, _ENTRY_FIELDS, '生图授权历史')
        if (not isinstance(binding, dict)
                or set(binding) != {'packet_digest', 'input_sources', 'source_count', 'sources'}
                or not isinstance(binding['packet_digest'], str)
                or len(binding['packet_digest']) != 64
                or any(char not in '0123456789abcdef' for char in binding['packet_digest'])
                or not isinstance(binding['input_sources'], dict) or not isinstance(binding['sources'], dict)
                or type(binding['source_count']) is not int
                or not previous_count <= binding['source_count'] <= len(rows)):
            raise AgentError('生图授权历史绑定结构无效')
        if verified_sources(binding['sources']) != rows[:binding['source_count']]:
            raise AgentError('生图授权完整来源历史只允许追加，不能改写原文、grant或确认标记')
        original = verified_sources(binding['input_sources'])
        if not original or any(message['id'] not in by_id
                or by_id[message['id']][1] != message
                or by_id[message['id']][0] >= binding['source_count'] for message in original):
            raise AgentError('生图授权来源快照与原packet来源不一致')
        if not isinstance(row['source_id'], str) or row['source_id'] not in by_id:
            raise AgentError('生图授权引用的消息不存在')
        index, source = by_id[row['source_id']]
        _require_new_message_location(source, rows[:index])
        maximum = _grant(source)
        changed_packet = previous_binding and binding['packet_digest'] != previous_binding['packet_digest']
        if (index <= previous_index or index >= binding['source_count']
                or maximum < previous_max or (not changed_packet and maximum <= previous_max)):
            raise AgentError('生图授权历史必须使用新增消息且累计上限单调增加')
        if previous_binding:
            prior_original = verified_sources(previous_binding['input_sources'])
            if changed_packet:
                if index < previous_count or original[:len(prior_original)] != prior_original:
                    raise AgentError('重新绑定须有新增许可消息，且不能改写历史packet来源')
            elif binding['input_sources'] != previous_binding['input_sources']:
                raise AgentError('相同packet的原始来源快照不能改变')
        if type(row['max_images']) is not int or row != _entry(source, previous_digest):
            raise AgentError('生图授权历史与用户消息不一致，不能改写来源、引文或额度')
        refs.append({'source_id': source['id'], 'quote': source['text']})
        previous_digest, previous_max, previous_index = row['digest'], maximum, index
        previous_count, previous_binding = binding['source_count'], binding
    if (type(record['max_images']) is not int or record['max_images'] != previous_max
            or record['source_refs'] != refs or previous_count != len(rows)
            or record['packet_digest'] != previous_binding['packet_digest']
            or record['input_sources_digest'] != previous_binding['input_sources']['digest']):
        raise AgentError('生图授权当前上限、来源引用或packet绑定与历史不一致')
    return record


def _check_current(record: dict, packet: dict) -> None:
    if record['packet_digest'] != packet['digest']:
        raise AgentError('生图授权绑定旧packet；需新增许可并显式--rebind，不能自动沿用旧授权')
    if record['input_sources_digest'] != packet['input_sources']['digest']:
        raise AgentError('生图授权消息来源已变化')
    if record['format'] == FORMAT and record['bindings'][-1]['input_sources'] != packet['input_sources']:
        raise AgentError('生图授权原packet来源快照不一致')


def _upgrade_v1(record: dict, packet: dict) -> dict:
    """Preserve legacy entries exactly; an unchanged source prefix anchors them."""
    messages = verified_sources(packet['input_sources'])
    candidates = [packet['input_sources']] + [_sources(messages[:n]) for n in range(1, len(messages) + 1)]
    snapshot = next((value for value in candidates if value['digest'] == record['input_sources_digest']), None)
    if snapshot is None:
        raise AgentError('旧版授权原始消息快照无法核验；不能迁移或补造')
    old_packet = {'digest': record['packet_digest'], 'input_sources': snapshot}
    _validate_v1(record, old_packet, verified_sources(snapshot))
    return _sealed({**{key: value for key, value in record.items() if key != 'digest'},
                    'format': FORMAT, 'sources': snapshot,
                    'bindings': [_binding(old_packet, snapshot) for _ in record['history']]})


def _require_unused(run: Path) -> None:
    # Changing a planning packet must never silently detach attempted images.
    if any((run / name).exists() for name in ('host-dispatches.json', 'dispatch.json',
            'host-image-tests.json', 'image-tests.json', 'native-budget-transfer.json',
            'native-budget-receipt.json', 'native-revision-grant.json')):
        raise AgentError('已有生图预留或预算关联，不能重新绑定packet；新增许可须绑定原任务')
    if (run / 'api-state.json').exists():
        from .api_recovery import load_api_state
        if any(row['kind'] == 'image' for row in load_api_state(run)['requests']):
            raise AgentError('已有API生图预留，不能重新绑定packet')
    elif (run / 'calls.json').exists() or (run / 'api').exists():
        raise AgentError('旧API调用证据无法排除生图预留，不能重新绑定packet')


def read_authorization(run: Path, packet: dict) -> dict:
    """Read a grant only after validating its packet, original sources and history."""
    try:
        run = Path(run)
        current, messages = _packet_sources(run, packet)
        path = run / AUTHORIZATION_FILE
        if not path.exists():
            raise AgentError('尚未记录明确生图授权；不能默认获得两张额度')
        record = read_json(path)
        if isinstance(record, dict) and record.get('format') == LEGACY_FORMAT:
            return _validate_v1(record, current, messages)
        record = _validate_v2(record)
        _check_current(record, current)
        return record
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AgentError('生图授权或消息来源损坏；不能推断生成权限') from exc


def record_authorization(run: Path, source_id: str, sources_file: Path | None = None,
                         *, rebind: bool = False) -> dict:
    """Append a user grant; a repeated source is an entirely read-only replay."""
    try:
        run = Path(run)
        with dispatch_lock(run):
            packet, packet_messages = _packet_sources(run)
            path = run / AUTHORIZATION_FILE
            current = None
            if path.exists():
                saved = read_json(path)
                current = (_upgrade_v1(saved, packet) if isinstance(saved, dict)
                           and saved.get('format') == LEGACY_FORMAT else _validate_v2(saved))
            changed_packet = current is not None and current['packet_digest'] != packet['digest']
            if changed_packet:
                if not rebind:
                    _check_current(current, packet)
                _require_unused(run)
            elif current:
                _check_current(current, packet)
            sources = current['sources'] if current else _sources(packet_messages)
            old_count = len(verified_sources(sources)) if current else 0
            sources = _append_sources(sources, packet_messages)
            if sources_file is not None:
                incoming = InputSources.model_validate(read_json(Path(sources_file)))
                sources = _append_sources(sources, [message.model_dump() for message in incoming.messages])
            messages = verified_sources(sources)
            by_id = {message['id']: message for message in messages}
            if not isinstance(source_id, str) or source_id not in by_id:
                raise AgentError('生图授权必须引用当前任务内的用户消息ID')
            source = by_id[source_id]
            maximum = _grant(source)
            history, bindings = [], []
            indexes = {message['id']: index for index, message in enumerate(messages)}
            if current:
                if changed_packet and indexes[source_id] < old_count:
                    raise AgentError('重新绑定必须引用新增许可消息，不能重用旧grant')
                if any(row['source_id'] == source_id for row in current['history']):
                    if sources != current['sources']:
                        raise AgentError('新增来源必须选择对应的新许可消息，不能通过旧grant静默导入')
                    return saved
                if maximum < current['max_images'] or (not changed_packet and maximum == current['max_images']):
                    raise AgentError('扩额须引用新用户消息并提高累计上限，不能覆盖已有授权')
                history, bindings = current['history'], current['bindings']
                if indexes[source_id] <= indexes[history[-1]['source_id']]:
                    raise AgentError('扩额必须引用先前授权之后的新增用户消息')
            history = [*history, _entry(source, history[-1]['digest'] if history else '')]
            record = _sealed({'format': FORMAT, 'packet_digest': packet['digest'],
                              'input_sources_digest': packet['input_sources']['digest'],
                              'origin': ORIGIN, 'max_images': maximum,
                              'source_refs': [{'source_id': row['source_id'], 'quote': row['quote']}
                                              for row in history],
                              'history': history, 'sources': sources,
                              'bindings': [*bindings, _binding(packet, sources)]})
            _validate_v2(record)
            if len((json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8')) > 4 * 1024 * 1024:
                raise AgentError('授权历史超过4 MiB，未覆盖旧记录')
            write_json(path, record)
            return record
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AgentError('生图授权或消息来源无法保存；未推断生成权限') from exc


def read_stop(run: Path) -> dict | None:
    """A terminal hold for this task version; it never cancels a remote request."""
    path = Path(run) / STOP_FILE
    if not path.exists():
        return None
    try:
        value = read_json(path)
        _verify_seal(value, {'format', 'state', 'packet_digest', 'sources', 'source_id',
                             'source_digest', 'origin', 'digest'}, '停止生成记录')
        if (value['format'] != 'hex.generation-stop.v1' or value['state'] != 'stopped'
                or value['origin'] != ORIGIN):
            raise AgentError('停止生成记录格式无效')
        source = next(row for row in verified_sources(value['sources']) if row['id'] == value['source_id'])
        if (source['actor'] != 'user' or source['origin'] not in _HOST_ORIGINS
                or digest(source) != value['source_digest']):
            raise AgentError('停止生成须绑定当前真实用户消息')
        # Packet refresh cannot silently clear a terminal stop in the same run.
        return value
    except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
        raise AgentError('停止生成证据损坏；不能推断仍有执行权限') from exc


def stop_generation(run: Path, source_id: str, sources_file: Path | None = None) -> dict:
    """Record an explicit host interpretation of a user's stop, without spending.

    No prose classifier or stop-to-grant conversion. Reopening requires an
    explicitly authorized linked task version with its existing budget rules.
    """
    from .native_budget import dispatch_budget_lock
    run = Path(run).resolve()
    with dispatch_budget_lock(run):
        packet, messages = _packet_sources(run)
        sources = _sources(messages)
        if (run / AUTHORIZATION_FILE).exists():
            grant = read_authorization(run, packet)
            sources = grant.get('sources', sources)
        if sources_file is not None:
            incoming = InputSources.model_validate(read_json(Path(sources_file)))
            sources = _append_sources(sources, [row.model_dump() for row in incoming.messages])
        rows = verified_sources(sources)
        source = next((row for row in rows if row['id'] == source_id), None)
        if (source is None or source['actor'] != 'user' or source['origin'] not in _HOST_ORIGINS
                or source.get('generation_grant') is not None):
            raise AgentError('停止生成必须引用独立的用户消息；不能使用提案、历史导入或生图grant')
        existing = read_stop(run)
        if existing:
            if existing['source_id'] == source_id and existing['source_digest'] != digest(source):
                raise AgentError('停止来源只允许保留，不能改写已有用户消息')
            return existing
        value = _sealed({'format': 'hex.generation-stop.v1', 'state': 'stopped',
                         'packet_digest': packet['digest'], 'sources': sources,
                         'source_id': source_id, 'source_digest': digest(source), 'origin': ORIGIN})
        write_json(run / STOP_FILE, value)
        return value


def require_not_stopped(run: Path, shared_budget: dict | None = None) -> None:
    if read_stop(run):
        raise AgentError('用户已停止本版本生成；保留预算和回执，只查询原任务或建立已授权的关联版本')
    if shared_budget and not shared_budget.get('explicit_revision_grant'):
        owner = Path(shared_budget['budget_source'])
        if owner.resolve() != Path(run).resolve() and read_stop(owner):
            raise AgentError('原预算任务已停止生成；不能通过子目录继续提交')


def require_submission_authorization(run: Path, attempt: dict, shared_budget=None) -> None:
    """Recheck a reserved slot; this is not a second reservation or a refund."""
    from .workflow import read_packet
    from .host import read_dispatches
    run = Path(run)
    require_not_stopped(run, shared_budget)
    packet = read_packet(run)
    attempts = read_dispatches(run)
    current = next((row for row in attempts if row['request']['id'] == attempt['request']['id']), None)
    if current != attempt or current['status'] != 'pending':
        raise AgentError('生成预留在准备期间发生变化；核对原回执，不能按旧状态提交')
    grant = read_authorization(run, packet)
    request = attempt['request']
    if request['packet_digest'] != packet['digest']:
        raise AgentError('待执行请求绑定旧packet；不能沿用旧授权提交')
    history_digests = {grant['digest']}
    if grant['format'] == FORMAT:
        for index, binding in enumerate(grant['bindings']):
            history = grant['history'][:index + 1]
            earlier = {**grant, 'packet_digest': binding['packet_digest'],
                       'input_sources_digest': binding['input_sources']['digest'],
                       'max_images': history[-1]['max_images'], 'history': history,
                       'source_refs': grant['source_refs'][:index + 1],
                       'sources': binding['sources'], 'bindings': grant['bindings'][:index + 1]}
            history_digests.add(digest({k: v for k, v in earlier.items() if k != 'digest'}))
    if request.get('authorization_digest') not in history_digests:
        raise AgentError('冻结请求的授权不在当前有效历史中；不能用替换grant继续提交')
    used = len(attempts)
    maximum = grant['max_images']
    if shared_budget:
        used = (shared_budget['grant_attempted_images'] if shared_budget.get('explicit_revision_grant')
                else shared_budget['attempted_images'])
        if not shared_budget.get('explicit_revision_grant'):
            owner = Path(shared_budget['budget_source'])
            maximum = min(maximum, read_authorization(owner, read_packet(owner))['max_images'])
    if used > maximum:
        raise AgentError('已预留次数超过当前授权；保留消耗，不能提交或清零')


@contextmanager
def submission_guard(run: Path, attempt: dict):
    """Serialize permission changes with the durable submission boundary."""
    from .native_budget import dispatch_budget_lock
    with dispatch_budget_lock(run) as shared:
        require_submission_authorization(run, attempt, shared)
        yield
