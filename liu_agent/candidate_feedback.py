"""Positive image feedback without inferring a comparison, acceptance or permission."""
from pathlib import Path

from .io import AgentError, digest, now, read_json, write_json
from .provenance import InputSources, verified_sources
from .feedback_sources import EVIDENCE

FILE = 'candidate-feedback.json'
FORMAT = 'liu.candidate-feedback.v1'
SOURCE_FORMAT = 'liu.candidate-feedback-sources.v1'
SCOPE = 'current_candidate'


def _binding(run: Path, request_id: str | None = None, *, result: dict | None = None) -> tuple[dict, dict]:
    from .feedback import verified_candidate
    packet, result, trial = verified_candidate(run, _result=result)
    if result.get('selected_trial_round') != trial['round']:
        raise AgentError('正向反馈只绑定当前选中候选；双图比较继续使用相对偏好')
    current_id = (trial.get('request_id') if trial['origin'] == 'host_reported_not_api_verified'
                  else (trial.get('generation_evidence') or {}).get('request_id'))
    if not current_id or (request_id is not None and request_id != current_id):
        raise AgentError('正向反馈request_id不是当前选中候选')
    return result, {'request_id': current_id, 'candidate_sha256': trial['sha256'],
                    'packet_digest': packet['digest'],
                    'contract_digest': result['acceptance']['digest']}


def _validate(receipt: dict) -> None:
    rows = verified_sources(receipt['sources'])
    by_id = {row['id']: row for row in rows}
    positions = {row['id']: i for i, row in enumerate(rows)}
    events = receipt.get('messages')
    if len(by_id) != len(rows) or not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise AgentError('候选反馈来源或消息数量无效')
    position = -1
    for event in events:
        if not isinstance(event, dict):
            raise AgentError('候选反馈消息结构无效')
        sid = event.get('source_id')
        row = by_id.get(sid)
        if (row is None or positions[sid] <= position or row['actor'] != 'user'
                or row['origin'] not in ('host_transcription', 'host_message_record')):
            raise AgentError('候选反馈须按顺序引用独立用户消息')
        position = positions[sid]
        quote = event.get('quote')
        if not isinstance(quote, str) or not quote.strip() or quote not in row['text']:
            raise AgentError('候选反馈原话不存在于用户消息')
        expected = {'source_id': sid, 'quote': quote, 'decision': 'positive_feedback',
                    'scope': SCOPE, **receipt['binding']}
        if (event.get('declaration') != expected or event.get('source_digest') != digest(row)
                or event.get('locator') != row['locator']
                or any(not isinstance(event.get(k), str) or not event[k].strip()
                       for k in ('source', 'recorded_at'))):
            raise AgentError('候选反馈与图片绑定或来源声明不一致')


def verified_candidate_feedback(run: Path, *, _result: dict | None = None) -> dict | None:
    if not (run/FILE).exists():
        return None
    receipt = read_json(run/FILE)
    if (not isinstance(receipt, dict) or receipt.get('format') != FORMAT
            or receipt.get('digest') != digest({k: v for k, v in receipt.items() if k != 'digest'})
            or not isinstance(receipt.get('binding'), dict)):
        raise AgentError('候选反馈结构或摘要无效')
    _, binding = _binding(run, receipt['binding'].get('request_id'), result=_result)
    if receipt['binding'] != binding:
        raise AgentError('候选反馈不属于当前图片或合同')
    _validate(receipt)
    return receipt


def projection(receipt: dict) -> dict:
    return {'binding': receipt['binding'], 'messages': receipt['messages'],
            'source_evidence': EVIDENCE, 'scope': SCOPE}


def apply_candidate_feedback(run: Path, result: dict) -> None:
    # During finalize, the authoritative ledger can already contain a newly
    # registered review while result.json still contains the prior export.
    # Validate the pending projection against that ledger, never bypass it.
    receipt = verified_candidate_feedback(run, _result=result)
    if receipt:
        result['candidate_feedback'] = projection(receipt)
    else:
        result.pop('candidate_feedback', None)


