"""Preserve revision intent and reconcile machine evidence, never score beauty."""
from __future__ import annotations

from .contracts import PairComparison
from .io import AgentError


def next_action(review: dict | None) -> dict | None:
    if not review or review.get('decision') != 'revise':
        return None
    labels = {'intent': '需求理解', 'reference': '参考作用', 'design': '设计依据',
              'compiler': '提示词编译', 'execution': '模型执行偏差', 'unknown': '缺失证据'}
    diagnosis = review['diagnosis']
    return {'command': None, 'return_to': diagnosis, 'action': 'review_failure_layer',
            'description': '先回到' + labels[diagnosis] + '核对：' + review['diagnosis_reason'] + '。此建议不增加生图额度。',
            'revision_targets': review.get('revision_targets', []), 'preserve': review.get('preserve', [])}


def revision_prompt(prompt: str, review: dict) -> str:
    """Both execution routes use the same already-reviewed changes and gains."""
    if review.get('changes'):
        prompt += '\n上一轮候选仅修订以下问题，保留其它已正确关系：' + '；'.join(review['changes'])
    if review.get('preserve'):
        prompt += '\n保留上一轮已正确内容：' + '；'.join(review['preserve'])
    return prompt


def select_pair(report: PairComparison, trials: list[dict]) -> tuple[int, dict]:
    """A is the retained candidate, B its revision; return advisory selection.

    Reported regressions are unresolved losses, not already accepted design
    tradeoffs. A model's positive verdict cannot erase conflicting evidence.
    Uncertainty retains A for inspection without claiming that A passes.
    """
    if len(trials) != 2:
        raise AgentError('成对比较需要恰好两张候选')
    if not report.evidence.strip():
        raise AgentError('选择须给具体可见依据，不用轮次判断好坏')
    if any(not item.strip() for item in report.regressions):
        raise AgentError('退步证据不能为空字符串；没有未解决退步时使用空列表')
    raw = report.model_dump()
    raw['reported_choice'] = report.choice
    # A confident per-image label is not evidence of a confident comparison.
    if report.choice == 'uncertain':
        raw['selection_reason'] = 'comparison_uncertain_keep_previous'
        return 0, raw

    index = 1 if report.choice == 'B' else 0
    ready = [t['review']['decision'] == 'ready_for_user' for t in trials]
    if ready[0] != ready[1]:
        index = 0 if ready[0] else 1
        raw['absolute_gate_override'] = True
        raw['choice'] = 'A' if index == 0 else 'B'

    before = {r['criterion_id']: r['verdict'] for r in trials[0]['review']['items']}
    after = {r['criterion_id']: r['verdict'] for r in trials[1]['review']['items']}
    lost = sorted(ident for ident, verdict in before.items()
                  if verdict == 'met' and after.get(ident) != 'met')
    if index == 1 and (report.regressions or lost):
        raw.update(choice='uncertain', selection_reason='revision_losses_need_resolution',
                   regressed_criterion_ids=lost)
        raw.pop('absolute_gate_override', None)
        return 0, raw
    raw['selection_reason'] = 'absolute_criteria' if raw.get('absolute_gate_override') else 'pair_evidence'
    return index, raw
