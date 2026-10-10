"""Linked native budgets and individually authorized, image-bound revisions."""
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .io import AgentError, digest, read_json, write_json

BINDING_FILE = 'native-budget-transfer.json'
RECEIPT_FILE = 'native-budget-receipt.json'
PREFLIGHT_MARKER = 'native_budget_transfer'
MAX_IMAGES = 2


def _has_original_transfer(run: Path) -> bool:
    if any((run/name).exists() for name in (BINDING_FILE, RECEIPT_FILE)):
        return True
    path = run/'preflight.json'
    if not path.exists():
        return False
    try:
        report = read_json(path)
        if not isinstance(report, dict):
            raise ValueError('preflight must be an object')
    except (OSError, ValueError) as exc:
        raise AgentError('preflight记录损坏；不能排除原生预算关联') from exc
    return PREFLIGHT_MARKER in report


def guard_api(run: Path) -> None:
    if (Path(run)/'packet.json').exists() and read_json(Path(run)/'packet.json').get('requirement_parent'):
        raise AgentError('关联需求版本保留原预算；当前不支持通过API新生成，请按原记录接续')
    if has_transfer(Path(run)):
        raise AgentError('此记录关联原生预算转接；不支持API入口，不能另起预算或提交模型请求')


def _native_only(run: Path) -> None:
    if any((run/name).exists() for name in ('api-state.json', 'calls.json', 'image-tests.json', 'api')):
        raise AgentError('预算转接只支持原生记录；不能混入API调用或另起预算')


def _snapshot(run: Path) -> dict:
    from .contracts import Solution
    from .design import acceptance
    from .workflow import read_packet, validate_solution
    _native_only(run)
    packet = read_packet(run)
    solution = Solution.model_validate(read_json(run/'solution.json'))
    validate_solution(packet, solution)
    contract = read_json(run/'acceptance.json')
    if not (run/'preflight.json').is_file() or acceptance(packet, solution.design) != contract:
        raise AgentError('预算转接要求已preflight且冻结标准与方案一致')
    return {'packet_digest': packet['digest'], 'contract_digest': contract['digest']}


def _parent_snapshot(run: Path) -> dict:
    from .feedback import verified_feedback
    from .host import read_dispatches
    _, _, feedback = verified_feedback(run)
    if feedback['decision'] != 'rejected_by_user':
        raise AgentError('只能转接用户已明确否决且证据有效的原生记录')
    attempts = read_dispatches(run)
    if not attempts or any(a['status'] in ('pending', 'outcome_unknown') for a in attempts):
        raise AgentError('父记录存在未知调用或缺少已登记调用；不能转接预算')
    return {**_snapshot(run), 'feedback_digest': digest(read_json(run/'user-feedback.json')),
            'attempts_digest': digest(attempts)}


def _linked_parent_candidate(parent: Path, child: Path, pp: dict, cp: dict) -> dict | None:
    """Recheck an explicit comparison image without changing parent selection."""
    link = cp.get('requirement_parent')
    base = {'run': str(parent), 'packet_digest': pp['digest']}
    if not isinstance(link, dict):
        raise AgentError('续改须严格关联实际父版本')
    if (set(link) == {*base, 'reuse_parent_input'} and link.get('reuse_parent_input') is True
            and all(link.get(key) == value for key, value in base.items())):
        if (cp.get('need', {}).get('understanding') or {}).get('task_mode') != 'local_edit':
            raise AgentError('同输入重测必须保留局部编辑范围')
        from .requirement_revision import reusable_input_candidate
        return reusable_input_candidate(parent)
    if link == base:
        if (pp['brief'].get('model_comparison') and
                (cp['need'].get('understanding') or {}).get('task_mode') == 'local_edit'):
            raise AgentError('双模型比较的关联编辑须明确绑定父图request_id')
        return None
    if (set(link) != {*base, 'parent_request_id', 'parent_candidate_sha256'} or
            any(link.get(key) != value for key, value in base.items()) or
            not isinstance(link.get('parent_request_id'), str) or
            not isinstance(link.get('parent_candidate_sha256'), str)):
        raise AgentError('关联父图身份或原需求摘要无效')
    from .feedback import verified_candidate
    _, _, candidate = verified_candidate(parent, link['parent_request_id'])
    if candidate['sha256'] != link['parent_candidate_sha256']:
        raise AgentError('关联父图摘要与实际候选不一致')
    return candidate


