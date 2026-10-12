"""One v2-only local downscale, with original-run reservation before pixels.

This is not a generative editor. It never calls a provider, expands a grant,
creates a run, overwrites the first draft, or claims visual/user acceptance.
The separate receipt deliberately does not masquerade as a model request.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image

from .io import AgentError, MAX_IMAGE_BYTES, digest, read_json, sha, within, write_json

SCOPE = 'downscale_to_requested_size'
FILE = 'bounded-repair.json'
DIRECTORY = 'bounded-repair'
MARKER = 'bounded_repair_reservation'
_ERRORS = (AgentError, OSError, ValueError, KeyError, TypeError, AttributeError)


def enabled(packet: dict) -> bool:
    from .workflow_policy import policy
    from .visual_decision import record_only
    selected = policy(packet)
    return bool(selected and selected['version'] == 2 and selected['deliverable_kind'] != 'prompt_only'
                and record_only(packet))


def has_reservation(run: Path) -> bool:
    run = Path(run)
    if (run / FILE).exists() or (run / DIRECTORY).exists():
        return True
    return (run / 'preflight.json').exists() and MARKER in read_json(run / 'preflight.json')


def _seal(body: dict) -> dict:
    return {**body, 'digest': digest(body)}


def _sealed(value: dict, label: str) -> dict:
    if (not isinstance(value, dict) or value.get('digest') !=
            digest({k: v for k, v in value.items() if k != 'digest'})):
        raise AgentError(label + '摘要或结构无效；保留占用，不能重新返修')
    return value


def _context(run: Path, request_id: str | None = None) -> tuple[dict, dict, dict, dict]:
    from .workflow_policy import _task
    from .native_budget import _native_only, _has_original_transfer, _has_revision, _has_next_revision
    from .host import read_dispatches
    from .generation_authorization import read_authorization
    from .generation_evidence import for_host
    _native_only(run)
    packet, contract, _ = _task(run)
    if not enabled(packet):
        raise AgentError('有界返修仅启用 workflow policy v2；不迁移旧合同')
    # First increment deliberately supports only a single native parent in its
    # original run. Existing linked/API/comparison routes are refused, not reset.
    if (packet.get('requirement_parent') or packet['brief'].get('model_comparison') or
            _has_original_transfer(run) or _has_revision(run) or _has_next_revision(run)):
        raise AgentError('此返修器不支持关联版本、API或模型比较；保留原父图和预算关联')
    attempts = read_dispatches(run)
    if len(attempts) != 1 or attempts[0]['status'] != 'completed':
        raise AgentError('只支持原run唯一已完成首稿；待定、未知、失败或多次调用均不能自动返修')
    parent = attempts[0]
    if request_id is not None and parent['request']['id'] != request_id:
        raise AgentError('返修 request_id 与原首稿不一致')
    image = within(run, parent['output']['file']).read_bytes()
    for_host(run, packet, contract, image)
    authorization = read_authorization(run, packet)
    return packet, contract, parent, authorization


def _grant(packet: dict, authorization: dict) -> dict:
    from .provenance import verified_sources, RepairGrant
    sources = authorization.get('sources', packet['input_sources'])
    rows = verified_sources(sources)
    grants = [row for row in rows if row.get('repair_grant') is not None]
    if len(grants) != 1:
        raise AgentError('须有唯一、独立且已绑定的用户 repair_grant；生图额度不授予返修')
    source = grants[0]
    if (source['actor'] != 'user' or source['origin'] not in ('host_transcription', 'host_message_record')
            or not source['text'].strip() or not source['locator'].strip()
            or sum(row['locator'] == source['locator'] for row in rows) != 1):
        raise AgentError('返修授权必须绑定真实用户原话及唯一定位，不能改名重放')
    grant = RepairGrant.model_validate(source['repair_grant']).model_dump()
    return {'source_id': source['id'], 'source_digest': digest(source), 'quote': source['text'],
            'locator': source['locator'], 'origin': source['origin'], 'grant': grant,
            'authority': 'host_recorded_not_platform_authenticated'}


def _target(parent: dict) -> tuple[int, int]:
    size = (parent['request'].get('generation_spec') or {}).get('requested_size')
    match = re.fullmatch(r'([1-9][0-9]*)x([1-9][0-9]*)', size or '')
    if not match:
        raise AgentError('原请求没有冻结精确像素尺寸；不从比例、提示词或审美意见猜测尺寸')
    return int(match[1]), int(match[2])


def _measured_action(run: Path, parent: dict) -> dict:
    from .generation_spec import image_facts
    output = parent['output']
    raw = within(run, output['file']).read_bytes()
    facts = image_facts(raw)
    width, height = _target(parent)
    if facts['width'] == width and facts['height'] == height:
        raise AgentError('首稿尺寸已符合原请求；不存在可自动修复的尺寸错误')
    if (facts['width'] <= width or facts['height'] <= height or
            facts['width'] * height != facts['height'] * width):
        raise AgentError('只支持同比例缩小；放大、裁切、扩图和比例变更须展示并重新明确范围')
    with Image.open(io.BytesIO(raw)) as image:
        if image.mode not in ('RGB', 'RGBA') or image.format != 'PNG':
            raise AgentError('只支持已规范化的 RGB/RGBA PNG 首稿')
    return {'scope': SCOPE, 'width': width, 'height': height, 'resampling': 'LANCZOS',
            'parent_facts': facts,
            'preserve': ['full_frame', 'aspect_ratio', 'alpha_if_present'],
            'limitation': '缩小会减少像素细节；不修复文字、身份、构图、光影或审美问题'}


def _review_gate(run: Path, packet: dict, contract: dict, parent: dict) -> None:
    """Never classify an existing negative/uncertain visual judgment as sizing."""
    trials_path = run / 'host-image-tests.json'
    if trials_path.exists():
        from .workflow import validate_trial
        from .host import validate_trial_dispatch
        for trial in read_json(trials_path):
            validate_trial(packet, run, contract, trial)
            validate_trial_dispatch(packet, run, trial)
            if trial.get('review') and trial['review'].get('decision') != 'ready_for_user':
                raise AgentError('已有视觉分歧或不确定判断；先展示原图，不自动返修')
    from .workflow_policy import _candidates, _requirements, _path, _bound_stage
    candidates, _ = _candidates(run, packet, contract)
    candidate = next(row for row in candidates if row['request_id'] == parent['request']['id'])
    if _path(run, 'reviews', candidate).exists():
        req = _requirements(run, packet, contract)
        review = _bound_stage(run, 'reviews', packet, contract, candidate, req)['report']['review']
        if review.get('decision') != 'ready_for_user':
            raise AgentError('已有视觉分歧或不确定判断；先展示原图，不自动返修')


def _decision(run: Path) -> str | None:
    if (run / 'user-feedback.json').exists():
        from .feedback import verified_feedback
        return verified_feedback(run)[2]['decision']
    return None


def evaluate(run: Path, request_id: str | None = None, *, issue: str = 'output_size') -> dict:
    """Read-only measured eligibility. The returned checkpoint is not consent."""
    from .workflow import read_packet
    from .generation_authorization import require_not_stopped
    from .generation_permission import require_bounded_repair_permission
    run = Path(run).resolve()
    body = {'format': 'hex.bounded-repair-assessment.v1', 'run': str(run),
            'state': 'blocked', 'eligible': False, 'scope': SCOPE, 'issue': issue,
            'automatic_execution': False, 'requests_submitted': 0,
            'first_draft_preserved': True, 'visual_acceptance': 'not_verified',
            'permission_is_submission_readiness': False}
    try:
        packet = read_packet(run)
        if not enabled(packet):
            return _seal({**body, 'state': 'not_applicable', 'reason': 'requires_workflow_policy_v2'})
        require_not_stopped(run)
        if has_reservation(run):
            record = read_record(run)
            state = {'reserved': 'stopped_after_interruption', 'failed': 'stopped_after_failed_repair',
                     'completed': 'repaired_needs_review'}[record['state']]
            return _seal({**body, 'state': state, 'record': record,
                          'remaining_attempts': 0, 'reason': 'single_repair_already_reserved'})
        packet, contract, parent, authorization = _context(run, request_id)
        body.update(parent_request_id=parent['request']['id'], parent=parent['output'],
                    packet_digest=packet['digest'], contract_digest=contract['digest'],
                    generation_authorization_digest=authorization['digest'])
        if issue == 'aesthetic_disagreement':
            return _seal({**body, 'state': 'show_candidate_first', 'reason': 'aesthetic_disagreement'})
        if issue != 'output_size':
            return _seal({**body, 'state': 'unsupported', 'reason': 'only_proportional_downscale_supported'})
        _review_gate(run, packet, contract, parent)
        permission = _grant(packet, authorization)
        require_bounded_repair_permission(workflow_version=2, max_images=authorization['max_images'],
            attempted_images=1, repair_grant=permission['grant'], repair_attempted=False,
            user_decision=_decision(run))
        action = _measured_action(run, parent)
        body.update(state='eligible', eligible=True, permission=permission, action=action,
                    remaining_attempts=authorization['max_images'] - 1,
                    budget_source=str(run), max_images=authorization['max_images'], attempted_images=1,
                    execution_entrypoint='host.execute_repair',
                    notice='本地缩小也占原剩余一次额度，不计作远程模型调用；结果仍须按原合同复核。')
    except _ERRORS as exc:
        body.update(reason=str(exc))
        if '先展示' in str(exc):
            body['state'] = 'show_candidate_first'
    return _seal(body)


def read_record(run: Path) -> dict:
    """Verify immutable reservation, live parent bytes and optional local output."""
    run = Path(run).resolve()
    try:
        record = _sealed(read_json(run / FILE), '返修记录')
        if (set(record) != {'format', 'reservation', 'state', 'result', 'digest'} or
                record['format'] != 'hex.bounded-repair-record.v1' or
                record['state'] not in ('reserved', 'completed', 'failed')):
            raise AgentError('返修记录结构或状态无效')
        reservation = _sealed(record['reservation'], '返修占用')
        if (set(reservation) != {'format', 'run', 'assessment', 'digest'} or
                reservation['format'] != 'hex.bounded-repair-reservation.v1' or
                reservation['run'] != str(run) or
                read_json(run / 'preflight.json').get(MARKER) != reservation['digest']):
            raise AgentError('原run返修占用标记缺失或不一致')
        assessment = _sealed(reservation['assessment'], '返修评估')
        packet, contract, parent, authorization = _context(run, assessment['parent_request_id'])
        if (assessment['state'] != 'eligible' or assessment['eligible'] is not True or
                assessment['run'] != str(run) or assessment['budget_source'] != str(run) or
                assessment['packet_digest'] != packet['digest'] or
                assessment['contract_digest'] != contract['digest'] or
                assessment['parent'] != parent['output'] or
                assessment['generation_authorization_digest'] != authorization['digest'] or
                assessment['permission'] != _grant(packet, authorization) or
                assessment['action'] != _measured_action(run, parent) or
                assessment['max_images'] != authorization['max_images'] or
                assessment['attempted_images'] != 1 or assessment['remaining_attempts'] != 1):
            raise AgentError('返修绑定的父图、合同、用户许可或原预算已变化')
        from .generation_permission import require_bounded_repair_permission
        require_bounded_repair_permission(workflow_version=2, max_images=authorization['max_images'],
            attempted_images=1, repair_grant=assessment['permission']['grant'], repair_attempted=False)
        if record['state'] == 'reserved' and record['result'] is not None:
            raise AgentError('未结束的返修不能包含完成结果')
        if record['state'] == 'failed' and not isinstance(record['result'], dict):
            raise AgentError('失败返修缺少结果记录')
        if record['state'] == 'completed':
            result = record['result']
            output = result['output']
            if output['file'] != DIRECTORY + '/repaired.png':
                raise AgentError('返修结果须保存在独立文件，不覆盖首稿')
            raw = within(run, output['file']).read_bytes()
            if sha(raw) != output['sha256'] or output['sha256'] == parent['output']['sha256']:
                raise AgentError('返修图像摘要无效或首稿被当作修复结果')
            checked = _check_output(run, parent, raw, assessment['action'])
            if not checked['passed'] or result.get('check') != checked:
                raise AgentError('返修结果未通过原定尺寸与本地变换复检')
        return record
    except _ERRORS as exc:
        raise AgentError('有界返修证据不完整；禁止清零、重试或另领预算：' + str(exc)) from exc


def delivery_asset(run: Path) -> dict | None:
    """A separately identified final asset; never a new model trial or request."""
    run = Path(run).resolve()
    if not has_reservation(run):
        return None
    record = read_record(run)
    if record['state'] != 'completed':
        raise AgentError('本地返修未成功，停止交付；保留首稿和已占用预算')
    assessment = record['reservation']['assessment']
    packet, contract, parent, _ = _context(run, assessment['parent_request_id'])
    from .generation_evidence import for_host
    original = for_host(run, packet, contract, within(run, parent['output']['file']).read_bytes())
    return _seal({'format': 'hex.derived-delivery-asset.v1', 'kind': 'local_transform',
        'asset_id': 'repair-' + record['reservation']['digest'],
        **record['result']['output'], 'packet_digest': packet['digest'], 'contract_digest': contract['digest'],
        'parent': {'request_id': parent['request']['id'], 'candidate_sha256': parent['output']['sha256'],
                   'file': parent['output']['file'], 'route': 'host', 'evidence_digest': original['digest']},
        'transform': {'receipt_file': FILE, 'receipt_digest': record['digest'],
                      'reservation_digest': record['reservation']['digest'], 'action': assessment['action'],
                      'check': record['result']['check'], 'model_calls': 0},
        'notice': '最终图是原首稿的本地同比例缩小副本；父图和原模型回执保持不变。'})


def delivery_evidence(run: Path, image: Path) -> dict | None:
    """Bind final bytes for review without claiming they came from a model."""
    run = Path(run).resolve()
    if Path(image).resolve() != within(run, DIRECTORY + '/repaired.png'):
        return None
    asset = delivery_asset(run)
    if asset is None:
        raise AgentError('派生图缺少先注册的本地变换回执')
    packet, contract, parent, _ = _context(run, asset['parent']['request_id'])
    return _seal({'format': 'hex.local-transform-evidence.v1', 'status': 'verified_local_transform',
        'route': 'local_transform', 'request_id': asset['parent']['request_id'], 'asset_id': asset['asset_id'],
        'candidate_sha256': asset['sha256'], 'packet_digest': packet['digest'],
        'contract_digest': contract['digest'], 'solution': parent['request']['solution'],
        'delivery_asset': asset, 'authority': 'local_deterministic_transform',
        'actual_prompt': '本图仅执行本地同比例缩小，没有新模型提示词或模型调用。父图原提示词：\n' + parent['request']['prompt'],
        'ordered_inputs': [{'id': 'PARENT_CANDIDATE', 'role': 'edit_target',
                            'file': asset['parent']['file'], 'sha256': asset['parent']['candidate_sha256']}],
        'notice': asset['notice']})


def budget_for_run(run: Path) -> dict:
    record = read_record(run)
    assessment = record['reservation']['assessment']
    return {'attempted_calls': 1, 'attempted_images': 1, 'max_images': assessment['max_images'],
            'reserved_image_slots': 2, 'remaining_images': 0, 'dispatch_remaining_images': 0,
            'local_attempted_images': 1, 'local_repair_attempts': 1,
            'budget_source': str(Path(run).resolve()), 'recovery_safe': True,
            'bounded_repair_digest': record['reservation']['digest'],
            'repair_state': record['state'], 'budget_unit': 'original_image_allowance_slots',
            'notice': '保留一次原生成和一次本地返修占用；本地返修没有远程调用或新增费用记录。'}


def _downscale(source: bytes, action: dict) -> bytes:
    with Image.open(io.BytesIO(source)) as image:
        result = image.resize((action['width'], action['height']), Image.Resampling.LANCZOS)
        result.info.clear()
        buffer = io.BytesIO()
        result.save(buffer, format='PNG')
    raw = buffer.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise AgentError('返修结果超出 PNG 文件限制')
    return raw


def _check_output(run: Path, parent: dict, raw: bytes, action: dict) -> dict:
    from .generation_spec import image_facts
    facts = image_facts(raw)
    original = within(run, parent['output']['file']).read_bytes()
    if sha(original) != parent['output']['sha256']:
        raise AgentError('返修期间首稿发生变化')
    # Recompute pixels, not just the size: a malicious/buggy transform cannot
    # report success after cropping, painting or returning an unrelated image.
    with Image.open(io.BytesIO(original)) as image, Image.open(io.BytesIO(raw)) as output:
        expected = image.resize((action['width'], action['height']), Image.Resampling.LANCZOS)
        exact = expected.mode == output.mode and expected.size == output.size and expected.tobytes() == output.tobytes()
    return {'passed': bool(exact and facts['output_format'] == 'png'), 'facts': facts,
            'pixel_transform_verified': exact, 'visual_quality': 'not_verified',
            'user_acceptance': 'not_recorded'}


def execute(run: Path, expected_checkpoint: str, request_id: str | None = None, *,
            issue: str = 'output_size') -> dict:
    """Reserve once, run the real local transform, check it, and stop on failure.

    No recovery replay exists: an interrupted reservation remains spent. Calling
    this twice cannot execute a second transform, even with a stale checkpoint.
    """
    from .native_budget import dispatch_budget_lock
    from .generation_authorization import require_not_stopped
    run = Path(run).resolve()
    with dispatch_budget_lock(run):
        assessment = evaluate(run, request_id, issue=issue)
        if assessment['digest'] != expected_checkpoint:
            raise AgentError('返修检查点已变化；重新评估，不沿旧判断执行')
        if not assessment['eligible']:
            raise AgentError('当前不允许自动返修：' + assessment.get('reason', assessment['state']))
        reservation = _seal({'format': 'hex.bounded-repair-reservation.v1', 'run': str(run),
                             'assessment': assessment})
        # Anchor first. If the second write fails, both generation and repair
        # fail closed instead of recovering a fresh second slot.
        write_json(run / 'preflight.json', {**read_json(run / 'preflight.json'), MARKER: reservation['digest']})
        record = {'format': 'hex.bounded-repair-record.v1', 'reservation': reservation,
                  'state': 'reserved', 'result': None}
        write_json(run / FILE, _seal(record))
        try:
            require_not_stopped(run)
            verified = read_record(run)  # Registration and all bindings before execution.
            parent = verified['reservation']['assessment']['parent']
            source = within(run, parent['file']).read_bytes()
            raw = _downscale(source, assessment['action'])
            output_path = within(run, DIRECTORY + '/repaired.png')
            output_path.parent.mkdir(exist_ok=True)
            with output_path.open('xb') as handle:
                handle.write(raw)
            checked = _check_output(run, {'output': parent}, output_path.read_bytes(), assessment['action'])
            result = {'origin': 'local_deterministic_transform', 'model_calls': 0,
                      'output': {'file': DIRECTORY + '/repaired.png', 'sha256': sha(raw)},
                      'check': checked, 'failure': None if checked['passed'] else 'postcheck_failed'}
            record.update(state='completed' if checked['passed'] else 'failed', result=result)
        except Exception as exc:
            # The registered slot survives every failure; no automatic second try.
            record.update(state='failed', result={'origin': 'local_deterministic_transform',
                          'model_calls': 0, 'failure': type(exc).__name__ + ': ' + str(exc)})
        write_json(run / FILE, _seal(record))
        return {'format': 'hex.bounded-repair-execution.v1', 'state': record['state'],
                'record': read_record(run), 'requests_submitted': 0, 'repair_attempts': 1,
                'remaining_attempts': 0, 'first_draft_preserved': True,
                'next_action': 'review_repaired_candidate' if record['state'] == 'completed' else 'stop_and_show_first_draft',
                'workflow_complete': False, 'visual_acceptance': 'not_verified'}
