"""Bounded delivery choices over existing frozen evidence, never a new judge."""
from pathlib import Path

from .io import AgentError


def self_check_allowed(contract: dict, requirements: dict | None = None) -> bool:
    policy = contract.get('workflow_policy') or {}
    if policy.get('version') != 2 or policy.get('delivery_level') != 'exploratory':
        return False
    text = (requirements or {}).get('text', {})
    # Exact user-authored text is high-risk even in a nominal exploration.
    return not (text.get('applicability') == 'required'
                and text.get('source_kind', 'user_requirement') == 'user_requirement')


def require_review_context(contract: dict, review: dict, requirements: dict | None = None) -> None:
    context = (review.get('holistic') or {}).get('review_context')
    allowed = ('isolated', 'author_context') if self_check_allowed(contract, requirements) else ('isolated',)
    if context not in allowed:
        raise AgentError('当前交付需要独立隔离评审；自检或未知上下文不能冒充已完成')


def deviation_evidence(run: Path) -> dict:
    """Only an observed local authored difference can be accepted as-is.

    Hard requirements, goal judgments, file/OCR failures, unresolved requests
    and legacy research selection are not waivable through this exit.
    """
    from . import workflow_policy as flow
    from .visual_decision import record_only
    run = Path(run).resolve()
    packet, contract, _ = flow._task(run)
    if flow.policy(packet)['version'] != 2 or not record_only(packet):
        raise AgentError('带偏差接受仅用于新交付策略，旧任务保留原合同')
    candidates, pending = flow._candidates(run, packet, contract)
    if pending or len(candidates) != 1 or packet['brief'].get('model_comparison'):
        raise AgentError('带偏差接受须绑定唯一已完成候选，不能猜选或绕过在途请求')
    candidate = candidates[0]
    req = flow._requirements(run, packet, contract)
    checked = flow._bound_stage(run, 'checks', packet, contract, candidate, req)
    if flow._check_failures(checked['report'], req):
        raise AgentError('图片或必要文字检查未通过，不能作为非硬性偏差收下')
    reviewed = flow._bound_stage(run, 'reviews', packet, contract, candidate, req)
    review = reviewed['report']['review']
    require_review_context(contract, review, req)
    whole = review.get('holistic') or {}
    if (whole.get('image_access') != 'viewed' or not whole.get('judgments')
            or any(row['verdict'] != 'met' for row in whole['judgments'])):
        raise AgentError('目标级整体判断尚未成立，不能按作者局部偏差完成')
    if any(row['verdict'] != 'comparable' for row in review.get('baseline_comparisons', [])):
        raise AgentError('参考比较尚未成立，不能按作者局部偏差完成')
    criteria = {row['id']: row for row in contract['checks']}
    deviations = []
    for item in review['items']:
        if item['verdict'] == 'met':
            continue
        check = criteria[item['criterion_id']]
        if check['dimension'] in ('hard', 'overall', 'expression', 'narrative'):
            raise AgentError('用户硬要求或目标级标准未满足，不能由局部偏差出口豁免')
        if item['verdict'] != 'not_met':
            raise AgentError('偏差仍不确定，须先说明实际可见差异，不能推断已披露')
        deviations.append({'criterion_id': check['id'], 'question': check['question'],
                           'verdict': item['verdict'], 'candidate_evidence': item['candidate_evidence']})
    if not deviations:
        raise AgentError('没有已知的作者局部偏差；正常候选沿原交付路径')
    return {'format': 'hex.deviation-acceptance.v1', 'candidate_sha256': candidate['sha256'],
            'request_id': candidate['request_id'], 'packet_digest': packet['digest'],
            'contract_digest': contract['digest'], 'review_digest': reviewed['digest'],
            'check_digest': checked['digest'], 'deviations': deviations,
            'quality_standards_met': False, 'permission_expanded': False,
            'preference_promoted': False}


def require_disclosure(snapshot: dict, source_id: str, evidence: dict) -> None:
    """Bind the user's acceptance to an immediately addressed difference notice.

    This verifies recorded message relations/text, not platform identity or
    natural-language understanding. The host must actually display the notice.
    """
    rows = {row['id']: row for row in snapshot['sources']['messages']}
    answer = rows[source_id]
    notice = rows.get(answer.get('in_reply_to'))
    if (not notice or notice['actor'] != 'assistant'
            or notice['origin'] not in ('host_transcription', 'host_message_record')
            or any(row['candidate_evidence'] not in notice['text'] for row in evidence['deviations'])):
        raise AgentError('带偏差接受须回复已逐项说明实际差异的消息；无需照读内部检查题干，普通好评不能代替')


def accepted_deviation(run: Path, request_id: str) -> dict | None:
    from .feedback import _read_feedback
    run = Path(run)
    if not (run / 'user-feedback.json').is_file():
        return None
    # Validate only a declared deviation exit; ordinary feedback remains separate.
    from .io import read_json
    payload = read_json(run / 'user-feedback.json').get('feedback', {})
    if 'deviation_acceptance' not in payload:
        return None
    evidence = deviation_evidence(run)
    binding = {key: evidence[key] for key in ('candidate_sha256', 'packet_digest', 'contract_digest')}
    feedback = _read_feedback(run, binding)
    if (not feedback or feedback['decision'] != 'accepted_by_user'
            or feedback.get('deviation_acceptance') != evidence or evidence['request_id'] != request_id):
        raise AgentError('带偏差接受的候选、检查或用户选择已变化；不能沿用旧确认')
    from .feedback_sources import read_snapshot
    require_disclosure(read_snapshot(run), feedback['source_id'], evidence)
    return evidence
