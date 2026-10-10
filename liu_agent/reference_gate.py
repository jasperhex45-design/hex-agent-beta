"""Shared reference admission and input ledger; submission never proves visual influence."""
from .io import AgentError, digest
from .contracts import ReferencePolicy


def build_reference_policy(packet: dict) -> dict:
    understanding = packet.get('need', {}).get('understanding') or {}
    mode = understanding.get('task_mode')
    if mode not in ('open_creation', 'reference_transfer', 'local_edit'):
        raise AgentError('参考策略需要当前任务模式，先完成需求理解')
    return ReferencePolicy(version=1, task_mode=mode,
                           understanding_digest=digest(understanding),
                           source_digest=digest(packet.get('input_sources'))).model_dump()


def read_reference_policy(packet: dict) -> dict:
    """No migration on read: old packets keep their original reference rule."""
    raw = packet.get('reference_policy')
    if raw is None:
        if packet.get('reference_policy_version') is not None:
            raise AgentError('reference_policy 缺失，不能移除新版策略后回退旧规则')
        return {'version': 0, 'task_mode': None,
                'allow_without_baseline': packet.get('brief', {}).get('exploration_allowed', False)}
    try:
        policy = ReferencePolicy.model_validate(raw).model_dump()
    except ValueError as exc:
        raise AgentError('未知或无效 reference_policy；不能降级成旧探索模式') from exc
    if policy != build_reference_policy(packet):
        raise AgentError('reference_policy 与当前需求或消息来源不一致，先 refresh')
    if packet.get('reference_policy_version', 1) != 1:
        raise AgentError('未知 reference_policy_version')
    if packet['brief']['scope'] != 'create' and policy['task_mode'] != 'local_edit':
        raise AgentError('编辑任务不能通过开放创作策略移除真实编辑目标')
    return {**policy, 'allow_without_baseline': policy['task_mode'] == 'open_creation'}


def validate_reference_policy(packet: dict, baseline_ids=None) -> dict:
    policy = read_reference_policy(packet)
    refs = packet.get('references', [])
    mode = policy['task_mode']
    if mode == 'local_edit':
        if not refs or packet['brief']['scope'] == 'create':
            raise AgentError('local_edit 需要真实编辑目标与编辑范围，不能作为纯文字创作')
        analysis = packet.get('reference_analysis') or {}
        by_id = {r['id']: r for r in refs}
        observed = {r['id']: r['sha256'] for r in analysis.get('input_images', [])}
        targets = [f['id'] for f in analysis.get('findings', [])
                   if f.get('seen') and f.get('useful') and f['id'] in by_id
                   and observed.get(f['id']) == by_id[f['id']]['sha256']
                   and (f.get('role_in_task') == 'canvas' or
                        (f.get('role_in_task') == 'identity' and by_id[f['id']]['role'] == 'identity'))
                   and generation_eligible(packet, by_id[f['id']])]
        if not targets:
            raise AgentError('needs_reference：local_edit 需要已观察的真实编辑目标；风格样图不能代替画布')
        policy.update(allow_without_baseline=True, edit_target_ids=targets)
    elif mode == 'reference_transfer':
        analysis = packet.get('reference_analysis') or {}
        from .host_references import observed_ids
        seen = observed_ids(packet)
        relevant = [f for f in analysis.get('findings', [])
                    if f.get('seen') and f.get('useful') and f.get('role_in_task') not in ('negative', 'reject')
                    and f['id'] in seen]
        if not relevant:
            raise AgentError('needs_reference：关系迁移需要本次实际观察的相关参考')
        # Observation-only references may guide a relation without upload.
        policy['allow_without_baseline'] = True
    if baseline_ids is not None and not baseline_ids and not policy['allow_without_baseline']:
        raise AgentError('needs_reference：缺少可用于生成的视觉基准；先补参考，不能自动开启探索')
    return policy


def needs_reference(packet: dict) -> bool:
    try:
        validate_reference_policy(packet, packet.get('visual_coverage', {}).get('positive_ids', []))
    except AgentError as exc:
        if str(exc).startswith('needs_reference：'):
            return True
        raise
    return False


def generation_eligible(packet: dict, ref: dict) -> bool:
    if ref.get('evidence_type') == 'host_attested' or ref.get('evaluation_only') or ref['role'] == 'aesthetic_negative':
        return False
    analysis = packet.get('reference_analysis') or {}
    finding = next((f for f in analysis.get('findings', []) if f['id'] == ref['id']), None)
    observed = {r['id']: r['sha256'] for r in analysis.get('input_images', [])}
    return bool(finding and finding['seen'] and finding['useful']
                and finding['role_in_task'] not in ('negative', 'reject')
                and observed.get(ref['id']) == ref['sha256'])


def reference_usage(packet: dict, baseline_ids, inputs=(), submission_evidence='not_submitted') -> dict:
    """Keep text retrieval, attachments, and submitted image bytes separate."""
    submitted = {r['id'] for r in inputs}
    from .host_references import export_context
    return {**export_context(packet),
        'retrieved_card_ids': [h['id'] for h in packet.get('aesthetic_retrieval', {}).get('hits', [])],
        'baseline_ids': list(baseline_ids),
        'attached_references': [
            {'id': r['id'], 'sha256': r['sha256'], 'source_sha256': r.get('original_sha256', r['sha256']),
             'role': r['role'], 'note': r['note'],
             'usage': 'evaluation_only' if r.get('evaluation_only') or r['role'] == 'aesthetic_negative' else 'generation',
             'state': 'generation_input' if r['id'] in submitted else 'reference_attached_not_submitted'}
            for r in packet['references']],
        'generation_inputs': [dict(r) for r in inputs],
        'submission_evidence': submission_evidence,
        'visual_influence': 'not_proven_by_input_manifest',
        'notice': '文字卡片命中、参考附件、实际输入分别记录；传入图片不证明已学到风格或达到个人审美。',
    }


def validate_generation_inputs(packet: dict, baseline_ids, inputs) -> dict:
    refs = {r['id']: r for r in packet['references']}
    policy = validate_reference_policy(packet, baseline_ids)
    ids = [r.get('id') for r in inputs]
    if len(ids) != len(set(ids)):
        raise AgentError('生成输入ID重复')
    for entry in inputs:
        ident = entry.get('id')
        if ident == 'PREVIOUS_CANDIDATE':
            if entry.get('role') not in ('edit_target', 'edit_canvas') or not entry.get('sha256'):
                raise AgentError('上一候选只可作为有摘要的编辑画布')
            continue  # Host/API execution validates the previous output separately.
        ref = refs.get(ident)
        if ref is None or not generation_eligible(packet, ref):
            raise AgentError('参考未看图、已失效、仅供评估或被排除，不能作为生成输入：' + str(ident))
        if entry.get('sha256') != ref['sha256'] or entry.get('role') != ref['role']:
            raise AgentError('生成输入与参考内容/用途不匹配：' + str(ident))
        if ref['rights'] != 'user_authorized':
            raise AgentError('生成输入尚未授权：' + str(ident))
    for ident in baseline_ids:
        if ident not in refs or ident not in ids or not generation_eligible(packet, refs[ident]):
            raise AgentError('reference_declared_not_adopted：基准图未进入实际生成输入：' + ident)
    if policy['task_mode'] == 'local_edit' and not set(policy['edit_target_ids']).intersection(ids):
        raise AgentError('local_edit 的真实编辑目标未进入实际输入，不能静默改成文字生图')
    return reference_usage(packet, baseline_ids, inputs, 'planned_not_submitted')