def _continuation_snapshot(parent: Path, child: Path, source_id: str) -> dict:
    """Bind a continuation to a shared grant without increasing its ceiling."""
    from .generation_authorization import read_authorization, FORMAT
    from .provenance import verified_sources
    from .workflow import read_packet
    from .host import read_dispatches
    from .run_status import _verify_attempt
    parent_packet, child_packet = read_packet(parent), read_packet(child)
    candidate = _linked_parent_candidate(parent, child, parent_packet, child_packet)
    authorizations = [read_authorization(run, packet) for run, packet in
                      ((parent, parent_packet), (child, child_packet))]
    if any(auth['format'] != FORMAT or auth['max_images'] != MAX_IMAGES
           or auth['history'][-1]['source_id'] != source_id for auth in authorizations):
        raise AgentError('续作须在父子有效授权中绑定同一新增用户许可，累计上限为2')
    messages = [next(row for row in verified_sources(auth['sources'])
                     if row['id'] == source_id) for auth in authorizations]
    if messages[0] != messages[1]:
        raise AgentError('续作许可须为原packet之外的同一新增用户消息')
    existing_edit = None
    if source_id in {row['id'] for row in verified_sources(parent_packet['input_sources'])}:
        existing_edit = _existing_allocation_edit(parent_packet, child_packet, candidate)
    attempts = read_dispatches(parent)
    if len(attempts) != 1 or attempts[0]['status'] not in ('completed', 'failed'):
        raise AgentError('续作只支持原记录一次已知调用；待定、未知或已耗尽不能续作')
    snapshot = _snapshot(parent)
    for attempt in attempts:
        _verify_attempt(parent_packet, parent, read_json(parent/'acceptance.json'), attempt)
    feedback_digest = None
    if (parent/'user-feedback.json').exists():
        from .feedback import verified_feedback
        verified_feedback(parent)
        feedback_digest = digest(read_json(parent/'user-feedback.json'))
    evidence = {**snapshot, 'attempts_digest': digest(attempts),
                'feedback_digest': feedback_digest,
                'authorization_digests': [auth['digest'] for auth in authorizations],
                'source_digest': digest(messages[0])}
    if existing_edit is not None:
        evidence['existing_allocation_edit'] = existing_edit
    return evidence


def _existing_allocation_edit(parent_packet: dict, child_packet: dict, candidate: dict | None) -> dict:
    """A remaining image is allocation, not a new grant or a redraw licence."""
    from .provenance import SourceRef, resolve_refs, verified_sources
    from .reference_gate import validate_reference_policy
    previous = verified_sources(parent_packet['input_sources'])
    current = verified_sources(child_packet['input_sources'])
    understanding = child_packet.get('need', {}).get('understanding') or {}
    change = child_packet.get('need', {}).get('brief_change') or {}
    preserve = understanding.get('preserve', [])
    if (candidate is None or current[:len(previous)] != previous or
            understanding.get('task_mode') != 'local_edit' or change.get('scope') not in
            ('edit', 'color_only', 'relight', 'scene') or
            not understanding.get('change_scope', '').strip() or not preserve or
            any(not value.strip() for value in preserve)):
        raise AgentError('复用原有额度须绑定真实父图、局部改变和保留项及新增用户消息')
    previous_ids = {row['id'] for row in previous}
    previous_locators = {row['locator'] for row in previous}
    sources = resolve_refs(child_packet['input_sources'],
                           [SourceRef.model_validate(row) for row in change.get('source_refs', [])])
    new_users = [row for row in sources if row['actor'] == 'user' and
                 row['origin'] in ('host_transcription', 'host_message_record') and
                 row['id'] not in previous_ids and row['locator'] not in previous_locators]
    if not new_users:
        raise AgentError('复用原有额度的范围变更须引用新增用户消息；旧指令或机器观察不能代替')
    targets = validate_reference_policy(child_packet)['edit_target_ids']
    target_ids = [row['id'] for row in child_packet['references'] if row['id'] in targets and
                  row['sha256'] == candidate['sha256']]
    if not target_ids:
        raise AgentError('复用原有额度须以所选真实父图作为已观察的编辑输入')
    return {'change_scope': understanding['change_scope'], 'preserve': preserve,
            'target_ids': target_ids, 'change_sources': [digest(row) for row in new_users]}


