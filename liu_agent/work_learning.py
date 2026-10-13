"""Private, evidence-bound WORK memory; TEST has its own store.

The host interprets pictures/feedback. This module never calls a model, grants
permissions, rewrites a Skill, or turns a comparison into lasting taste.
Local owner binding is NOT authentication against another OS user/process.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

FORMAT = 'hex.work-learning.v1'
MODES = {'work', 'test'}
KINDS = {'preference', 'method', 'model'}
SCOPES = {'create', 'edit', 'color_only', 'relight', 'scene'}
DOMAINS = {'person', 'vehicle', 'hybrid', 'illustration', 'poster', 'character', 'prop', 'storyboard'}
LIMIT = 3


class LearningError(ValueError):
    pass


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _seal(value):
    return {**value, 'digest': digest(value)}


def _text(value, name, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise LearningError(f'{name}: require non-empty text, at most {limit} characters')
    return value.strip()


def _read(path):
    path = Path(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise LearningError('Learning input exceeds 8 MiB')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise LearningError('Expected a JSON object')
    return value


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def _lock(path):
    """One writer. Never remove somebody else's lock or retry a stale lock."""
    lock = Path(str(path) + '.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise LearningError('Profile busy; inspect the existing lock before retrying') from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _verify_seal(value):
    if value.get('digest') != digest({k: v for k, v in value.items() if k != 'digest'}):
        raise LearningError('Learning state digest mismatch; do not reset it')


def _load(file, owner, mode):
    value = _read(file)
    _verify_seal(value)
    if mode not in MODES or value.get('format') != FORMAT or (value.get('owner'), value.get('mode')) != (owner, mode):
        raise LearningError('Owner/mode mismatch; WORK and TEST cannot share a profile file')
    return value


def _save(file, state):
    state = {k: v for k, v in state.items() if k != 'digest'}
    state['revision'] += 1
    _atomic(file, _seal(state))


def _message(path, message_id, quote):
    raw = _read(path)
    envelope = raw.get('sources', raw)
    if not isinstance(envelope, dict) or not isinstance(envelope.get('messages'), list):
        raise LearningError('Expected a real messages source envelope')
    messages = envelope['messages']
    found = [m for m in messages if isinstance(m, dict) and m.get('id') == message_id]
    if len(found) != 1 or found[0].get('actor') != 'user' or quote not in found[0].get('text', ''):
        raise LearningError('Missing exact user message/quote; model text is not user feedback')
    # Full bytes still bind validity. The separate logical key ignores schema
    # defaults inserted by InputSources and copying to another source file.
    key = digest({k: found[0].get(k, '') for k in ('id', 'actor', 'text', 'origin', 'locator')})
    return {'kind': 'message', 'file': str(Path(path).resolve()),
            'message_digest': digest(found[0]), 'message_key': key,
            'message_id': message_id, 'quote': quote}


def create(file, owner, mode, source_file, message_id, quote, *, allow_inferred=False):
    """Consent is host-interpreted, exact-source-bound, not platform-authenticated."""
    _text(owner, 'owner', 100)
    if mode not in MODES or type(allow_inferred) is not bool:
        raise LearningError('Invalid mode/inference policy')
    proof = _message(source_file, message_id, _text(quote, 'consent quote', 1000))
    file = Path(file).expanduser().resolve()
    with _lock(file):
        if file.exists():
            existing = _load(file, owner, mode)
            if existing['allow_inferred'] != allow_inferred:
                raise LearningError('Existing consent policy differs; do not silently overwrite it')
            return {'status': 'already_initialized', 'revision': existing['revision']}
        value = {'format': FORMAT, 'owner': owner, 'mode': mode, 'consent': proof,
                 'allow_inferred': allow_inferred, 'revision': 0,
                 'events': {}, 'rules': {}, 'operations': {}}
        _atomic(file, _seal(value))
    return {'status': 'initialized', 'file': str(file), 'owner': owner, 'mode': mode}


def binding_file(kb):
    return Path(kb).parent / '.runtime' / 'work-learning.json'


def bind(kb, file, owner, mode, *, project='', style='', model='', target='', exclude=()):
    """Select a local profile for FUTURE tasks, not frozen tasks already in flight."""
    _load(file, owner, mode)
    if any(not isinstance(v, str) or len(v) > 100 for v in (project, style, model, target)):
        raise LearningError('Invalid project/style/model selector')
    if bool(model) != bool(target):
        raise LearningError('A model selector needs its actual HEX target alias')
    if len(exclude) > 16 or any(not isinstance(v, str) or len(v) > 80 for v in exclude):
        raise LearningError('Invalid excluded attributes')
    value = {'file': str(Path(file).expanduser().resolve()), 'owner': owner, 'mode': mode,
             'project': project, 'style': style, 'model': model, 'target': target, 'exclude': list(exclude)}
    _atomic(binding_file(kb), _seal(value))
    return {'status': 'bound_for_new_tasks', **value}


def unbind(kb):
    binding_file(kb).unlink(missing_ok=True)
    return {'status': 'unbound_for_new_tasks', 'private_data_deleted': False}


def initialize(packet, kb, *, enabled=None):
    if 'work_learning' in packet:
        return True
    file = binding_file(kb)
    if not file.exists():
        return False
    binding = _read(file)
    _verify_seal(binding)
    if enabled is not False:
        _load(binding['file'], binding['owner'], binding['mode'])
    packet['work_learning'] = {'version': 1, 'binding': binding, 'enabled': enabled is not False,
                               'workspace_binding': str(file.resolve())}
    return True


def _current_binding(learning):
    active = _read(learning['workspace_binding'])
    _verify_seal(active)
    if any(active.get(k) != learning['binding'].get(k) for k in ('file', 'owner', 'mode')):
        raise LearningError('Active workspace profile changed; do not use another owner/mode in this task')


def _run_source(run, row, record_digest):
    """Canonical identity AFTER native verification; only receipt time is volatile.

    The evidence label alone is insufficient. Match the verified record to its
    actual user-message snapshot, including when supplied by register_run.
    """
    from . import feedback_sources as fs
    from .provenance import verified_sources
    from .user_feedback_context import _verify_images

    if row.get('source_evidence') != fs.EVIDENCE:
        raise LearningError('Private learning requires verified user-message evidence, not a legacy host report')
    kind = row.get('feedback_kind')
    if kind == 'single_image':
        receipt = _read(Path(run) / 'user-feedback.json')
        evidence = receipt['feedback']
        if receipt.get('digest') != digest(evidence):
            raise LearningError('Feedback receipt digest mismatch')
        snapshot_path = Path(run) / fs.FILE
        snapshot = fs.read_snapshot(Path(run))
        fs.verify_reference(Path(run), evidence, {k: evidence[k] for k in
                            ('candidate_sha256', 'contract_digest', 'packet_digest')})
        message_id = evidence['source_id']
        stable = {k: v for k, v in evidence.items() if k != 'recorded_at'}
    elif kind in ('positive_feedback', 'relative_preference'):
        snapshot_path = Path(run) / ('candidate-feedback.json' if kind == 'positive_feedback'
                                     else 'relative-preference.json')
        snapshot = _read(snapshot_path)
        _verify_seal(snapshot)
        message_id = row['source_id']
        events = [e for e in snapshot['messages'] if e['source_id'] == message_id]
        if len(events) != 1:
            raise LearningError('Feedback must reference one real user message')
        message = next((m for m in verified_sources(snapshot['sources']) if m['id'] == message_id), None)
        evidence = {'binding': snapshot['binding'], 'event': events[0], 'message': message}
        stable = {**evidence, 'event': {k: v for k, v in events[0].items() if k != 'recorded_at'}}
    else:
        raise LearningError('Unknown run feedback kind')
    if digest(evidence) != record_digest or row['id'] != kind + '.' + record_digest[:20]:
        raise LearningError('Verified run row no longer matches the original receipt')
    messages = verified_sources(snapshot['sources'])
    message = next((m for m in messages if m['id'] == message_id), None)
    if (message is None or message['actor'] != 'user'
            or message['origin'] not in ('host_transcription', 'host_message_record')):
        raise LearningError('Feedback requires a current host-recorded user message')
    proof = _message(snapshot_path, message_id, _text(row['user_quote'], 'user quote', 2000))
    _verify_images(row)
    # Keep source label, quote, declarations, message, image path/hash and task
    # binding. Do not strip timestamps recursively from arbitrary user content.
    summary = {k: v for k, v in row.items() if k not in ('id', 'recorded_at', 'task_id')}
    stable_digest = digest({'evidence': stable, 'message': message, 'record': summary})
    return {'kind': 'run', 'identity_version': 2, 'run': str(Path(run).resolve()),
            'record_id': kind + '.' + stable_digest[:20], 'record_digest': stable_digest,
            'message': proof}


def _details(event):
    source = event['source']
    if source['kind'] == 'message':
        proof = _message(source['file'], source['message_id'], source['quote'])
        if {k: proof.get(k) for k in source} != source:
            raise LearningError('Source message changed; refresh evidence rather than reusing it')
        return {'user_quote': source['quote'], 'source_message': proof}
    if source['kind'] == 'run':
        from .user_feedback_context import _run_records
        from .io import AgentError
        try:
            for row, summary, _ in _run_records(Path(source['run'])):
                if source.get('identity_version') == 2:
                    current = _run_source(source['run'], row, summary)
                    matches = current == source
                else:
                    # Old candidate files remain readable, but cannot skip the
                    # newly required real-message check or silently rebind data.
                    matches = row['id'] == source['record_id'] and summary == source['record_digest']
                    current = _run_source(source['run'], row, summary) if matches else None
                if matches:
                    return {**row, 'source_message': current['message']}
        except (AgentError, KeyError, TypeError) as exc:
            raise LearningError(str(exc)) from exc
        raise LearningError('Original feedback was withdrawn or changed')
    raise LearningError('Unknown learning evidence kind')


def _source_key(event, detail=None):
    """One original message, regardless of task labels, copies or quote slices."""
    source = event['source']
    proof = source if source['kind'] == 'message' else source.get('message')
    if proof is None or 'message_key' not in proof:
        proof = (detail or _details(event))['source_message']
    return proof['message_key']


def _stored_source_key(event):
    try:
        return _source_key(event)
    except (OSError, ValueError):
        # Pre-safety run events may lack a recoverable message. Never invent it.
        return digest(event['source'])


def _event_key(event):
    return digest(event['source'])


def record_message(file, owner, mode, source_file, message_id, quote, *, task_id, domain, project='', style='', task_scope='create'):
    if domain not in DOMAINS or task_scope not in SCOPES:
        raise LearningError('Unknown domain')
    event = {'source': _message(source_file, message_id, _text(quote, 'quote', 2000)),
             'task_id': _text(task_id, 'task_id', 200), 'domain': domain, 'project': project, 'style': style, 'task_scope': task_scope}
    return _record(file, owner, mode, [event])


def _record(file, owner, mode, events):
    with _lock(file):
        state = _load(file, owner, mode)
        known = {}
        for ident, row in state['events'].items():
            source = _stored_source_key(row['event'])
            if source not in known or row['state'] != 'pending':
                known[source] = ident
        count, ids = 0, []
        for event in events:
            source = _stored_source_key(event)
            key = known.get(source, _event_key(event))
            if key not in state['events']:
                if len(state['events']) >= 500:
                    raise LearningError('500-event candidate limit reached; archive deliberately, do not drop evidence')
                state['events'][key] = {'event': event, 'state': 'pending'}
                known[source] = key
                count += 1
            # Keep the first source/case binding. A copy cannot refresh evidence
            # or turn a processed message back into a pending event.
            if key not in ids:
                ids.append(key)
        if count:
            _save(file, state)
    return {'status': 'queued' if count else 'unchanged', 'new_events': count,
            'event_ids': ids, 'mode': mode}


def _case_family(packet, run):
    current, seen = packet, {str(Path(run).resolve())}
    group = str(Path(run).resolve())
    while current.get('requirement_parent', {}).get('run'):
        group = str(Path(current['requirement_parent']['run']).resolve())
        if group in seen or len(seen) >= 32:
            raise LearningError('Ambiguous or cyclic task lineage; do not count independent cases')
        seen.add(group)
        current = _read(Path(group) / 'packet.json')
    return group


def capture_run(run, *, verified_records=None):
    """Called after existing feedback verification. No guessing from a saved image."""
    packet = _read(Path(run) / 'packet.json')
    learning = packet.get('work_learning')
    if not learning:
        return None
    if not learning.get('enabled'):
        return {'status': 'learning_disabled', 'legacy_registration_skipped': True}
    _current_binding(learning)
    binding = learning['binding']
    _verify_seal(binding)
    from .user_feedback_context import _run_records
    from .io import AgentError
    events = []
    try:
        rows = list(_run_records(Path(run))) if verified_records is None else verified_records
        # Linked revisions remain one case family, not independent votes.
        group = _case_family(packet, run)
        for row, record_digest, _ in rows:
            events.append({'source': _run_source(run, row, record_digest),
                           'task_id': group, 'domain': row['domain'], 'task_scope': row['scope'],
                           'project': binding['project'], 'style': binding['style']})
    except (AgentError, KeyError, TypeError) as exc:
        raise LearningError(str(exc)) from exc
    return _record(binding['file'], binding['owner'], binding['mode'], events)


def prepare(file, owner, mode, limit=6):
    state = _load(file, owner, mode)
    result, invalid = [], []
    for ident, row in state['events'].items():
        if row['state'] != 'pending':
            continue
        try:
            detail = _details(row['event'])
        except (OSError, ValueError) as exc:
            invalid.append({'event_id': ident, 'reason': str(exc)})
            continue
        result.append({'event_id': ident, **deepcopy(row['event']), 'feedback': detail})
        if len(result) >= max(1, min(int(limit), 6)):
            break
    domains = {e['domain'] for e in result}
    related = [deepcopy(r) for r in state['rules'].values() if r['rule']['domain'] in domains]
    return {'revision': state['revision'], 'events': result, 'invalid': invalid,
            'existing_rules': related[:8], 'additional_rule_count': max(0, len(related) - 8),
            'instruction': 'Inspect real evidence; distinguish user facts from hypotheses. Return a bounded delta or no_change.'}


def _rule(value, events, quotes):
    rule = deepcopy(value)
    fields = {'kind', 'domain', 'project', 'style', 'model', 'attribute', 'guidance', 'task_scope',
              'when', 'not_for', 'basis', 'scope_quote'}
    if not isinstance(rule, dict) or set(rule) != fields:
        raise LearningError('Rule must use the exact bounded schema')
    if any(not isinstance(rule[k], str) for k in fields - {'when'}):
        raise LearningError('Rule fields must be bounded text')
    if rule['kind'] not in KINDS or rule['domain'] not in DOMAINS or rule['basis'] not in {'explicit', 'inferred'} or rule['task_scope'] not in SCOPES:
        raise LearningError('Invalid kind/domain/basis')
    if any(len(rule[k]) > 500 for k in ('project', 'style', 'model', 'scope_quote', 'not_for')):
        raise LearningError('Invalid scope field')
    rule['attribute'] = _text(rule['attribute'], 'attribute', 80)
    rule['guidance'] = _text(rule['guidance'], 'guidance', 500)
    if not isinstance(rule['when'], list) or len(rule['when']) > 3 or any(not isinstance(v, str) or not v.strip() or len(v) > 80 for v in rule['when']):
        raise LearningError('when must be at most three concrete terms')
    if (rule['kind'] == 'model') != bool(rule['model']):
        raise LearningError('Model experience requires an exact model; taste must not bind a model')
    if rule['basis'] == 'explicit':
        if not rule['scope_quote'].strip() or not any(rule['scope_quote'] in q for q in quotes):
            raise LearningError('Explicit scope needs the actual user quote')
    else:
        if rule['scope_quote']:
            raise LearningError('An inference cannot pretend to be an explicit quote')
        if any(any(e[k] != rule[k] for k in ('domain', 'project', 'style', 'task_scope')) for e in events):
            raise LearningError('Inferences cannot expand across domains, projects or styles')
    return rule


def _independent_support(state, ids, details):
    """Find three supports sharing neither a case family nor a source message.

    Matching (rather than min(unique cases, unique sources)) also handles old
    profiles where copied sources occur under several different case labels.
    """
    cases = {}
    for ident in ids:
        event = state['events'][ident]['event']
        cases.setdefault(event['task_id'], set()).add(_source_key(event, details[ident]))
    matched = {}

    def assign(case, seen):
        for source in sorted(cases[case]):
            if source in seen:
                continue
            seen.add(source)
            if source not in matched or assign(matched[source], seen):
                matched[source] = case
                return True
        return False

    for case in cases:
        assign(case, set())
        if len(matched) >= 3:
            return True
    return False


def _rule_evidence(state, row):
    details = {i: _details(state['events'][i]['event']) for i in row['evidence_ids']}
    if row['rule']['basis'] == 'inferred' and (not state['allow_inferred']
            or not _independent_support(state, row['evidence_ids'], details)):
        raise LearningError('Inference lacks three independent cases and source messages')


def _mutations(change):
    common = {'operation_id', 'expected_revision', 'event_ids', 'action'}
    if not isinstance(change, dict):
        raise LearningError('Delta must be a JSON object')
    if change.get('action') == 'batch':
        if set(change) != common | {'changes'} or not isinstance(change['changes'], list) or not 1 <= len(change['changes']) <= 16:
            raise LearningError('A batch needs 1..16 bounded changes and one shared set of source events')
        mutations = change['changes']
    else:
        if set(change) != common | {'rule'} | ({'replaces'} if change.get('action') == 'replace' else set()):
            raise LearningError('Unknown delta fields; code, permissions and budgets are not learning data')
        mutations = [{k: v for k, v in change.items() if k not in common - {'action'}}]
    for item in mutations:
        if not isinstance(item, dict) or item.get('action') not in ('upsert', 'replace', 'retire', 'no_change'):
            raise LearningError('Only upsert, replace, retire and no_change mutations are supported')
        if set(item) != {'action', 'rule'} | ({'replaces'} if item['action'] == 'replace' else set()):
            raise LearningError('Mutation must use the exact bounded schema')
        if item['action'] == 'no_change' and (item['rule'] is not None or len(mutations) != 1):
            raise LearningError('no_change requires rule=null and cannot mix with changes')
    return mutations


def apply(file, owner, mode, change):
    """One atomic commit consumes pending feedback once, across all mutations.

    Retried operation IDs return their original receipt, never replay a write.
    replace explicitly names old slots, so scope changes cannot leave a global
    predecessor live. Separate upserts may still express independent styles.
    """
    mutations = _mutations(change)
    op = _text(change.get('operation_id'), 'operation_id', 100)
    signature = digest(change)
    with _lock(file):
        state = _load(file, owner, mode)
        if op in state['operations']:
            if state['operations'][op]['digest'] != signature:
                raise LearningError('Operation ID reused for a different update')
            return state['operations'][op]['receipt']
        if type(change.get('expected_revision')) is not int or change['expected_revision'] != state['revision']:
            raise LearningError('Profile changed; re-read and reconcile before updating')
        ids = change.get('event_ids')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 6
                or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
            raise LearningError('An update needs 1..6 distinct source events')
        if any(i not in state['events'] or state['events'][i]['state'] != 'pending' for i in ids):
            raise LearningError('Every supplied event must be pending; use one batch for multiple attributes')
        events = [state['events'][i]['event'] for i in ids]
        details = {i: _details(state['events'][i]['event']) for i in ids}
        sources = {_source_key(state['events'][i]['event'], details[i]) for i in ids}
        if len(sources) != len(ids) or any(_stored_source_key(row['event']) in sources
                for row in state['events'].values() if row['state'] != 'pending'):
            raise LearningError('Feedback source already consumed or duplicated; a new task label is not new feedback')
        quotes = [details[i]['user_quote'] for i in ids]
        # Dict insertion order is durable and append-only, including v1 profiles.
        order = {i: n for n, i in enumerate(state['events'], 1)}
        newest, oldest = max(order[i] for i in ids), min(order[i] for i in ids)
        touched, results = set(), []
        for mutation in mutations:
            action, rule_id, replaced = mutation['action'], None, []
            if action in ('upsert', 'replace'):
                rule = _rule(mutation['rule'], events, quotes)
                slot = {k: rule[k] for k in ('kind', 'domain', 'project', 'style', 'model', 'attribute', 'task_scope')}
                rule_id = digest(slot)[:24]
                if action == 'replace':
                    replaced = mutation['replaces']
                    if (not isinstance(replaced, list) or not 1 <= len(replaced) <= 16
                            or any(not isinstance(i, str) or i not in state['rules'] for i in replaced)
                            or len(set(replaced)) != len(replaced)):
                        raise LearningError('replace needs 1..16 distinct existing rule IDs')
            elif action == 'retire':
                rule_id = mutation['rule']
                if not isinstance(rule_id, str) or rule_id not in state['rules']:
                    raise LearningError('Unknown rule to retire')
            affected = ({rule_id} if rule_id else set()) | set(replaced)
            if affected & touched:
                raise LearningError('Each rule may be mutated only once per batch; use replace for a merge')
            touched.update(affected)
            for ident in affected:
                prior = state['rules'].get(ident)
                if prior is None:
                    continue
                watermark = prior.get('evidence_order', max(order[i] for i in prior['evidence_ids']))
                if prior['status'] == 'retired' and 'evidence_order' not in prior:
                    # Old retirements did not save their source. Fail closed on
                    # feedback queued before any prior processed event.
                    watermark = max(watermark, max((order[i] for i, r in state['events'].items()
                                                    if r['state'] != 'pending'), default=0))
                if oldest <= watermark:
                    raise LearningError('Older feedback cannot overwrite a newer rule or its retirement')
                if action in ('upsert', 'replace') and rule['basis'] != 'explicit':
                    if prior['rule']['basis'] == 'explicit':
                        raise LearningError('An inference cannot overwrite or replace an explicit user preference')
                    if prior['status'] == 'retired':
                        raise LearningError('Restoring a retired rule requires new explicit user feedback')
            for ident in replaced + ([rule_id] if action == 'retire' else []):
                state['rules'][ident].update(active=False, status='retired', evidence_order=newest,
                                             updated_revision=state['revision'] + 1, retirement_evidence_ids=list(ids))
            if action in ('upsert', 'replace'):
                prior = state['rules'].get(rule_id)
                # Only unchanged, non-retired upserts accumulate prior support.
                support = sorted(set(ids + (prior['evidence_ids'] if action == 'upsert' and prior
                    and prior['rule'] == rule and prior['status'] != 'retired' else [])))
                for ident in support:
                    if ident not in details:
                        details[ident] = _details(state['events'][ident]['event'])
                active = rule['basis'] == 'explicit' or (state['allow_inferred'] and _independent_support(state, support, details))
                if rule_id not in state['rules'] and len(state['rules']) >= 128:
                    raise LearningError('128-rule candidate limit reached; archive deliberately, do not drop tombstones')
                state['rules'][rule_id] = {'id': rule_id, 'rule': rule, 'evidence_ids': support,
                    'evidence_order': newest, 'updated_revision': state['revision'] + 1,
                    'active': active, 'status': 'explicit' if rule['basis'] == 'explicit' else 'tentative',
                    'authority': 'user_preference_advisory' if rule['basis'] == 'explicit' else 'host_inference_advisory'}
            results.append({'action': action, 'rule_id': rule_id, 'replaced_rule_ids': replaced})
        for ident in ids:
            state['events'][ident]['state'] = 'processed'
        receipt = {'status': 'applied', 'revision': state['revision'] + 1, 'action': change['action'],
                   'rule_id': results[0]['rule_id'] if change['action'] != 'batch' else None,
                   'changes': results, 'mode': mode, 'common_skill_changed': False}
        state['operations'][op] = {'digest': signature, 'receipt': receipt}
        _save(file, state)
    return receipt


def context(file, owner, mode, *, domain, request='', project='', style='', model='', task_scope='create', exclude=()):
    state = _load(file, owner, mode)
    items, skipped = [], []
    for row in state['rules'].values():
        rule = row['rule']
        if (not row['active'] or rule['domain'] != domain or rule['task_scope'] != task_scope or rule['attribute'] in exclude
                or (rule['project'] and rule['project'] != project)
                or (rule['style'] and rule['style'] != style)
                or (rule['model'] and rule['model'] != model)
                or not all(term.casefold() in request.casefold() for term in rule['when'])):
            continue
        try:
            _rule_evidence(state, row)
        except (OSError, ValueError) as exc:
            skipped.append({'rule_id': row['id'], 'reason': str(exc)})
            continue
        items.append(deepcopy(row))
    # A narrower inference must not outrank explicit user evidence. Within the
    # same authority prefer scope, then recent feedback instead of the oldest
    # three insertions forever. Pre-safety profiles retain their stable order.
    items.sort(key=lambda r: (r['rule']['basis'] == 'explicit', bool(r['rule']['project']),
                              bool(r['rule']['style']), r.get('updated_revision', 0)), reverse=True)
    selected, slots = [], set()
    for row in items:
        key = row['rule']['attribute']
        if key in slots:
            continue
        slots.add(key)
        selected.append(row)
        if len(selected) == LIMIT:
            break
    return _seal({'version': 1, 'owner': owner, 'mode': mode, 'revision': state['revision'],
                  'items': selected, 'skipped': skipped, 'authority': 'advisory_current_request_wins',
                  'images_submitted': False, 'generation_authorized': False})


def refresh(packet):
    learning = packet.get('work_learning')
    if not learning:
        return
    _current_binding(learning)
    binding = learning['binding']
    _verify_seal(binding)
    need = packet['need']
    targets = packet.get('brief', {}).get('targets', [])
    model = binding['model'] if targets == [binding['target']] else ''
    values = context(binding['file'], binding['owner'], binding['mode'], domain=need['domain'],
                     request=need['intent'] + ' ' + need.get('subject', ''),
                     project=binding['project'], style=binding['style'], model=model, task_scope=packet.get('brief', {}).get('scope', 'create'), exclude=binding['exclude']) if learning['enabled'] else {}
    learning['context'] = values
    learning['need_digest'] = digest(need)
    learning['snapshot_digest'] = digest({k: v for k, v in learning.items() if k != 'snapshot_digest'})


def frozen_context(packet):
    learning = packet.get('work_learning')
    if not learning:
        return {}
    if (type(learning.get('version')) is not int or learning.get('version') != 1 or learning.get('need_digest') != digest(packet['need'])
            or learning.get('snapshot_digest') != digest({k: v for k, v in learning.items() if k != 'snapshot_digest'})):
        raise LearningError('Learning snapshot does not match this task; refresh via normal HEX workflow')
    _verify_seal(learning['binding'])
    return deepcopy(learning['context'])


def require_current(packet):
    value = frozen_context(packet)
    if not value:
        return
    _current_binding(packet['work_learning'])
    binding = packet['work_learning']['binding']
    state = _load(binding['file'], binding['owner'], binding['mode'])
    for row in value['items']:
        if state['rules'].get(row['id']) != row:
            raise LearningError('Selected personal experience changed/was retired; refresh before NEW execution')
        _rule_evidence(state, row)
