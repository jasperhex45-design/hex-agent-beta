"""Task-goal review, with author explanations withheld from the visual call.

These guards establish coverage and input isolation, not aesthetic truth. A
host's declaration that it viewed an image is not independently authenticated.
"""
from copy import deepcopy
from .io import AgentError


def task_evaluation_card(packet: dict, contract: dict, refs: list) -> dict:
    """Derive a review aid from frozen checks, never a second task contract.

    Do not include Plan explanations, reference observations, prior reviews or
    machine-diagnosed failures. ``failure_evidence`` is the frozen prospective
    failure condition, not a claim that the candidate already failed it.
    """
    from .io import digest
    version(contract)
    if contract.get('digest') != digest({k: v for k, v in contract.items() if k != 'digest'}):
        raise AgentError('评价卡需要完整且未变化的冻结验收合同')
    roles = {ref['id']: ref.get('role', 'unknown') for ref in refs}
    roles.update({ref['id']: 'host_attested_research_pixels_unavailable'
                  for ref in packet.get('host_visible_references', [])})
    items = []
    ids = set()
    for check in contract['checks']:
        ident = check['id']
        if ident in ids:
            raise AgentError('冻结检查ID不能重复')
        ids.add(ident)
        items.append({
            'criterion_id': ident,
            'dimension': check['dimension'],
            'inspect': check['question'],
            'expected_relation': check['expected'],
            'failure_evidence': check['failure'],
            'references': [{'id': rid, 'role': roles.get(rid, 'unavailable')}
                           for rid in check['reference_ids']],
            'uncertain_when': ['候选或必要参考未实际可见', '相关区域被遮挡或分辨率不足',
                               '现有证据不能判断这项关系；不能用相似度替代身份或语义'],
            'source': {'contract_digest': contract['digest'], 'check_id': ident},
        })
    return {
        'format': 'hex.task-evaluation-card.v1',
        'authority': 'derived_from_frozen_checks_not_a_new_contract',
        'contract_digest': contract['digest'],
        'task': {key: packet['brief'][key] for key in ('domain', 'scope', 'medium')
                 if key in packet['brief']},
        'review_order': ['overall', 'visual_coherence', 'expression', 'task_checks'],
        'items': items,
        'rules': ['按当前媒介与允许风格判断；不以统一写实、低饱和或平均均衡为目标',
                  '没有可见证据用uncertain；机器建议不表示用户接受，不增加返修许可'],
    }


def version(data: dict) -> int | None:
    value = data.get('quality_contract_version')
    if value is not None and (type(value) is not int or value != 1):
        raise AgentError('未知quality_contract_version；不能降级或迁移冻结任务')
    return value


def validate_adoption(packet, spec, design) -> None:
    if version(packet) is None:
        return
    available = {hit['card']['id'] for hit in packet['retrieval']['hits']}
    used = {ident for choice in design.choices for ident in choice.core_knowledge_ids}
    selected = set(spec.selected_knowledge_ids)
    if used != selected or not used.issubset(available):
        raise AgentError('采用的core知识须与具体choice引用完全一致，且来自本次命中')
    if available and not selected and not (design.knowledge_skip_reason or '').strip():
        raise AgentError('可以零采用；请说明本轮命中知识为何不适用，不为凑ID强采')


def validate_holistic(contract, evaluation) -> None:
    if version(contract) is None:
        return
    whole = evaluation.holistic
    if whole is None:
        raise AgentError('新任务须先记录整体观感、视觉可信与表达；局部全met不能代替整体复查')
    if evaluation.diagnosis in ('compiler', 'execution'):
        raise AgentError('首次隔离评审没有原prompt和方案，不能确定compiler/execution归因；应标unknown并后续查证')
    dimensions = [item.dimension for item in whole.judgments]
    if sorted(dimensions) != ['expression', 'overall', 'visual_coherence']:
        raise AgentError('整体复查须各覆盖overall、visual_coherence、expression一次')
    if not whole.first_impression.strip() or any(not item.evidence.strip() for item in whole.judgments):
        raise AgentError('整体复查必须有非空的观感与可见证据')
    if whole.image_access != 'viewed' and evaluation.decision != 'uncertain':
        raise AgentError('未实际看到图像只能记录uncertain，不能建议就绪或定向返修')
    if evaluation.decision == 'ready_for_user':
        if whole.review_context != 'isolated':
            raise AgentError('作者同上下文或未知上下文的自评不能建议视觉就绪')
        if any(item.verdict != 'met' for item in whole.judgments):
            raise AgentError('整体观感、视觉可信或表达存在失败/不确定，不能建议视觉就绪')


def holistic_failures(evaluation) -> set[str]:
    if evaluation.holistic is None:
        return set()
    return {'goal.' + row.dimension for row in evaluation.holistic.judgments
            if row.verdict == 'not_met'}


def goal_first_payload(packet: dict, contract: dict, refs: list, candidate_sha: str,
                       evidence: dict | None) -> dict:
    """Withhold solution/prompt/previous reviews; label the authored task summary."""
    version(contract)
    brief = packet['brief']
    sources = packet.get('input_sources') or {}
    messages = [{key: row[key] for key in ('id', 'actor', 'text', 'origin') if key in row}
                for row in sources.get('messages', []) if row.get('actor') == 'user']
    visible_contract = {key: deepcopy(contract[key]) for key in
                        ('format', 'digest', 'request', 'request_summary', 'medium',
                         'checks', 'baseline_reference_ids', 'reference_images',
                         'source_contract_version', 'quality_contract_version')
                        if key in contract}
    from .host_references import review_context
    host_context = review_context(packet)
    # References retain their declared role, never prior observations or ratings.
    image_list = [{key: ref[key] for key in ('id', 'role', 'file', 'sha256') if key in ref}
                  for ref in refs]
    return {
        **host_context,
        'review_mode': 'goal_first_isolated',
        'brief': {key: brief[key] for key in ('title', 'request', 'domain', 'scope', 'medium') if key in brief},
        'original_request': contract['request'],
        **({'selected_decision': deepcopy(contract.get('selected_decision'))}
           if contract.get('interaction_policy') else {}),
        'input_sources': {'messages': messages},
        'goal': {'task_summary': brief['request'],
                 'summary_authority': 'host_summary_not_user_verbatim',
                 'criteria_authority': 'frozen_task_checks_not_user_endorsement'},
        'contract': visible_contract,
        'images_in_order': image_list + [{'id': 'CANDIDATE', 'role': 'candidate', 'sha256': candidate_sha}],
        'generation_evidence_binding': {
            'digest': evidence.get('digest') if evidence else None,
            'candidate_sha256': candidate_sha,
            'contract_digest': contract['digest'],
            'notice': '仅绑定原调用；作者方案与实参不向首次视觉评审提供。'},
        'notice': '在隔离上下文实际看图。先整体后局部，按任务媒介判断；不把戏剧性或设计当成不真实。未读原实参不能确定归因为compiler/execution。',
    }