def _read_transfer(run: Path) -> tuple[dict, Path, Path]:
    record = read_json(run/BINDING_FILE)
    fields = {'format', 'parent_run', 'child_run', 'parent', 'child', 'max_images', 'digest'}
    if isinstance(record, dict) and record.get('format') == 'liu.native-budget-transfer.v2':
        fields.add('source_id')
    if not isinstance(record, dict) or set(record) != fields:
        raise AgentError('原生预算转接记录结构无效；不能推断独立额度')
    body = {k: v for k, v in record.items() if k != 'digest'}
    if (record['format'] not in ('liu.native-budget-transfer.v1', 'liu.native-budget-transfer.v2') or record['max_images'] != MAX_IMAGES
            or digest(body) != record['digest']):
        raise AgentError('原生预算转接摘要或原始上限不一致')
    parent, child = Path(record['parent_run']), Path(record['child_run'])
    if (not parent.is_absolute() or not child.is_absolute() or parent != parent.resolve()
            or child != child.resolve() or parent == child or run not in (parent, child)):
        raise AgentError('预算转接路径无效、循环或不属于当前记录')
    receipt = {'format': 'liu.native-budget-receipt.v1', 'parent_run': str(parent),
               'child_run': str(child), 'transfer_digest': record['digest']}
    for endpoint in (parent, child):
        if (read_json(endpoint/BINDING_FILE) != record or read_json(endpoint/RECEIPT_FILE) != receipt
                or read_json(endpoint/'preflight.json').get(PREFLIGHT_MARKER) != receipt):
            raise AgentError('父子预算关联不完整或不一致；禁止回退为独立额度')
    parent_snapshot = (_continuation_snapshot(parent, child, record['source_id'])
                       if record['format'] == 'liu.native-budget-transfer.v2' else _parent_snapshot(parent))
    if parent_snapshot != record['parent'] or _snapshot(child) != record['child']:
        raise AgentError('预算转接所绑定的需求、标准、反馈或原调用已变化')
    return record, parent, child


def _recorded_maximum(run: Path) -> int:
    """Keep legacy bookkeeping readable; this does not grant new generation."""
    from .workflow import read_packet
    packet = read_packet(run)  # Unknown source versions must never fall back.
    if packet.get('source_contract_version') is None:
        # Historical transfer records were sealed with the original two-image
        # ceiling. Host generation still requires the current source contract.
        return MAX_IMAGES
    from .generation_authorization import read_authorization
    return read_authorization(run, packet)['max_images']


def _original_budget(run: Path) -> dict | None:
    """Read both authoritative ledgers; partial registration never means a fresh budget."""
    from .host import read_dispatches
    from .run_status import _verify_attempt
    from .workflow import read_packet
    run = Path(run).resolve()
    if not _has_original_transfer(run):
        return None
    try:
        record, parent, child = _read_transfer(run)
        ledgers = {endpoint: read_dispatches(endpoint) for endpoint in (parent, child)}
        for endpoint, attempts in ledgers.items():
            packet, contract = read_packet(endpoint), read_json(endpoint/'acceptance.json')
            for attempt in attempts:
                _verify_attempt(packet, endpoint, contract, attempt)
        parent_used, child_used = len(ledgers[parent]), len(ledgers[child])
        used = parent_used + child_used
        if not 0 < parent_used < MAX_IMAGES or used > MAX_IMAGES:
            raise AgentError('父子原生调用总数不符合原始预算')
        maximum = min(MAX_IMAGES, *(_recorded_maximum(endpoint) for endpoint in (parent, child)))
        remaining = max(0, maximum - used)
        return {'attempted_calls': used, 'attempted_images': used, 'max_images': maximum,
                'remaining_images': remaining, 'dispatch_remaining_images': remaining if run == child else 0,
                'local_attempted_images': len(ledgers[run]), 'budget_source': str(parent),
                'transfer_child': str(child), 'transfer_role': 'parent' if run == parent else 'child',
                'transfer_digest': record['digest'], 'recovery_safe': True}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AgentError('原生预算关联缺失、损坏或不完整；禁止推断独立额度') from exc


