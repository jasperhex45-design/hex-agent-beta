"""Append-only local feedback evidence; message hashes are not platform authentication."""
from pathlib import Path

from .io import AgentError, digest, read_json
from .provenance import InputSources, verified_sources


FORMAT = 'liu.feedback-sources.v1'
FILE = 'feedback-sources.json'
EVIDENCE = 'host_recorded_not_platform_authenticated'
LEGACY_EVIDENCE = 'legacy_host_report_unverified'
SCOPE = 'selected_candidate'
_BINDING = ('candidate_sha256', 'contract_digest', 'packet_digest')
_DECISION_FIELDS = {'source_id', 'quote', 'decision', 'scope', *_BINDING}


def _seal(body: dict) -> dict:
    return {**body, 'digest': digest(body)}


def _messages(raw: dict, *, sealed: bool) -> list[dict]:
    try:
        return (verified_sources(raw) if sealed else
                [row.model_dump() for row in InputSources.model_validate(raw).messages])
    except (TypeError, ValueError) as exc:
        raise AgentError('反馈消息来源结构无效') from exc


def _validate_decisions(rows: list[dict], decisions: list[dict]) -> None:
    by_id = {row['id']: row for row in rows}
    if len(by_id) != len(rows):
        raise AgentError('反馈消息来源ID重复')
    seen = set()
    positions = {row['id']: index for index, row in enumerate(rows)}
    previous_position = -1
    if not isinstance(decisions, list) or len(decisions) > 100:
        raise AgentError('反馈决策声明结构无效')
    for declaration in decisions:
        if not isinstance(declaration, dict) or set(declaration) != _DECISION_FIELDS:
            raise AgentError('反馈决策声明字段无效')
        source_id = declaration['source_id']
        if not isinstance(source_id, str) or source_id not in by_id:
            raise AgentError('反馈引用的用户消息不存在')
        if source_id in seen:
            raise AgentError('同一反馈消息不能声明多次决策；更新请追加新消息')
        if positions[source_id] <= previous_position:
            raise AgentError('反馈决策必须依照消息顺序追加，不能用早期消息覆盖后续反馈')
        previous_position = positions[source_id]
        seen.add(source_id)
        message = by_id[source_id]
        if message['actor'] != 'user' or message['origin'] not in ('host_transcription', 'host_message_record'):
            raise AgentError('反馈必须引用宿主记录的用户消息；历史导入与Agent提案不能追认')
        quote = declaration['quote']
        if not isinstance(quote, str) or not quote.strip() or quote not in message['text']:
            raise AgentError('反馈引文不存在于对应用户消息')
        if (declaration['decision'] not in ('accepted_by_user', 'rejected_by_user')
                or declaration['scope'] != SCOPE):
            raise AgentError('反馈决策只支持当前候选的接受或否决，不表示全任务验收')
        if any(not isinstance(declaration[key], str) or len(declaration[key]) != 64
               or any(c not in '0123456789abcdef' for c in declaration[key]) for key in _BINDING):
            raise AgentError('反馈决策缺少有效的图像、标准或任务绑定')


def read_snapshot(run: Path) -> dict:
    snapshot = read_json(run / FILE)
    if (not isinstance(snapshot, dict) or set(snapshot) != {'format', 'sources', 'decisions', 'digest'}
            or snapshot['format'] != FORMAT
            or snapshot['digest'] != digest({k: v for k, v in snapshot.items() if k != 'digest'})):
        raise AgentError('反馈来源快照结构或摘要不一致')
    rows = _messages(snapshot['sources'], sealed=True)
    _validate_decisions(rows, snapshot['decisions'])
    return snapshot


