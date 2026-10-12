"""Pure permission checks; generation allocation never implies v2 repair consent."""
from __future__ import annotations

from .io import AgentError


def _nonempty_text_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and item.strip() for item in value)


def require_image_permission(*, max_images: int, attempted_images: int,
                             review: dict | None = None, user_decision: str | None = None,
                             workflow_version: int | None = None) -> None:
    """Check an already-established user allowance and current reviewed decision.

    The caller supplies the actual allowance, including failure/unknown spending;
    this function never infers consent from the product cap. The authoritative
    ledger must separately stop pending/unknown requests and unevaluated results.
    A definite failure may have no review; another attempt can use only the
    remaining actual allowance, without resetting that failure's count.
    Text validation here cannot prove that proposed changes or preservation
    instructions are semantically specific; the evidence reviewer owns that.
    """
    if type(max_images) is not int or max_images not in (0, 1, 2):
        raise AgentError('实际生图授权必须是0至2次的整数；产品上限不是默认授权')
    if type(attempted_images) is not int or attempted_images < 0:
        raise AgentError('已尝试次数必须是非负整数，失败和未知调用同样计数')
    if attempted_images >= max_images:
        raise AgentError('实际授权次数已用完；失败和未知调用不能清零或补额度')
    if user_decision in ('accepted_by_user', 'rejected_by_user'):
        raise AgentError('用户已接受或否决当前方向；停止本版本生成，新需求须另建关联版本')
    if user_decision not in (None, 'not_recorded', 'not_verified'):
        raise AgentError('用户反馈状态未知，不能据此推断仍可生成')
    if workflow_version == 2 and attempted_images:
        raise AgentError('v2原生重试须走有独立用户返修授权的有界返修入口；剩余额度不是返修许可')
    if review is None:
        return
    if not isinstance(review, dict):
        raise AgentError('已有候选的复查记录结构无效')
    decision = review.get('decision')
    if decision != 'revise':
        raise AgentError(f'上一候选复查为{decision}；仅明确revise可以占用修订额度')
    if review.get('diagnosis') not in ('design', 'compiler', 'execution'):
        raise AgentError('修订须定位为design/compiler/execution；需求、参考或未知问题先回上游核实')
    items = review.get('items')
    if not isinstance(items, list) or any(not isinstance(item, dict) or
            item.get('verdict') not in ('met', 'not_met', 'uncertain') for item in items):
        raise AgentError('复查项目结构或结论无效，不能据此修订')
    whole_failed = False
    if review.get('holistic') is not None:
        from .contracts import HolisticReview
        try:
            whole = HolisticReview.model_validate(review['holistic'])
        except ValueError as exc:
            raise AgentError('整体复查结构无效，不能据此修订') from exc
        if ({row.dimension for row in whole.judgments} != {'overall', 'visual_coherence', 'expression'} or
                not whole.first_impression.strip() or any(not row.evidence.strip() for row in whole.judgments)):
            raise AgentError('整体复查维度或可见证据不完整，不能据此修订')
        if whole.image_access != 'viewed':
            raise AgentError('尚未实际看到候选，不能占用修订额度')
        whole_failed = any(row.verdict == 'not_met' for row in whole.judgments)
    if not any(item['verdict'] == 'not_met' for item in items) and not whole_failed:
        raise AgentError('修订至少须有一项已知not_met；仅uncertain不能直接重画')
    changes = review.get('changes')
    if not _nonempty_text_list(changes) or len(changes) > 2:
        raise AgentError('修订必须列出1至2条非空的具体changes')
    if not _nonempty_text_list(review.get('preserve')):
        raise AgentError('修订必须列出非空preserve，说明需要保留的已有关系')


def require_bounded_repair_permission(*, workflow_version: int, max_images: int,
                                      attempted_images: int, repair_grant: dict | None,
                                      repair_attempted: bool,
                                      user_decision: str | None = None) -> None:
    """Caller must first verify the grant's user source and measurable failure."""
    if type(workflow_version) is not int or workflow_version != 2:
        raise AgentError('有界自动返修只适用于 workflow policy v2；旧任务不迁移')
    if repair_attempted is not False:
        raise AgentError('单次返修已占用；失败、未知和中断均停止，不退回额度')
    if (not isinstance(repair_grant, dict) or set(repair_grant) != {'scope', 'max_repairs'}
            or repair_grant.get('scope') != 'downscale_to_requested_size'
            or type(repair_grant.get('max_repairs')) is not int or repair_grant['max_repairs'] != 1):
        raise AgentError('缺少独立的单次 repair_grant；generation_grant 和剩余额度都不是返修授权')
    require_image_permission(max_images=max_images, attempted_images=attempted_images,
                             user_decision=user_decision)