@contextmanager
def dispatch_budget_lock(run: Path):
    """Serialize spending at the original run, then recheck under both run locks."""
    from .host import dispatch_lock
    run = Path(run).resolve()
    initial = budget_for_run(run)
    owner = Path(initial['budget_source']) if initial else run
    with ExitStack() as stack:
        stack.enter_context(dispatch_lock(owner))
        if owner != run:
            stack.enter_context(dispatch_lock(run))
        budget = budget_for_run(run)
        current_owner = Path(budget['budget_source']) if budget else run
        if current_owner != owner or bool(initial) != bool(budget):
            raise AgentError('预算关联在取得锁前发生变化；先重新检查，不提交请求')
        yield budget


def transfer_budget(parent_run: Path, child_run: Path, source_id: str | None = None) -> dict:
    """Bind an unused frozen child by verified rejection or explicit new permission."""
    from .host import dispatch_lock, read_dispatches
    from .run_status import inspect_run
    parent, child = Path(parent_run).resolve(), Path(child_run).resolve()
    if parent == child or not parent.is_dir() or not child.is_dir():
        raise AgentError('预算转接须指定不同且已存在的父子记录')
    with dispatch_lock(parent), dispatch_lock(child):
        if has_transfer(parent) or has_transfer(child):
            raise AgentError('已有或未完成的预算关联；不能重复领取、改绑或继续转接')
        _native_only(parent)
        _native_only(child)
        source = inspect_run(parent, _dispatch_lock_held=True)
        target = inspect_run(child, _dispatch_lock_held=True)
        if source_id is not None:
            parent_snapshot = _continuation_snapshot(parent, child, source_id)
            if source['blockers'] or source['stage'] in ('blocked', 'awaiting_result'):
                raise AgentError('父记录证据阻断；不能续作')
        elif source['stage'] != 'exported' or source['human_acceptance'] != 'rejected_by_user':
            raise AgentError('父记录须已验证用户否决且无待定调用或证据阻断')
        unused_child = not read_dispatches(child)
        exported_prompt_only = (target['stage'] == 'exported' and not target['blockers']
                                and target['generation_status'] == 'not_attempted'
                                and not target['candidates']
                                and read_json(child/'result.json').get('status') == 'prompt_only_not_tested')
        if not unused_child or not (target['stage'] == 'ready_for_dispatch' or exported_prompt_only):
            raise AgentError('子记录须已preflight冻结且尚未尝试生成；已导出的提示词须无候选和证据阻断')
        if exported_prompt_only:
            from .contracts import Solution
            from .workflow import read_packet, validate_solution
            packet = read_packet(child)
            solution = Solution.model_validate(read_json(child/'solution.json'))
            if read_json(child/'preflight.json') != validate_solution(packet, solution):
                raise AgentError('已导出的子记录preflight与当前冻结方案不一致')
        maximum = min(MAX_IMAGES, *(_recorded_maximum(endpoint) for endpoint in (parent, child)))
        remaining = maximum - len(read_dispatches(parent))
        if remaining <= 0:
            raise AgentError('父记录原始预算已用完；不能转接或新增额度')
        body = {'format': 'liu.native-budget-transfer.v1', 'parent_run': str(parent),
                'child_run': str(child), 'parent': parent_snapshot if source_id is not None else _parent_snapshot(parent),
                'child': _snapshot(child), 'max_images': MAX_IMAGES}
        if source_id is not None:
            body.update(format='liu.native-budget-transfer.v2', source_id=source_id)
        binding = {**body, 'digest': digest(body)}
        receipt = {'format': 'liu.native-budget-receipt.v1', 'parent_run': str(parent),
                   'child_run': str(child), 'transfer_digest': binding['digest']}
        # Anchor the association in the existing preflight before sidecars.
        # Child first: any partial registration must stop it claiming a fresh 0/2.
        for endpoint in (child, parent):
            report = read_json(endpoint/'preflight.json')
            write_json(endpoint/'preflight.json', {**report, PREFLIGHT_MARKER: receipt})
        write_json(child/RECEIPT_FILE, receipt)
        write_json(parent/RECEIPT_FILE, receipt)
        write_json(child/BINDING_FILE, binding)
        write_json(parent/BINDING_FILE, binding)
        return {'parent_run': str(parent), 'child_run': str(child), 'transfer_digest': binding['digest'],
                'remaining_images': remaining, 'requests_submitted': 0,
                'notice': '仅转接原有剩余次数；父记录不再生成，失败及未知调用仍占次数。'}