def prepare_snapshot(run: Path, sources_file: Path | None, source_id: str,
                     decision: str, quote: str, binding: dict) -> tuple[dict, dict]:
    """Validate and append in memory; caller holds the run lock and persists once valid."""
    previous = read_snapshot(run) if (run / FILE).exists() else None
    rows = list(verified_sources(previous['sources'])) if previous else []
    declarations = list(previous['decisions']) if previous else []
    if sources_file is not None:
        incoming = read_json(sources_file)
        if (not isinstance(incoming, dict) or set(incoming) != {'format', 'sources', 'decisions'}
                or incoming['format'] != FORMAT):
            raise AgentError('反馈来源文件须使用liu.feedback-sources.v1并包含sources与decisions')
        added = _messages(incoming['sources'], sealed=False)
        # Validate against combined messages: callers may append a decision for an existing message.
        by_id = {row['id']: row for row in rows}
        incoming_ids = set()
        for message in added:
            sid = message['id']
            if sid in incoming_ids:
                raise AgentError('反馈消息来源ID重复')
            incoming_ids.add(sid)
            if sid in by_id:
                if message != by_id[sid]:
                    raise AgentError('反馈消息来源只允许追加，不能改写已有原文')
                continue
            if message['in_reply_to'] and message['in_reply_to'] not in by_id:
                raise AgentError('反馈消息引用的原消息不存在')
            rows.append(message)
            by_id[sid] = message
        _validate_decisions(rows, incoming['decisions'])
        by_source = {item['source_id']: item for item in declarations}
        for declaration in incoming['decisions']:
            sid = declaration['source_id']
            if sid in by_source:
                if declaration != by_source[sid]:
                    raise AgentError('反馈决策只允许追加，不能改写已有范围或决策')
                continue
            declarations.append(declaration)
            by_source[sid] = declaration
    source_snapshot = _seal({'format': 'liu.input-sources.v1', 'messages': rows})
    _messages(source_snapshot, sealed=True)
    _validate_decisions(rows, declarations)
    snapshot = _seal({'format': FORMAT, 'sources': source_snapshot, 'decisions': declarations})
    reference = validate_reference(snapshot, source_id, decision, quote, binding)
    # An earlier acceptance cannot be replayed after a later rejection for the same candidate.
    latest = next((item for item in reversed(declarations)
                   if all(item[key] == binding[key] for key in _BINDING)), None)
    if latest is None or latest['source_id'] != source_id:
        raise AgentError('反馈引用不是本候选最新的决策；不能用早期接受覆盖后续否决')
    return snapshot, reference


def validate_reference(snapshot: dict, source_id: str, decision: str, quote: str, binding: dict) -> dict:
    declaration = next((item for item in snapshot['decisions'] if item['source_id'] == source_id), None)
    if declaration is None:
        raise AgentError('反馈引用的用户消息或决策声明不存在')
    expected = {'source_id': source_id, 'quote': quote, 'decision': decision, 'scope': SCOPE, **binding}
    if declaration != expected:
        raise AgentError('反馈原话、决策或图像/标准绑定与消息声明不一致')
    message = next(row for row in verified_sources(snapshot['sources']) if row['id'] == source_id)
    return {'source_id': source_id, 'source_digest': digest(message),
            'decision_digest': digest(declaration), 'source_locator': message['locator'],
            'source_evidence': EVIDENCE, 'acceptance_scope': SCOPE}


def verify_reference(run: Path, payload: dict, binding: dict) -> dict:
    snapshot = read_snapshot(run)
    expected = validate_reference(snapshot, payload.get('source_id'), payload['decision'],
                                  payload['user_quote'], binding)
    if any(payload.get(key) != value for key, value in expected.items()):
        raise AgentError('反馈记录与来源快照不一致')
    latest = next((item for item in reversed(snapshot['decisions'])
                   if all(item[key] == binding[key] for key in _BINDING)), None)
    if not latest or latest['source_id'] != payload['source_id']:
        raise AgentError('反馈记录落后于本候选的最新来源决策')
    return expected


RELATIVE_FORMAT = 'liu.relative-preference-sources.v1'
RELATIVE_FIELDS = {'source_id', 'quote', 'decision', 'scope',
                   'preferred_request_id', 'preferred_sha256',
                   'compared_request_id', 'compared_sha256',
                   'contract_digest', 'packet_digest'}
RELATIVE_BINDING = RELATIVE_FIELDS - {'source_id', 'quote', 'decision', 'scope'}