def projection_pending(result: dict, expected: dict) -> bool:
    """Accept only a shorter, identical prefix after an interrupted export."""
    old, current = result.get('candidate_feedback'), expected.get('candidate_feedback')
    if old == current:
        return False
    if not current:
        raise AgentError('导出正向反馈与来源或图片证据不一致')
    if old is None:
        return True
    if not isinstance(old, dict) or not isinstance(old.get('messages'), list):
        raise AgentError('旧正向反馈投影结构无效')
    count = len(old['messages'])
    if not 0 < count < len(current['messages']) or old != {**current, 'messages': current['messages'][:count]}:
        raise AgentError('导出正向反馈不是当前消息记录的合法前缀')
    return True


def prior_export_digest_matches(result: dict, old_digest: str) -> bool:
    current = result.get('candidate_feedback')
    if not isinstance(current, dict) or not isinstance(current.get('messages'), list):
        return False
    for count in range(len(current['messages'])):
        old = dict(result)
        if count:
            old['candidate_feedback'] = {**current, 'messages': current['messages'][:count]}
        else:
            old.pop('candidate_feedback', None)
        if digest(old) == old_digest:
            return True
    return False


def record_positive_feedback(run: Path, quote: str, source: str, *,
                             sources_file: Path | None, source_id: str | None,
                             request_id: str | None = None) -> dict:
    if (not isinstance(quote, str) or not quote.strip() or not isinstance(source, str)
            or not source.strip() or sources_file is None or not source_id):
        raise AgentError('正向反馈需要原话、来源、sources文件及source_id')
    from .feedback import apply_feedback, apply_relative_preference
    from .host import dispatch_lock
    from .exporter import export_result
    with dispatch_lock(run):
        result, binding = _binding(run, request_id)
        previous = verified_candidate_feedback(run)
        incoming = read_json(sources_file)
        if (not isinstance(incoming, dict) or set(incoming) != {'format', 'sources', 'decisions'}
                or incoming['format'] != SOURCE_FORMAT):
            raise AgentError('正向反馈来源文件格式无效')
        declaration = {'source_id': source_id, 'quote': quote.strip(),
                       'decision': 'positive_feedback', 'scope': SCOPE, **binding}
        if incoming['decisions'] != [declaration]:
            raise AgentError('正向反馈原话、决策或图片绑定与来源声明不一致')
        try:
            added = [row.model_dump() for row in InputSources.model_validate(incoming['sources']).messages]
        except (TypeError, ValueError) as exc:
            raise AgentError('正向反馈来源结构无效') from exc
        rows = list(verified_sources(previous['sources'])) if previous else []
        events = list(previous['messages']) if previous else []
        if any(event['source_id'] == source_id for event in events):
            raise AgentError('正向反馈必须追加新消息，不能重复登记')
        by_id = {row['id']: row for row in rows}
        for row in added:
            if row['id'] in by_id:
                if row != by_id[row['id']]:
                    raise AgentError('正向反馈用户消息只允许追加，不能改写')
                continue
            if row['in_reply_to'] and row['in_reply_to'] not in by_id:
                raise AgentError('正向反馈引用的消息不存在')
            rows.append(row)
            by_id[row['id']] = row
        row = by_id.get(source_id)
        if row is None:
            raise AgentError('正向反馈用户消息不存在')
        sources = {'format': 'liu.input-sources.v1', 'messages': rows}
        sources['digest'] = digest(sources)
        events.append({'source_id': source_id, 'quote': quote.strip(), 'locator': row['locator'],
                       'source_digest': digest(row), 'declaration': declaration,
                       'source': source.strip(), 'recorded_at': now()})
        receipt = {'format': FORMAT, 'binding': binding, 'sources': sources, 'messages': events}
        _validate(receipt)
        receipt['digest'] = digest(receipt)
        write_json(run/FILE, receipt)
        apply_feedback(run, result)
        apply_relative_preference(run, result)
        export_result(result, run)
        write_json(run/'run-state.json', {'status': result['status'],
                   'review_authority': result['review_authority'],
                   'human_acceptance': result['human_acceptance']})
        return {'status': result['status'], 'human_acceptance': result['human_acceptance'],
                'task_acceptance': result['task_acceptance'],
                'candidate_feedback': result['candidate_feedback']}