REVISION_FILE = 'native-revision-grant.json'
REVISION_MARKER = 'native_revision_grant'


def _has_revision(run: Path) -> bool:
    return (run/REVISION_FILE).exists() or ((run/'preflight.json').exists() and
        REVISION_MARKER in read_json(run/'preflight.json'))


def has_transfer(run: Path) -> bool:
    return _has_original_transfer(run) or _has_revision(run) or _has_next_revision(run)


def _revision_evidence(parent: Path, child: Path, source_id: str) -> dict:
    from .generation_authorization import read_authorization
    from .provenance import verified_sources
    from .workflow import read_packet
    from .host import read_dispatches
    from .run_status import _verify_attempt
    prior = _original_budget(parent)
    if not prior or prior['transfer_role'] != 'child' or prior['remaining_images'] != 0:
        raise AgentError('新增续改仅支持旧关联预算已耗尽的末端版本')
    pp, cp = read_packet(parent), read_packet(child)
    _linked_parent_candidate(parent, child, pp, cp)
    parent_snapshot = _parent_snapshot(parent)
    auth = read_authorization(child, cp)
    if auth['max_images'] != 1 or len(auth['history']) != 1 or auth['history'][0]['source_id'] != source_id:
        raise AgentError('新增续改须独立绑定新用户许可且仅新增一张')
    source = next(row for row in verified_sources(auth['sources']) if row['id'] == source_id)
    for endpoint in (Path(prior['budget_source']), parent):
        old_auth = read_authorization(endpoint, read_packet(endpoint))
        if source_id in {row['id'] for row in verified_sources(old_auth['sources'])}:
            raise AgentError('续改不得重复消费旧来源许可')
        if any(a['status'] not in ('completed', 'failed') for a in read_dispatches(endpoint)):
            raise AgentError('旧链存在未知或未完成调用')
    for attempt in read_dispatches(child):
        _verify_attempt(cp, child, read_json(child/'acceptance.json'), attempt)
    return {'parent': parent_snapshot, 'prior_budget': prior, 'child': _snapshot(child),
            'authorization_digest': auth['digest'], 'source_digest': digest(source)}


def _legacy_budget(run: Path) -> dict | None:
    run = Path(run).resolve()
    if not _has_revision(run):
        return _original_budget(run)
    from .host import read_dispatches
    try:
        record = read_json(run/REVISION_FILE)
        fields = {'format', 'parent_run', 'child_run', 'source_id', 'evidence', 'digest'}
        if (set(record) != fields or record['format'] != 'liu.native-revision-grant.v1' or
                record['digest'] != digest({k: v for k, v in record.items() if k != 'digest'})):
            raise AgentError('新增续改关联损坏')
        parent, child = Path(record['parent_run']), Path(record['child_run'])
        if (not parent.is_absolute() or not child.is_absolute() or parent != parent.resolve() or
                child != child.resolve() or parent == child or run not in (parent, child)):
            raise AgentError('新增续改关联路径无效')
        for endpoint in (parent, child):
            if (read_json(endpoint/REVISION_FILE) != record or
                    read_json(endpoint/'preflight.json').get(REVISION_MARKER) != record['digest']):
                raise AgentError('新增续改双向关联不完整')
        evidence = _revision_evidence(parent, child, record['source_id'])
        if evidence != record['evidence']:
            raise AgentError('新增续改绑定的旧支出、否决或新授权已变化')
        local = len(read_dispatches(child))
        if local > 1:
            raise AgentError('新增续改超过一次许可')
        used = evidence['prior_budget']['attempted_images'] + local
        maximum = evidence['prior_budget']['max_images'] + 1
        return {'attempted_calls': used, 'attempted_images': used, 'max_images': maximum,
                'remaining_images': 1-local, 'dispatch_remaining_images': 1-local if run == child else 0,
                'local_attempted_images': len(read_dispatches(run)), 'budget_source': str(parent),
                'transfer_child': str(child), 'transfer_role': 'parent' if run == parent else 'child',
                'transfer_digest': record['digest'], 'recovery_safe': True,
                'explicit_revision_grant': True, 'grant_max_images': 1,
                'grant_attempted_images': local}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AgentError('新增续改证据缺失或损坏，禁止恢复独立额度') from exc