def verify_relative_snapshot(receipt: dict) -> None:
    sources = receipt.get('sources')
    rows = _messages(sources, sealed=True)
    declarations = receipt.get('decisions')
    messages = receipt.get('messages')
    if (not isinstance(declarations, list) or not isinstance(messages, list)
            or len(declarations) != len(messages) or not 1 <= len(messages) <= 100):
        raise AgentError('相对偏好消息与声明数量无效')
    by_id = {row['id']: row for row in rows}
    if len(by_id) != len(rows):
        raise AgentError('相对偏好消息来源ID重复')
    positions = {row['id']: index for index, row in enumerate(rows)}
    previous_position = -1
    seen = set()
    for declaration, event in zip(declarations, messages):
        if not isinstance(declaration, dict) or set(declaration) != RELATIVE_FIELDS:
            raise AgentError('相对偏好声明字段无效')
        sid = declaration['source_id']
        row = by_id.get(sid)
        if (row is None or sid in seen or positions[sid] <= previous_position
                or row['actor'] != 'user'
                or row['origin'] not in ('host_transcription', 'host_message_record')):
            raise AgentError('相对偏好须按顺序引用独立用户消息')
        seen.add(sid)
        previous_position = positions[sid]
        if (declaration['decision'] != 'relative_preference'
                or declaration['scope'] != 'comparison_pair'
                or not isinstance(declaration['quote'], str)
                or not declaration['quote'].strip()
                or declaration['quote'] not in row['text']):
            raise AgentError('相对偏好声明与用户原话不一致')
        current = receipt['binding']
        current_pair = {current['preferred_request_id']: current['preferred_sha256'],
                        current['compared_request_id']: current['compared_sha256']}
        declared_pair = {declaration['preferred_request_id']: declaration['preferred_sha256'],
                         declaration['compared_request_id']: declaration['compared_sha256']}
        if (declared_pair != current_pair
                or declaration['contract_digest'] != current['contract_digest']
                or declaration['packet_digest'] != current['packet_digest']):
            raise AgentError('相对偏好声明与本轮双候选、合同或任务绑定不一致')
        expected = {'source_id': sid, 'quote': declaration['quote'],
                    'locator': row['locator'], 'source_digest': digest(row),
                    'decision_digest': digest(declaration),
                    'preferred_request_id': declaration['preferred_request_id'],
                    'compared_request_id': declaration['compared_request_id']}
        if (not isinstance(event, dict) or any(event.get(key) != value for key, value in expected.items())
                or not isinstance(event.get('source'), str) or not event['source'].strip()
                or not isinstance(event.get('recorded_at'), str) or not event['recorded_at']):
            raise AgentError('相对偏好事件与消息快照不一致')
    if {key: declarations[-1][key] for key in RELATIVE_BINDING} != receipt['binding']:
        raise AgentError('当前相对偏好必须来自最新用户消息')


def prepare_relative_snapshot(previous: dict | None, sources_file: Path,
                              source_id: str, quote: str, binding: dict) -> tuple[dict, dict]:
    rows = list(verified_sources(previous['sources'])) if previous else []
    declarations = list(previous['decisions']) if previous else []
    incoming = read_json(sources_file)
    if (not isinstance(incoming, dict) or set(incoming) != {'format', 'sources', 'decisions'}
            or incoming.get('format') != RELATIVE_FORMAT):
        raise AgentError('相对偏好来源文件格式无效')
    added = _messages(incoming['sources'], sealed=False)
    by_id = {row['id']: row for row in rows}
    for row in added:
        if row['id'] in by_id:
            if row != by_id[row['id']]:
                raise AgentError('相对偏好用户消息不能改写')
            continue
        if row['in_reply_to'] and row['in_reply_to'] not in by_id:
            raise AgentError('相对偏好消息引用不存在')
        rows.append(row)
        by_id[row['id']] = row
    if not isinstance(incoming['decisions'], list):
        raise AgentError('相对偏好声明结构无效')
    for item in incoming['decisions']:
        if not isinstance(item, dict) or set(item) != RELATIVE_FIELDS:
            raise AgentError('相对偏好声明字段无效')
        old = next((d for d in declarations if d['source_id'] == item['source_id']), None)
        if old:
            if old != item:
                raise AgentError('相对偏好来源声明不能改写')
            continue
        declarations.append(item)
    declaration = next((d for d in declarations if d['source_id'] == source_id), None)
    if declaration is None:
        raise AgentError('相对偏好来源声明不存在')
    expected = {'source_id': source_id, 'quote': quote, 'decision': 'relative_preference',
                'scope': 'comparison_pair', **binding}
    if declaration != expected:
        raise AgentError('相对偏好原话或候选绑定与声明不一致')
    if declarations[-1]['source_id'] != source_id or any(
            d['source_id'] == source_id for d in (previous['decisions'] if previous else [])):
        raise AgentError('相对偏好必须追加新消息；不能用早期来源覆写或重复登记')
    row = by_id.get(source_id)
    if row is None:
        raise AgentError('相对偏好用户消息不存在')
    sources = _seal({'format': 'liu.input-sources.v1', 'messages': rows})
    snapshot = {'sources': sources, 'decisions': declarations}
    event = {'source_id': source_id, 'quote': quote, 'locator': row['locator'],
             'source_digest': digest(row), 'decision_digest': digest(declaration),
             'preferred_request_id': binding['preferred_request_id'],
             'compared_request_id': binding['compared_request_id']}
    probe = {'binding': binding, 'sources': sources, 'decisions': declarations,
             'messages': [*(previous['messages'] if previous else []),
                          {**event, 'source': 'validated', 'recorded_at': 'validated'}]}
    verify_relative_snapshot(probe)
    return snapshot, event
