"""Local generation facts shared by inspection and export; never proof of receipt."""
from pathlib import Path

from .io import AgentError, read_json


def execution_facts(run: Path, *, attempts=None, api_state=None, trials=None) -> dict:
    run = Path(run)
    rows = []
    if api_state is None and (run/'api-state.json').exists():
        from .api_recovery import load_api_state
        api_state = load_api_state(run)
    if attempts is None:
        from .host import read_dispatches
        attempts = read_dispatches(run)
    if api_state is not None and attempts:
        raise AgentError('此目录混有API与宿主调用记录；不能合并推断执行事实')
    for attempt in attempts:
        rows.append({'id': attempt['request']['id'], 'route': 'host', 'phase': 'generate',
                     'status': attempt['status'], 'evidence_refs': ['host-dispatches.json']})
    if api_state is not None:
        rows += [{'id': r['id'], 'route': 'api', 'phase': r['phase'], 'status': r['state'],
                  'evidence_refs': ['api-state.json', r['snapshot']]}
                 for r in api_state['requests'] if r['kind'] == 'image']
    for row in rows:
        if row['status'] not in ('pending', 'outcome_unknown', 'completed', 'failed'):
            raise AgentError('未知生成执行状态')
        row['evidence_scope'] = ('local_reservation_only' if row['status'] == 'pending'
                                 else 'local_execution_record')
    legacy = api_state is None and any((run/name).exists() for name in ('calls.json', 'image-tests.json', 'api'))
    if legacy:
        # Old ledgers have neither durable reservations nor reliable terminal states.
        # Preserve the known lower bound, without inventing successful submissions.
        ledger = read_json(run/'calls.json') if (run/'calls.json').exists() else {}
        events = ledger.get('events', [])
        count = ledger.get('attempted_images', 0)
        if type(count) is not int or count < 0 or not isinstance(events, list):
            raise AgentError('API旧调用计数无效')
        image_events = [e for e in events if e.get('kind') == 'image']
        for index in range(max(count, len(image_events))):
            rows.append({'id': f'legacy-api-image-{index+1}', 'route': 'api', 'phase': 'generate',
                         'status': 'outcome_unknown', 'evidence_scope': 'legacy_local_records',
                         'evidence_refs': ['calls.json']})
    states = {r['status'] for r in rows}
    status = next((s for s in ('outcome_unknown', 'pending', 'completed', 'failed') if s in states),
                  'evidence_incomplete' if legacy or trials else 'not_attempted')
    scope = ('legacy_local_records' if legacy else 'local_reservation_only' if states == {'pending'}
             else 'local_execution_record' if rows else 'candidate_record_only' if trials else 'no_generation_record')
    return {'generation_status': status, 'generation_evidence_scope': scope,
            'known_generation_attempts': rows}


def execution_quality_status(status: str, facts: dict, has_trials: bool) -> str:
    """Keep visual quality separate; missing trials never erase execution facts."""
    if has_trials:
        return status
    return {'pending': 'generation_pending', 'outcome_unknown': 'generation_outcome_unknown',
            'failed': 'generation_failed', 'completed': 'generation_result_incomplete',
            'evidence_incomplete': 'generation_evidence_incomplete'}.get(facts['generation_status'], status)