def grant_revision(parent_run: Path, child_run: Path, source_id: str) -> dict:
    """Add exactly one newly authorized revision without rewriting prior budgets."""
    from .host import dispatch_lock, read_dispatches
    parent, child = Path(parent_run).resolve(), Path(child_run).resolve()
    if parent == child or not parent.is_dir() or not child.is_dir():
        raise AgentError('须指定不同且已存在的续改父子版本')
    with dispatch_lock(parent), dispatch_lock(child):
        if _has_next_revision(parent) or has_transfer(child) or read_dispatches(child):
            raise AgentError('许可已绑定、关联未完成或子版本已调用，不能重复消费')
        from .workflow import read_packet
        # Existing v1 grants remain byte-for-byte readable. New local edits and
        # successors of a prior grant use an append-only, multi-generation link.
        local_edit = (read_packet(child).get('need', {}).get('understanding') or {}).get('task_mode') == 'local_edit'
        if local_edit or _has_revision(parent):
            return _grant_next_revision(parent, child, source_id)
        if _has_revision(parent) or has_transfer(child) or read_dispatches(child):
            raise AgentError('许可已绑定、关联未完成或子版本已调用，不能重复消费')
        evidence = _revision_evidence(parent, child, source_id)
        body = {'format': 'liu.native-revision-grant.v1', 'parent_run': str(parent),
                'child_run': str(child), 'source_id': source_id, 'evidence': evidence}
        record = {**body, 'digest': digest(body)}
        for endpoint in (child, parent):
            write_json(endpoint/'preflight.json', {**read_json(endpoint/'preflight.json'),
                                                   REVISION_MARKER: record['digest']})
        for endpoint in (child, parent):
            write_json(endpoint/REVISION_FILE, record)
        return {'parent_run': str(parent), 'child_run': str(child), 'grant_digest': record['digest'],
                'remaining_images': 1, 'cumulative_max_images': evidence['prior_budget']['max_images']+1,
                'requests_submitted': 0}


NEXT_REVISION_FILE = 'native-revision-next.json'
NEXT_REVISION_MARKER = 'native_revision_next'


def _has_next_revision(run: Path) -> bool:
    return (run/NEXT_REVISION_FILE).exists() or ((run/'preflight.json').exists() and
        NEXT_REVISION_MARKER in read_json(run/'preflight.json'))


def _base_budget(run: Path, visited: frozenset[Path]) -> dict | None:
    """Read the incoming grant only, so an outgoing link cannot recurse itself."""
    if run in visited:
        raise AgentError('续改版本关联存在循环；禁止推断预算')
    if _has_revision(run):
        record = read_json(run/REVISION_FILE)
        if record.get('format') == 'liu.native-revision-grant.v2':
            return _next_revision_budget(run, record, visited | {run})
    return _legacy_budget(run)


