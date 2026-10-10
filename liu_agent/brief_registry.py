"""Local brief coverage registry. Planning and preparation never grant execution."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .contracts import Brief, Strict
from .io import AgentError, ROOT, digest, now, read_json, sha, write_json


class SourceAsset(Strict):
    path: str = Field(min_length=1)
    source_family: str = Field(min_length=1)
    sha256: str | None = None


class Revision(Strict):
    parent: SourceAsset
    hypothesis: str = Field(min_length=1, max_length=2000)
    visible_goal: str = Field(min_length=1, max_length=2000)


class Registration(Strict):
    case_id: str = Field(min_length=1, max_length=160)
    brief_id: str = Field(min_length=1, max_length=160)
    domain: Literal['automotive', 'portrait', 'graphic', 'storyboard']
    use: Literal['fixed_regression', 'exploration', 'heldout_spec']
    capabilities: list[str] = Field(min_length=1, max_length=30)
    input_type: str = Field(min_length=1, max_length=300)
    source_family: str = Field(min_length=1, max_length=160)
    split: Literal['dev', 'heldout']
    planned_images: int = Field(default=2, strict=True, ge=0, le=100)
    readiness: Literal['planned', 'ready'] = 'planned'
    brief_file: str | None = None
    sources_file: str | None = None
    need_file: str | None = None
    source_assets: list[SourceAsset] = Field(default_factory=list, max_length=30)
    route_tool: str | None = None
    route_evidence: str | None = None
    preparation_route: Literal['workflow', 'car_photography'] = 'workflow'
    revision: Revision | None = None


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    try:
        handle = lock.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise AgentError('Brief registry is being written; retry after the owner finishes') from exc
    try:
        with handle:
            handle.write(now())
        yield
    finally:
        lock.unlink()


def _save(path: Path, body: dict) -> dict:
    value = {**body, 'digest': digest(body)}
    write_json(path, value)
    return value


def _load(path: Path) -> dict:
    if not path.exists():
        return {'format': 'hex.brief-registry.v1', 'entries': []}
    value = read_json(path)
    body = {k: v for k, v in value.items() if k != 'digest'}
    if (value.get('format') != 'hex.brief-registry.v1' or value.get('digest') != digest(body)
            or not isinstance(value.get('entries'), list)):
        raise AgentError('Brief registry format or digest is invalid')
    if len({e['registration']['case_id'] for e in value['entries']}) != len(value['entries']):
        raise AgentError('Duplicate case ID in registry')
    return body


def _file(path: str | Path, origin: Path, role: str, family: str | None = None,
          expected: str | None = None) -> dict:
    source = (origin / path).resolve()
    if not source.is_file():
        raise AgentError('Missing source file: ' + str(source))
    actual = sha(source.read_bytes())
    if expected is not None and expected != actual:
        raise AgentError('Source SHA256 does not match: ' + str(source))
    return {'path': str(source), 'sha256': actual, 'role': role, 'source_family': family}


def _verified_files(entry: dict) -> None:
    for source in entry['materials']:
        if sha(Path(source['path']).read_bytes()) != source['sha256']:
            raise AgentError('Registered source changed: ' + source['path'])


def _route(entry: dict) -> dict:
    """Consume an explicit saved availability probe, never infer it from a catalog."""
    from .executors import catalog
    tool = entry['registration']['route_tool']
    if not any(r['implemented'] and tool in r['tools'] for r in catalog()):
        raise AgentError('Execution route is not integrated: ' + str(tool))
    from .generation_spec import validate_tool_target
    brief_source = next((s for s in entry['materials'] if s['role'] == 'brief'), None)
    targets = Brief.model_validate(read_json(Path(brief_source['path']))).targets if brief_source else []
    compatible = []
    for target in targets:
        try:
            validate_tool_target(tool, target)
            compatible.append(target)
        except AgentError:
            continue
    if len(compatible) != 1:
        raise AgentError('The saved route must match exactly one original target')
    from .generation_spec import host_capabilities
    brief = Brief.model_validate(read_json(Path(brief_source['path'])))
    runtime = host_capabilities(tool)
    if ('generation' if brief.scope == 'create' else 'edit') not in runtime.get('operations', []):
        raise AgentError('The saved route does not support this brief operation')
    if (runtime.get('reference_images', {}).get('support') == 'not_supported'
            and any(r.role == 'identity' and not r.evaluation_only for r in brief.references)):
        raise AgentError('The saved route cannot accept required identity images')
    evidence = next((s for s in entry['materials'] if s['role'] == 'route'), None)
    if not evidence:
        raise AgentError('Ready briefs require saved route availability evidence')
    value = read_json(Path(evidence['path']))
    if (value.get('format') != 'hex.route-readiness.v1' or value.get('tool') != tool
            or value.get('available') is not True
            or value.get('authority') not in ('local_probe', 'host_transcription_not_tool_authenticated')):
        raise AgentError('Route availability evidence does not match this tool')
    try:
        checked = datetime.fromisoformat(value['checked_at'])
        expires = datetime.fromisoformat(value['expires_at'])
        current = datetime.now(timezone.utc)
        if checked.tzinfo is None or expires.tzinfo is None or not checked <= current <= expires:
            raise ValueError('expired or invalid interval')
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentError('Route availability evidence is expired or has no valid interval') from exc
    return {'tool': tool, 'target': compatible[0], 'evidence_sha256': evidence['sha256'],
            'authority': value['authority'], 'availability_scope': 'saved_probe_within_declared_validity',
            'live_probe_performed_by_registry': False, 'expires_at': value['expires_at']}


def _normalize(raw: dict, origin: Path) -> dict:
    r = Registration.model_validate(raw)
    if any(not x.strip() for x in r.capabilities) or len(set(r.capabilities)) != len(r.capabilities):
        raise AgentError('Capabilities must be nonempty and unique')
    if (r.split == 'heldout') != (r.use == 'heldout_spec'):
        raise AgentError('Heldout specifications must remain in the heldout split')
    materials = []
    for role, path in [('brief', r.brief_file), ('sources', r.sources_file),
                       ('need', r.need_file), ('route', r.route_evidence)]:
        if path:
            materials.append(_file(path, origin, role, r.source_family if role in ('brief', 'sources') else None))
    assets = list(r.source_assets) + ([r.revision.parent] if r.revision else [])
    for asset in assets:
        materials.append(_file(asset.path, origin, 'lineage', asset.source_family, asset.sha256))
    if r.brief_file:
        brief_path = next(Path(s['path']) for s in materials if s['role'] == 'brief')
        brief = Brief.model_validate(read_json(brief_path))
        for ref in brief.references:
            materials.append(_file(ref.path, brief_path.parent, 'reference', r.source_family))
    row = {'registration': r.model_dump(), 'materials': materials, 'runs': []}
    if r.readiness == 'ready':
        if r.source_family.startswith('planned-') or not r.brief_file or not r.sources_file:
            raise AgentError('Ready briefs need actual materials and a non-placeholder source family')
        from .provenance import merge_sources, verified_sources
        sources = next(Path(s['path']) for s in materials if s['role'] == 'sources')
        if not any(m['actor'] == 'user' for m in verified_sources(merge_sources(None, sources))):
            raise AgentError('Ready briefs require original user-message provenance')
        _route(row)
    return row


def _tokens(entry: dict) -> set[str]:
    r = entry['registration']
    brief = next((s for s in entry['materials'] if s['role'] == 'brief'), None)
    request = ({'request:' + digest(Brief.model_validate(read_json(Path(brief['path']))).request.strip())}
               if brief else set())
    return request | {'family:' + r['source_family']} | {
        ('family:' + s['source_family']) for s in entry['materials'] if s['source_family']
    } | {'sha:' + s['sha256'] for s in entry['materials'] if s['role'] in ('brief', 'sources', 'reference', 'lineage')}


def _groups(entries: list[dict]) -> list[list[dict]]:
    """Connected ancestry groups also catch indirect overlap and renamed families."""
    remaining, groups = list(entries), []
    while remaining:
        group = [remaining.pop(0)]
        tokens = _tokens(group[0])
        changed = True
        while changed:
            changed = False
            for row in remaining[:]:
                if tokens & _tokens(row):
                    remaining.remove(row)
                    group.append(row)
                    tokens |= _tokens(row)
                    changed = True
        groups.append(group)
    return groups


def register(registry: Path, entry_file: Path) -> dict:
    registry, entry_file = Path(registry).resolve(), Path(entry_file).resolve()
    raw = read_json(entry_file)
    if not isinstance(raw, dict):
        raise AgentError('Brief registration must be a JSON object')
    rows = raw.get('entries') if raw.get('format') == 'hex.brief-plan.v1' else [raw]
    if not isinstance(rows, list) or not rows:
        raise AgentError('Registration requires at least one brief entry')
    incoming = [_normalize(row, entry_file.parent) for row in rows]
    ids = [e['registration']['case_id'] for e in incoming]
    if len(set(ids)) != len(ids):
        raise AgentError('Duplicate case IDs in registration batch')
    with _lock(registry):
        body = _load(registry)
        for row in incoming:
            old = next((e for e in body['entries'] if e['registration']['case_id'] == row['registration']['case_id']), None)
            if old:
                if old['registration'] == row['registration'] and old['materials'] == row['materials']:
                    continue
                if old['runs'] or old['registration']['readiness'] != 'planned':
                    raise AgentError('Prepared or ready registrations are immutable; use a new case ID')
                body['entries'].remove(old)
            body['entries'].append(row)
        if any(len({e['registration']['split'] for e in g}) > 1 for g in _groups(body['entries'])):
            raise AgentError('Same source family or derived material cannot cross dev/heldout splits')
        saved = _save(registry, body)
    return {'registered_case_ids': ids, 'total_registered': len(body['entries']), 'digest': saved['digest'],
            'generation_calls': 0, 'new_image_authorization': False}


def _entry(body: dict, case_id: str) -> dict:
    row = next((e for e in body['entries'] if e['registration']['case_id'] == case_id), None)
    if row is None:
        raise AgentError('Unknown registered case ID: ' + case_id)
    return row


def prepare(registry: Path, case_id: str, out: Path, kb: Path = ROOT / 'knowledge',
            state: Path = ROOT / '.state') -> dict:
    """Run the existing official preparation, without preflight, grants or dispatch."""
    registry, out = Path(registry).resolve(), Path(out).resolve()
    with _lock(registry):
        body = _load(registry)
        row = _entry(body, case_id)
        if row['registration']['readiness'] != 'ready':
            raise AgentError('Brief is planned, not prepared for execution')
        _verified_files(row)
        route = _route(row)
        previous = next((r for r in row['runs'] if r['run'] == str(out)), None)
        if previous:
            summary = _run(row, out)
            return {'case_id': case_id, 'run': str(out), 'reused_existing_task': True,
                    'budget': summary['budget'], 'generation_calls': 0, 'new_image_authorization': False}
        if row['runs']:
            raise AgentError('This brief already has a task; continue it rather than recreating its budget')
        files = {s['role']: Path(s['path']) for s in row['materials'] if s['role'] in ('brief', 'sources', 'need')}
        if row['registration']['revision']:
            raise AgentError('A directed revision must use the existing linked-revision workflow, then report its run')
        if row['registration']['preparation_route'] == 'car_photography':
            from .car_photography import prepare as car_prepare
            car_prepare(files['brief'], out, kb, state, sources_file=files['sources'], need_file=files.get('need'))
        else:
            from .workflow import make_packet, save_packet
            from .generation_spec import host_capabilities
            packet = make_packet(files['brief'], out, kb, state, files.get('need'),
                                 sources_file=files['sources'], execution_profile='production-baseline')
            packet.setdefault('compilation_runtimes', {})[route['target']] = host_capabilities(route['tool'])
            save_packet(packet, out)
        from .workflow import read_packet
        packet = read_packet(out)
        _verified_files(row)
        row['runs'].append({'run': str(out), 'initial_packet_digest': packet['digest'], 'prepared_at': now()})
        _save(registry, body)
        summary = _run(row, out)
    return {'case_id': case_id, 'run': str(out), 'packet_digest': packet['digest'], 'route': route,
            'budget': summary['budget'], 'new_image_authorization': False, 'generation_calls': 0,
            'next': 'Read HOST_TASK.md and continue the existing workflow; dispatch still requires its original authorization'}


def _run(entry: dict, run: Path) -> dict:
    from .run_status import inspect_run
    from .workflow import read_packet
    from .provenance import merge_sources, verified_sources
    from .harness import case_record, _input_hashes
    run = Path(run).resolve()
    before = _input_hashes(run)
    status = inspect_run(run)
    if status['blockers']:
        raise AgentError('Task evidence failed existing inspection: ' + '; '.join(status['blockers']))
    packet = read_packet(run)
    brief_path = next(Path(s['path']) for s in entry['materials'] if s['role'] == 'brief')
    brief = Brief.model_validate(read_json(brief_path))
    if packet['brief']['request'] != brief.request:
        raise AgentError('Reported task belongs to a different registered brief')
    source_path = next(Path(s['path']) for s in entry['materials'] if s['role'] == 'sources')
    original = verified_sources(merge_sources(None, source_path))
    current = verified_sources(packet.get('input_sources'))
    if any(m not in current for m in original):
        raise AgentError('Reported task does not contain registered original message provenance')
    hashes = {s['sha256'] for s in entry['materials'] if s['role'] == 'reference'}
    if not hashes.issubset({s['original_sha256'] for s in packet.get('references', [])}):
        raise AgentError('Reported task reference sources differ from the registered brief')
    cases = [case_record(run, a['id']) for a in status['known_generation_attempts']]
    attempts = [{**a, 'actual_call_recorded': _was_called(c, a, run),
                 'revision_recorded': bool(entry['registration']['revision'] or packet.get('requirement_parent')
                                           or _revision_attempt(c, run))}
                for c, a in zip(cases, status['known_generation_attempts'])]
    _verified_files(entry)
    from .io import within
    for case in cases:
        candidate = case['candidate']
        if candidate and sha(within(run, candidate['file']).read_bytes()) != candidate['sha256']:
            raise AgentError('Candidate bytes changed during coverage inspection')
    if before != _input_hashes(run):
        raise AgentError('Task evidence changed during coverage inspection')
    return {'run': str(run), 'stage': status['stage'], 'budget': status['budget'], 'cases': cases,
            'attempts': attempts,
            'revision': bool(entry['registration']['revision'] or packet.get('requirement_parent'))}


def _revision_attempt(case: dict, run: Path) -> bool:
    if case['route'] == 'host':
        from .host import read_dispatches
        request = next(a['request'] for a in read_dispatches(run)
                       if a['request']['id'] == case['attempt']['request_id'])
        return ((request.get('generation_spec') or {}).get('candidate_role') == 'revision'
                or any(i.get('role') == 'edit_target' for i in request.get('generation_inputs', [])))
    from .api_recovery import load_api_state, _verified
    from .io import within
    row = next(r for r in load_api_state(run)['requests'] if r['id'] == case['attempt']['request_id'])
    snapshot = _verified(within(run, row['snapshot']))
    return ((snapshot.get('generation', {}).get('generation_spec') or {}).get('candidate_role') == 'revision'
            or any(i.get('role') in ('edit_canvas', 'edit_target')
                   for i in snapshot.get('generation', {}).get('inputs', [])))


def _was_called(case: dict, attempt: dict, run: Path) -> bool:
    """Separate dispatch reservations and upload/preparation failures from calls."""
    if case['route'] == 'api':
        return attempt['evidence_scope'] == 'local_execution_record'
    tool = case['model']['tool']
    from .comfy_connector import TOOL_TARGETS
    from .pippit_connector import TOOL_NAME
    family = 'comfy' if tool in TOOL_TARGETS else 'pippit' if tool == TOOL_NAME else None
    if family:
        from .host import read_dispatches
        from . import comfy_execution, pippit_execution
        original = next(a for a in read_dispatches(run) if a['request']['id'] == attempt['id'])
        directory = run / family / attempt['id']
        adapter = comfy_execution if family == 'comfy' else pippit_execution
        state = adapter._read_state(directory, original)
        if not state:
            return False
        if family == 'comfy':
            return (directory / 'submission-started.json').is_file()
        return (directory / 'submit-argv.json').is_file()
    return attempt['evidence_scope'] == 'local_execution_record'


def report(registry: Path, *, case_id: str | None = None, run: Path | None = None) -> dict:
    body = _load(Path(registry).resolve())
    if run is not None and case_id is None:
        raise AgentError('An explicit reported run requires a registered case ID')
    extra = _entry(body, case_id) if run is not None else None
    tasks, blocked, ready, material_verified = {}, [], [], []
    for entry in body['entries']:
        if entry['registration']['readiness'] == 'ready':
            try:
                _verified_files(entry)
                material_verified.append(entry)
                _route(entry)
                ready.append(entry)
            except (AgentError, OSError, ValueError, KeyError) as exc:
                blocked.append({'case_id': entry['registration']['case_id'], 'run': None, 'reason': str(exc)})
        paths = [Path(r['run']) for r in entry['runs']]
        if entry is extra:
            paths.append(Path(run).resolve())
        for path in paths:
            key = str(path.resolve())
            if key in tasks:
                continue
            try:
                _verified_files(entry)
                tasks[key] = {**_run(entry, path), 'case_id': entry['registration']['case_id']}
            except (AgentError, OSError, ValueError, KeyError) as exc:
                blocked.append({'case_id': entry['registration']['case_id'], 'run': key, 'reason': str(exc)})
    attempted, candidates, revisions, unknown = set(), set(), set(), set()
    for path, task in tasks.items():
        for case, attempt in zip(task['cases'], task['attempts']):
            key = (path, attempt['id'])
            if attempt['actual_call_recorded']:
                attempted.add(key)
                if attempt['revision_recorded']:
                    revisions.add(key)
            else:
                unknown.add(key)
            if case['candidate']:
                candidates.add(key)
    groups = _groups(material_verified)
    offline = _offline_coverage(body['entries'], list(tasks.values()))
    return {'format': 'hex.brief-coverage.v1', 'offline_coverage': offline,
            'registered_briefs': len({e['registration']['brief_id'] for e in body['entries']}),
            'planned_briefs': sum(e['registration']['readiness'] == 'planned' for e in body['entries']),
            'declared_ready_briefs': sum(e['registration']['readiness'] == 'ready' for e in body['entries']),
            'ready_briefs': len(ready), 'prepared_tasks': len(tasks), 'source_families': len(groups),
            'source_family_groups': [[e['registration']['case_id'] for e in g] for g in groups],
            'candidates': len(candidates), 'revisions': len(revisions), 'actual_calls': len(attempted),
            'reserved_or_unverified_calls': len(unknown), 'call_count_authority': 'verified_local_execution_records_not_provider_audit',
            'actual_calls_scope': 'image_generation_attempts',
            'source_family_authority': 'declared_ancestry_and_verified_file_hashes_not_semantic_independence_proof',
            'tasks': list(tasks.values()), 'blocked_evidence': blocked,
            'revision_policy': {'suggested_max_per_parent_image': 2,
                'requires': ['hypothesis', 'visible_goal'], 'without_verified_benefit_after_two': 'failure_diagnosis',
                'automatic_revision_or_diagnosis': False},
            'new_image_authorization': False, 'visual_quality': 'not_verified_by_registry'}


def _offline_coverage(entries: list[dict], tasks: list[dict]) -> dict:
    """Verified materials are useful offline even when the generator is unavailable."""
    from .provenance import merge_sources, verified_sources
    bound, unbound = [], []
    for entry in entries:
        try:
            _verified_files(entry)
            files = {m['role']: Path(m['path']) for m in entry['materials']}
            if 'brief' not in files or 'sources' not in files:
                raise AgentError('Original brief or user-message sources not bound')
            Brief.model_validate(read_json(files['brief']))
            if not any(m['actor'] == 'user' for m in verified_sources(merge_sources(None, files['sources']))):
                raise AgentError('Original user-message provenance not bound')
            bound.append(entry)
        except (AgentError, OSError, ValueError, KeyError) as exc:
            unbound.append({'case_id': entry['registration']['case_id'], 'reason': str(exc)})
    groups = _groups(bound)
    domains = {}
    for domain in ('automotive', 'portrait', 'graphic', 'storyboard'):
        rows = [e for e in bound if e['registration']['domain'] == domain]
        ids = {e['registration']['case_id'] for e in rows}
        selected = [t for t in tasks if t['case_id'] in ids]
        domains[domain] = {'registered_briefs': len({e['registration']['brief_id'] for e in entries
                                                    if e['registration']['domain'] == domain}),
                           'material_bound_briefs': len({e['registration']['brief_id'] for e in rows}),
                           'source_groups': len(_groups(rows)), 'tasks': len(selected),
                           'candidates': sum(bool(c['candidate']) for t in selected for c in t['cases'])}
    return {'material_bound_briefs': len({e['registration']['brief_id'] for e in bound}),
            'independent_source_groups': len(groups),
            'source_family_groups': [[e['registration']['case_id'] for e in g] for g in groups],
            'domains': domains, 'unbound': unbound,
            'independence_authority': 'declared_ancestry_file_hashes_and_exact_brief_text_not_semantic_proof',
            'generator_readiness_separate': True, 'visual_quality': 'not_verified'}


def attach_run(registry: Path, case_id: str, run: Path) -> dict:
    """Associate existing evidence; never prepare or mutate the original task."""
    registry, run = Path(registry).resolve(), Path(run).resolve()
    with _lock(registry):
        body = _load(registry)
        entry = _entry(body, case_id)
        if any(e['registration']['case_id'] != case_id and any(Path(r['run']).resolve() == run
               for r in e['runs']) for e in body['entries']):
            raise AgentError('Existing task is already attached to another case')
        _verified_files(entry)
        summary = _run(entry, run)
        reused = any(Path(r['run']).resolve() == run for r in entry['runs'])
        if not reused:
            from .workflow import read_packet
            entry['runs'].append({'run': str(run), 'initial_packet_digest': read_packet(run)['digest'],
                                  'prepared_at': now(), 'association': 'existing_read_only_task'})
            _save(registry, body)
    return {'case_id': case_id, 'run': str(run), 'reused_existing_task': reused,
            'budget': summary['budget'], 'writes_to_run': 0, 'generation_calls': 0,
            'new_image_authorization': False}


def schedule(registry: Path, split: str = 'dev') -> dict:
    """Recommend review of existing candidates before proposing any new generation."""
    if split not in ('dev', 'heldout'):
        raise AgentError('Unknown split')
    body = _load(Path(registry).resolve())
    coverage = report(registry)
    rows = {e['registration']['case_id']: e for e in body['entries']}
    eligible = []
    for task in coverage['tasks']:
        entry = rows[task['case_id']]
        if entry['registration']['split'] != split:
            continue
        for case in task['cases']:
            if case['candidate']:
                eligible.append({'case_id': task['case_id'], 'domain': entry['registration']['domain'],
                                 'run': task['run'], 'request_id': case['attempt']['request_id'],
                                 'candidate_sha256': case['candidate']['sha256'],
                                 'action': 'isolated_visual_review', 'entrypoint': 'hex_agent.intent_review visual',
                                 'related_entrypoints': {'semantic_trace':'hex_agent.minimal_harness trace',
                                    'requires_explicit_primary_choice':True,
                                    'visual_index':'hex_agent.visual_evidence_store build',
                                    'probability_experiment':'hex_agent.visual_decision_gate prepare/replay'},
                                 'model_judgment_authorizes_generation':False})
    eligible.sort(key=lambda x: (coverage['offline_coverage']['domains'][x['domain']]['candidates'],
                                  x['case_id'], x['request_id']))
    return {'format': 'hex.offline-brief-schedule.v1', 'recommendation': eligible[0] if eligible else None,
            'eligible': eligible, 'split': split, 'offline_coverage': coverage['offline_coverage'],
            'unbound_domains': [d for d, v in coverage['offline_coverage']['domains'].items()
                                if not v['material_bound_briefs']],
            'blocked_evidence': coverage['blocked_evidence'], 'generation_calls': 0,
            'new_image_authorization': False, 'writes_to_run': 0,
            'notice': 'Existing review evidence only; coverage does not prove independent visual success.'}


def next_brief(registry: Path, split: str = 'dev') -> dict:
    body = _load(Path(registry).resolve())
    if split not in ('dev', 'heldout'):
        raise AgentError('Unknown split')
    coverage = report(registry)
    covered = {t['case_id'] for t in coverage['tasks'] if any(a['actual_call_recorded'] for a in t['attempts'])}
    past = [e for e in body['entries'] if e['registration']['case_id'] in covered]
    domains = {e['registration']['domain'] for e in past}
    capabilities = {c for e in past for c in e['registration']['capabilities']}
    families = set().union(*[_tokens(e) for e in past]) if past else set()
    eligible, unavailable = [], list(coverage['blocked_evidence'])
    for entry in body['entries']:
        r = entry['registration']
        if r['split'] != split or r['readiness'] != 'ready' or entry['runs'] or r['case_id'] in covered or r['revision']:
            continue
        try:
            _verified_files(entry)
            route = _route(entry)
        except (AgentError, OSError, ValueError, KeyError) as exc:
            unavailable.append({'case_id': r['case_id'], 'reason': str(exc)})
            continue
        novelty = {'uncovered_domain': r['domain'] not in domains,
                   'uncovered_capabilities': [c for c in r['capabilities'] if c not in capabilities],
                   'independent_source_family': not bool(_tokens(entry) & families)}
        score = (int(novelty['independent_source_family']), int(novelty['uncovered_domain']), len(novelty['uncovered_capabilities']))
        eligible.append((score, r['case_id'], {'case_id': r['case_id'], 'brief_id': r['brief_id'],
                                            'novelty': novelty, 'route': route}))
    eligible.sort(key=lambda x: (tuple(-v for v in x[0]), x[1]))
    return {'format': 'hex.next-brief.v1', 'recommendation': eligible[0][2] if eligible else None,
            'eligible_case_ids': [row[1] for row in eligible], 'unavailable': unavailable,
            'planned_not_recommended': [e['registration']['case_id'] for e in body['entries'] if e['registration']['readiness'] == 'planned'],
            'next_action': 'prepare_registered_brief' if eligible else 'prepare_independent_materials_and_route_evidence',
            'recommendation_is_execution_authorization': False, 'generation_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register', 'next', 'prepare', 'report', 'attach-run', 'schedule'])
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--entry', type=Path)
    parser.add_argument('--case-id')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--split', choices=['dev', 'heldout'], default='dev')
    parser.add_argument('--kb', type=Path, default=ROOT / 'knowledge')
    parser.add_argument('--state', type=Path, default=ROOT / '.state')
    parser.add_argument('--format', choices=['json','human'], default='json')
    args = parser.parse_args()
    try:
        if args.action == 'register':
            if not args.entry:
                parser.error('register requires --entry')
            value = register(args.registry, args.entry)
        elif args.action == 'next':
            value = next_brief(args.registry, args.split)
        elif args.action == 'prepare':
            if not args.case_id or not args.out:
                parser.error('prepare requires --case-id and --out')
            value = prepare(args.registry, args.case_id, args.out, args.kb, args.state)
        elif args.action == 'attach-run':
            if not args.case_id or not args.run:
                parser.error('attach-run requires --case-id and --run')
            value = attach_run(args.registry, args.case_id, args.run)
        elif args.action == 'schedule':
            value = schedule(args.registry, args.split)
        else:
            value = report(args.registry, case_id=args.case_id, run=args.run)
    except (AgentError, OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
    if args.format == 'human' and args.action in ('report','schedule'):
        coverage = value['offline_coverage']
        labels = {'automotive':'汽车','portrait':'人像','graphic':'平面','storyboard':'分镜'}
        for domain, counts in coverage['domains'].items():
            print(f"{labels[domain]}：素材已绑定 {counts['material_bound_briefs']} 个需求，"
                  f"来源族 {counts['source_groups']}，任务 {counts['tasks']}，候选 {counts['candidates']}。")
        for item in coverage['unbound']:
            print('未绑定 '+item['case_id']+'：'+item['reason'])
        if args.action == 'schedule':
            choice = value['recommendation']
            print('下一步：复查现有 '+choice['case_id']+' 候选。' if choice else '下一步：绑定缺失题目与原消息／素材；当前没有可复查候选。')
        print('本轮不生成，不授予新额度；素材和候选数量不证明视觉质量。')
    else: print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