def _next_revision_evidence(parent: Path, child: Path, source_id: str,
                            visited: frozenset[Path]) -> dict:
    from .feedback import verified_candidate, verified_feedback
    from .generation_authorization import read_authorization
    from .host import read_dispatches
    from .provenance import verified_sources
    from .reference_gate import validate_reference_policy
    from .run_status import _verify_attempt
    from .workflow import read_packet

    pp, cp = read_packet(parent), read_packet(child)
    linked_candidate = _linked_parent_candidate(parent, child, pp, cp)
    _native_only(parent)
    _native_only(child)
    prior = _base_budget(parent, visited)
    attempts = read_dispatches(parent)
    reuse_unused = (not attempts and prior is not None and prior.get('transfer_role')=='child'
                    and prior.get('remaining_images')==1 and prior.get('local_attempted_images')==0
                    and prior.get('explicit_revision_grant') is True and prior.get('grant_max_images')==1
                    and prior.get('grant_attempted_images')==0 and linked_candidate is not None)
    if (not attempts and not reuse_unused) or any(a['status'] not in ('completed', 'failed') for a in attempts):
        raise AgentError('父版本存在未知、待定或缺失调用；不能续改')
    for attempt in attempts:
        _verify_attempt(pp, parent, read_json(parent/'acceptance.json'), attempt)
    if prior is None:
        maximum = _recorded_maximum(parent)
        prior = {'attempted_images': len(attempts), 'max_images': maximum,
                 'remaining_images': maximum-len(attempts), 'transfer_role': 'child'}
    if prior['transfer_role'] != 'child' or (prior['remaining_images'] != 0 and not reuse_unused):
        raise AgentError('新增单张许可只用于原有预算已耗尽的末端版本')

    if linked_candidate is None:
        _, _, candidate = verified_candidate(parent)
    else:
        candidate = linked_candidate
    feedback_digest, decision = None, None
    if (parent/'user-feedback.json').exists():
        _, _, feedback = verified_feedback(parent)
        decision = feedback['decision']
        feedback_digest = digest(read_json(parent/'user-feedback.json'))
    auth = read_authorization(child, cp)
    if auth['max_images'] != 1 or len(auth['history']) != 1 or auth['history'][0]['source_id'] != source_id:
        raise AgentError('每次续改必须独立绑定一个新用户许可且只新增一张')
    source = next(row for row in verified_sources(auth['sources']) if row['id'] == source_id)
    parent_auth = read_authorization(parent, pp)
    old_sources = verified_sources(parent_auth.get('sources', pp['input_sources']))
    child_sources = verified_sources(cp['input_sources'])
    previous_sources = verified_sources(pp['input_sources'])
    if child_sources[:len(previous_sources)] != previous_sources:
        raise AgentError('续改来源必须保留父版本原始消息，不得移除或改写')
    if (source not in child_sources or any(source['id'] == row['id'] or source['locator'] == row['locator']
                                         for row in old_sources)):
        raise AgentError('续改须引用新增消息，不得重复消费父版本来源或改名重放')

    understanding = cp['need'].get('understanding') or {}
    # Change/preserve are the host's bounded interpretation, including retained
    # parent requirements. Users need not repeat them verbatim in each message.
    # _snapshot validates real intent sources and hard constraints separately;
    # this gate does not infer semantic permission from matching words.
    change = understanding.get('change_scope', '')
    preserve = understanding.get('preserve', [])
    solution = read_json(child/'solution.json')
    claims = solution['design']['intent_claims']
    if (not change.strip() or not preserve or any(not value.strip() for value in preserve) or
            not any(not claim['inferred'] and any(ref['source_id'] == source_id
                       for ref in claim.get('source_refs', []))
                    for claim in claims)):
        raise AgentError('续改须引用新增用户修改消息，并记录非空改变项与保留项；不能从接受或机器意见推断许可')
    target_ids = []
    if understanding.get('task_mode') == 'local_edit':
        targets = validate_reference_policy(cp)['edit_target_ids']
        target_ids = [r['id'] for r in cp['references']
                      if r['id'] in targets and r['sha256'] == candidate['sha256']]
        if not target_ids:
            raise AgentError('局部续改必须把所选父图作为已观察的实际编辑输入')
    elif decision != 'rejected_by_user':
        raise AgentError('保留或已接受图片的续改须使用local_edit和真实父图；接受不是重画授权')
    for attempt in read_dispatches(child):
        _verify_attempt(cp, child, read_json(child/'acceptance.json'), attempt)
    result = {'parent': {**_snapshot(parent), 'candidate_sha256': candidate['sha256'],
                       'request_id': candidate['request_id'], 'feedback_digest': feedback_digest,
                       'attempts_digest': digest(attempts)},
            'prior_budget': prior, 'child': _snapshot(child),
            'authorization_digest': auth['digest'], 'source_digest': digest(source),
            'edit': {'change_scope': change, 'preserve': preserve, 'target_ids': target_ids}}
    if reuse_unused:result['reuses_unspent_allocation']=True
    return result


def _next_revision_budget(run: Path, record: dict, visited: frozenset[Path]) -> dict:
    from .host import read_dispatches
    fields = {'format', 'parent_run', 'child_run', 'source_id', 'evidence', 'digest'}
    if (set(record) != fields or record['format'] != 'liu.native-revision-grant.v2' or
            record['digest'] != digest({k: v for k, v in record.items() if k != 'digest'})):
        raise AgentError('连续续改记录结构或摘要无效')
    parent, child = Path(record['parent_run']), Path(record['child_run'])
    if (not parent.is_absolute() or not child.is_absolute() or parent != parent.resolve() or
            child != child.resolve() or parent == child or run not in (parent, child)):
        raise AgentError('连续续改关联路径无效')
    if (read_json(parent/NEXT_REVISION_FILE) != record or read_json(child/REVISION_FILE) != record or
            read_json(parent/'preflight.json').get(NEXT_REVISION_MARKER) != record['digest'] or
            read_json(child/'preflight.json').get(REVISION_MARKER) != record['digest']):
        raise AgentError('连续续改双向关联不完整，不能恢复独立额度')
    evidence = _next_revision_evidence(parent, child, record['source_id'], visited)
    if evidence != record['evidence']:
        raise AgentError('连续续改绑定的父图、修改目标、旧支出或新授权已改变')
    local = len(read_dispatches(child))
    if local > 1:
        raise AgentError('续改超过单张许可')
    used = evidence['prior_budget']['attempted_images'] + local
    maximum = evidence['prior_budget']['max_images'] + (0 if evidence.get('reuses_unspent_allocation') else 1)
    return {'attempted_calls': used, 'attempted_images': used, 'max_images': maximum,
            'remaining_images': 1-local, 'dispatch_remaining_images': 1-local if run == child else 0,
            'local_attempted_images': len(read_dispatches(run)), 'budget_source': str(parent),
            'transfer_child': str(child), 'transfer_role': 'parent' if run == parent else 'child',
            'transfer_digest': record['digest'], 'recovery_safe': True,
            'explicit_revision_grant': True, 'grant_max_images': 1, 'grant_attempted_images': local}


def budget_for_run(run: Path) -> dict | None:
    run = Path(run).resolve()
    try:
        if _has_next_revision(run):
            return _next_revision_budget(run, read_json(run/NEXT_REVISION_FILE), frozenset())
        return _base_budget(run, frozenset())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AgentError('连续续改证据缺失或损坏；禁止恢复独立额度') from exc


def _grant_next_revision(parent: Path, child: Path, source_id: str) -> dict:
    evidence = _next_revision_evidence(parent, child, source_id, frozenset({child}))
    body = {'format': 'liu.native-revision-grant.v2', 'parent_run': str(parent),
            'child_run': str(child), 'source_id': source_id, 'evidence': evidence}
    record = {**body, 'digest': digest(body)}
    # Mark both endpoints before sidecars; an interrupted registration fails closed.
    for endpoint, marker in ((child, REVISION_MARKER), (parent, NEXT_REVISION_MARKER)):
        write_json(endpoint/'preflight.json', {**read_json(endpoint/'preflight.json'), marker: record['digest']})
    write_json(child/REVISION_FILE, record)
    write_json(parent/NEXT_REVISION_FILE, record)
    return {'parent_run': str(parent), 'child_run': str(child), 'grant_digest': record['digest'],
            'remaining_images': 1,
            'cumulative_max_images': evidence['prior_budget']['max_images']+(0 if evidence.get('reuses_unspent_allocation') else 1),
            'requests_submitted': 0}
