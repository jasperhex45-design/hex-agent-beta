"""Opt-in interaction rules using the existing Need, sources and direction drafts.

These checks prove provenance/state, never that a proposal understands the goal.
There is no second decision ledger and no generation authority in this module.
"""
from copy import deepcopy

from .io import AgentError, digest

PROFILE = 'selective-co-creation-v1'
NATURAL_PROFILE = 'natural-co-creation-v1'


def policy(profile):
    if profile in (None, 'legacy'):
        return None
    if profile not in (PROFILE, NATURAL_PROFILE):
        raise AgentError('未知 interaction_profile')
    return {'version': 1, 'profile': profile}


def enabled(packet):
    if 'interaction_policy' not in packet:
        return False
    value = packet['interaction_policy']
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value not in (policy(PROFILE), policy(NATURAL_PROFILE))):
        raise AgentError('未知 interaction_policy；不能迁移或静默降级旧任务')
    return True


def natural(packet):
    return enabled(packet) and packet['interaction_policy']['profile'] == NATURAL_PROFILE


def copilot_enabled(packet):
    """New task contract only; do not reinterpret frozen v1/legacy decisions."""
    return packet.get('visual_research_version') == 2 and enabled(packet)


def intake_enabled(packet):
    """Version only the new intake behavior, without migrating saved tasks."""
    if 'intake_policy_version' not in packet:
        return False
    version = packet['intake_policy_version']
    if type(version) is not int or version != 1:
        raise AgentError('未知 intake_policy_version；不能静默跳过需求理解')
    return copilot_enabled(packet)


def initial_assessment_required(packet):
    """Missing host judgment is not itself a reason to question the user."""
    return copilot_enabled(packet) and packet['need'].get('direction_review') is None


def options_digest(record):
    return digest({'id': record.id, 'question': record.question,
                   'affected_decision': record.affected_decision,
                   'options': [row.model_dump() for row in record.options],
                   'recommended_id': record.recommended_id})


def validate_question(record, sources, *, selective=False):
    from .provenance import resolve_refs
    if selective and record.status == 'withdrawn' and record.decision_type == 'expression':
        messages = resolve_refs(sources, record.source_refs)
        if not messages or any(row['actor'] != 'user' for row in messages):
            raise AgentError('关键方向问题不可由Agent自行撤回；须关联用户解决或撤销来源')
    if not record.options:
        if record.selected_id or record.selection_source or record.answer_options_digest or record.recommended_id:
            raise AgentError('选项回答必须绑定实际方向草案')
        return
    ids = [row.id for row in record.options]
    if record.decision_type != 'expression' or len(ids) != len(set(ids)):
        raise AgentError('方向选项须为expression问题且选项ID唯一')
    if record.recommended_id not in ids:
        raise AgentError('推荐方向必须来自当前选项；推荐不等于选择')
    from itertools import combinations
    from .creative_directions import _normal
    modern_cards = any(row.purpose is not None or row.first_visual_focus is not None
                       or row.subject_relation is not None or row.proposal_kind == 'same_direction_variant'
                       for row in record.options)
    if modern_cards and len(record.options) > 2:
        raise AgentError('关键创作分歧最多展示两个方向；不强凑备选')
    for a, b in combinations(record.options, 2):
        if (a.proposal_kind == b.proposal_kind == 'direction'
                and all(_normal(getattr(a, axis)) == _normal(getattr(b, axis)) for axis in
                        ('viewer_relation', 'subject_state', 'spatial_organization'))):
            raise AgentError('备选须有实际画面关系差异，不能只换名称或冷暖')
    if record.status == 'answered':
        if record.selected_id not in ids or not record.selection_source:
            raise AgentError('方向回答必须记录实际选择及选择来源')
        if record.answer_options_digest != options_digest(record):
            raise AgentError('选项内容已变化，旧回答不能支持新选项')
        if record.selection_source == 'user_delegated' and not record.delegation_scope.strip():
            raise AgentError('委托选择须记录本任务的委托范围')
    elif record.selected_id or record.selection_source or record.answer_options_digest:
        raise AgentError('待选或已撤销问题不能预填推荐作为用户选择')


def validate_state(packet):
    current_intake = intake_enabled(packet)
    if not enabled(packet):
        return
    from .contracts import Need
    from .clarification import _validate_record
    from .provenance import resolve_refs, verified_sources
    need = Need.model_validate(packet['need'])
    sources = packet.get('input_sources')
    source_order = {row['id']: i for i, row in enumerate(verified_sources(sources))}

    def later_user_refs(refs, old):
        messages = resolve_refs(sources, refs)
        prior = max((source_order[r.source_id] for r in old.source_refs), default=-1)
        return bool(messages) and all(m['actor'] == 'user' for m in messages) and any(
            source_order[m['id']] > prior for m in messages)

    rows = {q.id: q for q in need.question_records}
    if len(rows) != len(need.question_records):
        raise AgentError('方向问题ID重复')
    for q in rows.values():
        _validate_record(q, sources)
        validate_question(q, sources, selective=True)
        if q.supersedes:
            old = rows.get(q.supersedes)
            if old is None or old.id == q.id or old.status == 'open':
                raise AgentError('改选须先用用户来源关闭旧问题，保留原回答并关联新问题')
            if not later_user_refs(q.source_refs, old):
                raise AgentError('改选必须绑定晚于旧回答的新增用户来源，不能重用原目标或Agent提案')
    review = need.direction_review
    if review is None:
        superseded = {q.supersedes for q in rows.values() if q.supersedes}
        if natural(packet) and any(q.options and q.status in ('open', 'answered')
                                   and q.id not in superseded for q in rows.values()):
            raise AgentError('已有方向提案或选择须保留方向回查；不能用自然执行绕过选中项')
        return
    messages = resolve_refs(sources, review.source_refs)
    if not review.reason.strip() or not messages or any(m['actor'] != 'user' for m in messages):
        raise AgentError('方向回查须保留本任务用户目标来源及具体理由')
    if current_intake:
        # A natural answer is evidence to understand, not an already authored
        # direction. A fresh assessment must cite that answer (or a subsequent
        # user instruction); restoring the old review cannot absorb the reply.
        answered_at = [source_order[ref.source_id]
                       for q in rows.values() if q.status == 'answered' and not q.options
                       for ref in q.source_refs]
        if answered_at and not any(source_order[m['id']] >= max(answered_at) for m in messages):
            raise AgentError('自然澄清后的需求回查须引用最新回答或后续用户指令；先吸收回答更新 Need，不重复提问')
    if review.route == 'direct':
        if review.basis == 'unresolved_tradeoff' or review.question_id is not None:
            raise AgentError('未决重要取舍不能标为直接执行')
        superseded = {q.supersedes for q in rows.values() if q.supersedes}
        active = [q for q in rows.values() if q.status == 'answered' and q.options and q.id not in superseded]
        for q in active:
            if review.basis == 'bounded_autonomy' or not later_user_refs(review.source_refs, q):
                raise AgentError('已有用户选择不能改成自主直接执行；须有新增用户变更或委托来源')
    else:
        q = rows.get(review.question_id)
        if (review.basis != 'unresolved_tradeoff' or q is None
                or (not copilot_enabled(packet) and q.decision_type != 'expression')):
            raise AgentError('需共创的方向必须关联稳定的expression问题')
        superseded = {r.supersedes for r in rows.values() if r.supersedes}
        if any(r.status == 'answered' and r.options and r.id not in superseded and r.id != q.id
               for r in rows.values()):
            raise AgentError('已有用户选择必须通过带新来源的继任问题改选')


def blockers(packet):
    """Do not trust the convenience questions[] list to be the sole gate."""
    if not enabled(packet):
        return list(packet.get('need', {}).get('questions', []))
    validate_state(packet)
    need = packet['need']
    result = list(need.get('questions', []))
    for q in need.get('question_records', []):
        if q['status'] == 'open' and q['question'] not in result:
            result.append(q['question'])
    review = need.get('direction_review')
    if review is None and (not natural(packet) or copilot_enabled(packet)):
        result.append('先回查目标与主导构图，记录直接执行依据或准备关键取舍')
    elif review and review['route'] == 'ask':
        q = next(q for q in need['question_records'] if q['id'] == review['question_id'])
        if q['status'] != 'answered' or ((q.get('options') or not copilot_enabled(packet)) and not q.get('selected_id')):
            result.append('关键问题尚未回答；不能代答' if copilot_enabled(packet) and not q.get('options')
                          else '关键方向尚未选择；空回答不采用推荐')
    return list(dict.fromkeys(result))


def early_blockers(packet):
    if not enabled(packet):
        return blockers(packet)
    rows = packet['need'].get('question_records', [])
    if copilot_enabled(packet):
        # A question about the intended expression can be an intake question,
        # not an instruction to invent pictured alternatives before asking.
        return list(dict.fromkeys(list(packet['need'].get('questions', [])) + [
            q['question'] for q in rows if q['status'] == 'open']))
    expression = {q['question'] for q in rows if q['decision_type'] == 'expression'}
    return [q for q in packet['need'].get('questions', []) if q not in expression] + [
        q['question'] for q in rows if q['status'] == 'open' and q['decision_type'] != 'expression']


def needs_assessment(packet):
    if not enabled(packet) or early_blockers(packet):
        return False
    review = packet['need'].get('direction_review')
    if review is None:
        return not natural(packet) or copilot_enabled(packet)
    if review['route'] == 'ask':
        q = next((q for q in packet['need'].get('question_records', [])
                  if q['id'] == review['question_id']), {})
        return q.get('status') == 'open' and not q.get('options')
    return False


def decision_digest(packet):
    data = {key: packet.get(key) for key in (
        'interaction_policy', 'brief', 'need', 'input_sources', 'references',
        'reference_policy', 'reference_analysis')}
    if packet.get('creative_context_version') == 1:
        from .user_feedback_context import frozen_context
        data.update(creative_context_version=1, research=packet.get('research'),
                    user_feedback_context=frozen_context(packet))
    if 'host_visible_references' in packet:
        from .host_references import validate_context
        validate_context(packet)
        data['host_visible_references'] = packet['host_visible_references']
    return digest(data)


def chosen_question(packet):
    review = packet['need'].get('direction_review') or {}
    if review.get('route') != 'ask':
        return None
    return next((q for q in packet['need'].get('question_records', [])
                 if q['id'] == review.get('question_id')), None)


def observation_context(need):
    core = {k: v for k, v in need.items() if k not in ('questions', 'question_records', 'direction_review')}
    answers = [q for q in need.get('question_records', []) if q['status'] != 'open']
    return {'need': core, 'answers': answers}


def validate_assessment(packet, need):
    previous = observation_context(packet['need'])
    current = observation_context(need.model_dump())
    if previous != current:
        # DIRECTION also performs the new intake's missing host interpretation.
        # It must be able to absorb a bound natural answer into Need before the
        # normal refresh checks provenance, scope, locks and question history.
        # An established direction proposal still cannot rewrite its own goal,
        # and even initial interpretation cannot rewrite a recorded answer.
        interpreting = intake_enabled(packet) and initial_assessment_required(packet)
        if not interpreting or previous['answers'] != current['answers']:
            raise AgentError('方向提案阶段不可暗改原目标或代答问题；先按真实来源修正需求')
    from .creative_directions import _verify_evidence
    for q in need.question_records:
        if q.status == 'open':
            for option in q.options:
                for evidence in option.reference_evidence:
                    _verify_evidence(packet, evidence)


def validate_new_options(packet, need):
    if not enabled(packet):
        return
    from .creative_directions import _verify_evidence
    old = {q['id']: q for q in packet.get('need', {}).get('question_records', [])}
    for q in need.question_records:
        if q.options and not old.get(q.id, {}).get('options'):
            if observation_context(packet.get('need', {})) != observation_context(need.model_dump()) and any(
                    option.reference_evidence for option in q.options):
                raise AgentError('目标或决定已变化；先观察新目标下的参考，再登记有参考依据的提案')
            for option in q.options:
                for evidence in option.reference_evidence:
                    _verify_evidence(packet, evidence)


def registered_selection_evidence(packet, evidence):
    """An immutable answered proposal retains its originally checked quote.

    The caller still checks the current image hash and fresh applicability.
    A re-observation need not repeat the old wording verbatim.
    """
    if not enabled(packet):
        return False
    validate_state(packet)
    q = chosen_question(packet)
    if not q or q['status'] != 'answered' or not q.get('options'):
        return False
    selected = next(row for row in q['options'] if row['id'] == q['selected_id'])
    return evidence.model_dump() in selected['reference_evidence']


def selected_meaning(packet):
    if not enabled(packet):
        return None
    q = chosen_question(packet)
    if not q or q['status'] != 'answered' or not q.get('options'):
        return None
    option = next(row for row in q['options'] if row['id'] == q['selected_id'])
    # Author reasoning, expected effects and all alternatives stay withheld.
    visible = {k: option[k] for k in ('viewer_relation', 'subject_state',
               'spatial_organization', 'dominant_decision', 'supporting_decisions')}
    for key in ('first_visual_focus', 'subject_relation'):
        if option.get(key) is not None:
            visible[key] = option[key]
    result = {'question_id': q['id'], 'selected_id': option['id'],
            'selection_source': q['selection_source'], 'source_refs': q['source_refs'],
            'delegation_scope': q.get('delegation_scope', ''),
            'visible_content': visible,
            'answer_options_digest': q['answer_options_digest'],
            'notice': '方向选择不等于成图接受；委托不形成用户长期偏好'}
    for key in ('proposal_kind', 'base_direction_id', 'variation_axes'):
        if key in option:
            result[key] = option[key]
    return result


def validate_plan(packet, design):
    if not enabled(packet):
        return
    pending = blockers(packet)
    if pending:
        raise AgentError('needs_input：' + '；'.join(pending))
    passive_direct = natural(packet) and not copilot_enabled(packet) and packet['need'].get('direction_review') is None
    if ((not passive_direct or design.decision_context_digest is not None)
            and design.decision_context_digest != decision_digest(packet)):
        raise AgentError('方向/需求/来源已变化；旧Plan失效，须回查并重新设计')
    q = chosen_question(packet)
    if q and q.get('options'):
        selection = design.creative_directions
        if selection is None or selection.question_id != q['id'] or selection.selected_id != q['selected_id']:
            raise AgentError('最终Plan必须绑定实际已选问题与方向，不得省略或替换')
        chosen = next(row for row in q['options'] if row['id'] == q['selected_id'])
        if len(selection.drafts) != 1 or selection.drafts[0].model_dump() != chosen:
            raise AgentError('最终Plan只展开原已选草案，不能混入备选或改写选项含义')
    elif design.creative_directions and design.creative_directions.question_id:
        raise AgentError('直接执行不能伪造用户方向选择')


def compiler_projection(payload, packet):
    """Remove alternatives at every nested Need/intake/Design input path."""
    if not enabled(packet):
        return payload
    result = deepcopy(payload)
    if result.get('input_sources'):
        sources = result['input_sources']
        result['input_sources'] = {
            'messages': [row for row in sources.get('messages', []) if row['actor'] == 'user'],
            'original_sources_digest': sources.get('digest'),
            'authority': 'projection_user_messages_only_not_a_new_source_ledger'}
    meaning = selected_meaning(packet)
    result['selected_decision'] = meaning
    for key in ('need', 'intake'):
        if key in result:
            result[key].pop('question_records', None)
            result[key].pop('adaptive_questions', None)
            result[key].pop('next_question', None)
            result[key].pop('direction_options', None)
            result[key].pop('direction_cards', None)
            if key == 'need':
                result[key].pop('direction_review', None)
    if result.get('method_context'):
        result['method_context'].get('selection_basis', {}).pop('direction_review', None)
    if 'design' in result and result['design'].get('creative_directions'):
        row = result['design']['creative_directions']
        row['drafts'] = [d for d in row['drafts'] if d['id'] == row['selected_id']]
        row.pop('alternatives_reason', None)
        row.pop('selection_reason', None)
    return result


def intake_projection(intake, packet):
    if not enabled(packet):
        return intake
    result = deepcopy(intake)
    result['interaction_policy'] = packet['interaction_policy']
    result['question_policy']['blocking_topics'] = list(dict.fromkeys(
        result['question_policy']['blocking_topics'] + ['unresolved_important_direction']))
    result['question_policy']['autonomy_scope'] = 'current_explicit_direction_and_task_delegation'
    result['adaptive_questions'] = blockers(packet)
    if copilot_enabled(packet) and initial_assessment_required(packet):
        result['adaptive_questions'] = early_blockers(packet)
    result['next_question'] = next(iter(result['adaptive_questions']), None)
    result['status'] = 'needs_input' if result['adaptive_questions'] else 'ready_for_reference_review'
    if initial_assessment_required(packet) and not result['adaptive_questions']:
        result['status'] = 'needs_intent_assessment'
    q = chosen_question(packet)
    if not q or q['status'] != 'open' or not q.get('options'):
        q = None
    result['direction_options'] = q
    result['direction_cards'] = []
    result.pop('options_digest', None)
    if q:
        from .contracts import QuestionRecord
        from .creative_directions import direction_card
        result['options_digest'] = options_digest(QuestionRecord.model_validate(q))
        result['next_question'] = q['question']
        purpose = (packet['need'].get('understanding') or {}).get('purpose', '')
        result['direction_cards'] = [direction_card(row, task_purpose=purpose) for row in q['options']]
    result['interaction_surface'] = 'host_conversation_or_co_creation_show'
    return result


def render(packet):
    intake = intake_projection(packet['intake'], packet)
    lines = ['本次目标：' + packet['need']['intent']]
    q = intake.get('direction_options')
    if q:
        lines.append(q['question'])
        for option, card in zip(q['options'], intake['direction_cards']):
            kind = '同方向变体' if card['proposal_kind'] == 'same_direction_variant' else '创作方向'
            lines.append(f"{card['id']}{'（推荐）' if card['id'] == q['recommended_id'] else ''} · {kind}")
            lines.append('用途：' + card['purpose'])
            lines.append('第一眼看：' + card['first_visual_focus'])
            lines.append('主体状态：' + card['subject_state'])
            lines.append('人物／物体／环境关系：' + card['subject_relation'])
            lines.append('观看关系：' + card['viewer_relation'])
            lines.append('画面安排：' + card['spatial_organization'])
            lines.append('适合之处：' + card['expected_effect'] + '。取舍：' + card['tradeoff'])
            if card['proposal_kind'] == 'same_direction_variant':
                labels = {'camera_angle': '角度', 'color_temperature': '色温', 'focal_length': '焦段'}
                lines.append('原方向：' + card['base_direction_id'] + '；变化：'
                             + '、'.join(labels[axis] for axis in card['variation_axes']))
            if option['supporting_decisions']:
                lines.append('配合做法：' + '；'.join(option['supporting_decisions']))
        lines.append('这些是待核查的提案；文字字段差异不证明实际画面差异或成图质量。')
        lines.append('可选择、用自然语言调整、说都不接近，或明确委托本任务由 Hex 决定。')
    elif intake['next_question']:
        lines.append(intake['next_question'])
    elif intake['status'] == 'needs_intent_assessment':
        lines.append('宿主先依据原话判断研究目标是否明确；缺判断不等于需要用户补填信息。')
    else:
        lines.append('当前问题已解决，按已确认需求继续研究与设计。' if copilot_enabled(packet)
                     else '方向已明确，可以继续当前单一方案。')
    return '\n'.join(lines)


def answer(run, question_id, sources_file, source_id, expected_options_digest=None,
           selected_id=None, mode='user_selected', delegation_scope='', *, kb=None, state=None):
    """Bind a real reply to the existing question, with option binding if any."""
    from .contracts import Need, QuestionRecord, SourceRef
    from .host import dispatch_lock
    from .io import ROOT
    from .provenance import merge_sources, verified_sources
    from .workflow import read_packet, refresh_packet, ensure_references_mutable
    if mode not in ('user_selected', 'user_explicit', 'user_delegated', 'rejected'):
        raise AgentError('未知回答类型')
    with dispatch_lock(run):
        packet = read_packet(run)
        if not enabled(packet):
            raise AgentError('此入口仅用于显式选择性共创候选')
        ensure_references_mutable(run)
        current = chosen_question(packet)
        if not current or current['id'] != question_id:
            raise AgentError('回答必须绑定当前展示的方向问题；请读取当前提案')
        need = Need.model_validate(packet['need'])
        row = next((q for q in need.question_records if q.id == question_id), None)
        if row is None or row.status != 'open' or (not row.options and not copilot_enabled(packet)):
            raise AgentError('回答需要当前待选的问题与已展示选项')
        if row.options and options_digest(row) != expected_options_digest:
            raise AgentError('显示的选项已变化；请读取当前提案后再绑定回答')
        if not row.options and (expected_options_digest is not None or selected_id is not None):
            raise AgentError('自然语言澄清没有方向选项，不能伪造选择或选项摘要')
        sources = merge_sources(packet.get('input_sources'), sources_file)
        source = next((r for r in verified_sources(sources) if r['id'] == source_id), None)
        if not source or source['actor'] != 'user' or not source['text'].strip():
            raise AgentError('空回答或非用户来源保持待选，不默认选推荐')
        data = row.model_dump()
        data['source_refs'] = [SourceRef(source_id=source_id, quote=source['text']).model_dump()]
        if mode == 'rejected':
            if selected_id is not None:
                raise AgentError('都不接近/撤销不能同时登记选择')
            data.update(status='withdrawn', reason='用户拒绝本组选项或撤销相关范围；按原话重新理解')
            need.direction_review = None
        elif row.options:
            data.update(status='answered', selected_id=selected_id, selection_source=mode,
                        answer_options_digest=expected_options_digest, delegation_scope=delegation_scope)
        else:
            data.update(status='answered', reason='用户已提供本题澄清；按原话更新理解与研究问题，不推断选项或长期偏好')
            if intake_enabled(packet):
                # This command binds the reply; it cannot semantically rewrite
                # Need or the research question. Require host interpretation
                # before research/design, while preserving the answered record.
                need.direction_review = None
        updated = QuestionRecord.model_validate(data)
        validate_question(updated, sources, selective=True)
        need.question_records = [updated if q.id == question_id else q for q in need.question_records]
        need.questions = [q.question for q in need.question_records if q.status == 'open']
        return refresh_packet(packet, run, kb or ROOT/'knowledge', state or ROOT/'.state',
                              need, 'host_supplied', sources_file=sources_file)


def main(argv=None):
    import argparse
    from pathlib import Path
    from .workflow import read_packet
    parser = argparse.ArgumentParser(description='展示方向提案并绑定真实回答；不生成图片')
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('show')
    show.add_argument('--run', type=Path, required=True)
    reply = sub.add_parser('answer')
    reply.add_argument('--run', type=Path, required=True)
    reply.add_argument('--question-id', required=True)
    reply.add_argument('--sources', type=Path, required=True)
    reply.add_argument('--source-id', required=True)
    reply.add_argument('--options-digest')
    reply.add_argument('--selected-id')
    reply.add_argument('--mode', choices=['user_selected', 'user_explicit', 'user_delegated', 'rejected'], default='user_selected')
    reply.add_argument('--delegation-scope', default='')
    args = parser.parse_args(argv)
    try:
        if args.command == 'show':
            packet = read_packet(args.run)
        else:
            packet = answer(args.run, args.question_id, args.sources, args.source_id,
                            args.options_digest, args.selected_id, args.mode, args.delegation_scope)
        print(render(packet))
        return 0
    except (AgentError, ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


INSTRUCTION = '''
交互 natural-co-creation-v1 / selective-co-creation-v1：先理解准确，再按需共创；下面规则覆盖默认自主选定方向的措辞。
自然入口的明确需求直接进入单一设计，不要求先填方向回查，不为缺少direction_review增加追问或额外调用；需要共创时显式登记ask与问题，已有提案或已选方向继续原来源、摘要与冻结约束，不得省略后恢复自主选定。显式selective候选继续原强制回查流程。
PARSE保留用户观看目标、主次、程度、局部/整体与允许范围。“允许裁切”不等于必须裁切；HEX选择不写成用户must_keep。已有原话先用，不重复问技术缺省。
OBSERVE只依据实际可见图像，记录事实→作用解释→本题迁移→不迁移/未知；逐项回指参考。身份图背景、风格图姿态不自动成为要求。必要图不可用先恢复，可选参考可不采用；文件存在或seen不能证明理解正确。
在现有设计阶段回查第一个主导决定：观者先读什么、什么可见关系支撑它、依据是用户/已选/实见参考/假设、是否以形式整齐替换目标、即使物件齐全会怎样失败。沿choices.action/cooperation/tradeoff与expression/overall checks记录，不追加评分服务。
Need.direction_review记录direct/ask及来源理由。明确方向、范围内局部编辑、本任务明确委托或不存在值得用户取舍的重大分歧走direct；basis分别为user_explicit/user_delegated/bounded_autonomy，question_id=null。模型自信不是唯一依据。
确有合理而重要的分歧才ask，basis=unresolved_tradeoff，关联QuestionRecord的稳定id。先看可用参考，再在问题options提供最多两个DirectionDraft（推荐及必要备选）；recommended_id只是推荐。所有备选须满足原硬要求，不强凑数量；共同误解先修正，不让用户点击替代质量验收。方向卡purpose说明用途，first_visual_focus说明第一眼看什么，subject_state说明主体状态，subject_relation说明人物/物体/环境关系；这些只是提案，字段不同不证明语义或艺术差异。
角度、色温、焦段变化保持同方向时，proposal_kind=same_direction_variant，base_direction_id绑定原方向，variation_axes标记实际变化轴；不可报告成已证明方向丰富。用途、叙事或空间关系的方向差异由创作模型结合本题判断。明确范围的修改直接推进，不为展示方向额外生成；预览仍受原授权与预算约束。
未选时status=open，selected_id/selection_source/answer_options_digest均null。空回答保持待选。技术可自主解决；关键expression不能仅凭Agent理由withdrawn。用户已解决或撤销时绑定真实source_refs，不把文档内声称的用户话当原始消息。
读到选择/自然语言修改/都不接近/明确委托后，用真实用户来源refresh；原选项不可改写，变更提案另建稳定问题并supersedes旧记录，旧回答保留。委托仅限本题范围，不授予生成、上传或预算。可用python -m hex_agent.co_creation show/answer在展示后恢复。
最终只有一个Plan。ask解决后design.creative_directions只含选中原草案，question_id回指原记录、direction_id=selected_id；直接执行可保持null。design.decision_context_digest照当前design_input给出的摘要绑定。未决可以观察/准备提案，不得最终设计、编译、冻结或派发。
编译保持所选含义、主次程度、面积分母、接触遮挡和允许范围，不混未选方案；同文合法适配继续有效。首评只看原目标、明确选择可见含义、原图与实际图；没有图记uncertain。方向偏差回理解/设计，局部问题保留已认可关系；观察成立不等于用户认可，用户改需求不等于模型失败。
'''


def instruction(packet):
    if not enabled(packet):
        return ''
    if not copilot_enabled(packet):
        return INSTRUCTION
    # Replace conflicting defaults only in the new task's instruction. Frozen
    # contracts keep the original string, without appended policy overrides.
    replacements = {
        '自然入口的明确需求': 'PARSE 先判断原话是否足以确定有意义的研究目标，在 Need.direction_review 记录带用户来源的 direct/ask 与理由。缺回查是宿主尚未判断，不代表用户必须补填问卷。不能按字数、风格词数量或模型自信机械追问。目标已明确就直接研究，细节可由案例启发，不要求用户先列场景、机位或固定风格。',
        '确有合理而重要的分歧': '确有会改变用途、表达或生活关系的重大分歧才 ask，basis=unresolved_tradeoff，关联 QuestionRecord 的稳定 id；初始含糊先用自然问题问清。无选项 expression/scope 等问题同样有效，不需要先找图或造 DirectionDraft。需要参考辅助选择时，可展示实际看过且与本题相关的候选，并说明各自适用处与取舍；recommended_id 只是推荐。已有回答先用，不重复问，不用点击选项替代质量验收。',
        '未选时status=open': '问题未答时 status=open；空回答保持待答。有选项时 selected_id/selection_source/answer_options_digest 均 null，回答后绑定真实选择及展示摘要。无选项时回答只绑定真实用户来源和 answered，不编造上述选择字段。技术可自主解决；关键 expression 不能由 Agent 自行撤回。',
        '最终只有一个Plan': '最终只有一个 Plan。有选项 ask 解决后 design.creative_directions 只含选中原草案，question_id 回指原记录、direction_id=selected_id；无选项澄清或直接执行可保持 null。design.decision_context_digest 绑定当前输入摘要。存在 open 问题先向用户提问，不让研究覆盖待答状态，不得最终设计、编译、冻结或派发。',
    }
    text = '\n'.join(next((replacement for prefix, replacement in replacements.items()
                           if line.startswith(prefix)), line) for line in INSTRUCTION.split('\n'))
    text += '''
初始澄清解决后，按需求和审美判断找真实案例；参考形成动态风格候选，不把有限标签当风格全集。拿不准时补找不同关系的案例。候选均不适合、重要偏好仍未决或用户认为结果不好看时，启动有针对性的 Copilot 问答；不是自动重抽或机械要求两个草案。问题可直接问，也可展示有实际观察依据的候选。
得到回答后把含义落实到 Need 与研究问题；是否已理解仍由宿主判断，不由字段存在证明。只把真正需要用户决定的问题交给用户；研究评估缺失由宿主继续完成。
'''
    if intake_enabled(packet):
        text += '''
本题 intake_policy_version=1。先从本轮原话、已有上下文和实际素材理解用户真正想做什么，区分已确定目标、明确委托的选择和仍影响方向的未知。图、海报、视频等媒介不是目标；只有“我要做一个图”且没有可用上下文或明确委托时，先问一个能确定主题、对象或用途的关键问题，再带着目标找参考。已给出的目标足够时直接推进，不按清单补问用途、受众、风格或技术参数，也不把未提供的可选信息当阻塞。
沉默不是委托。“用户没说”不能写成 user_delegated；该依据须在 source_refs 引用真实委托，并在 reason 说明本题交出的选择范围。bounded_autonomy 用于已知目标内的可自主实现，不用来编造用户做图的目的。用户明确委托主题或表达也可自主研究；只有仍会改变真实目标、身份或范围的问题才交给用户，每轮只问一个，不强制提供选项。
目标明确不等于视觉方案齐全。direction_review=direct 只表示不需先问用户；开放创作仍按当前视觉问题研究并实际看图，不能据此改为 specified_direction 免研究。用户已给可执行视觉方案、范围明确的局部编辑或明确不要研究时，沿实际研究策略处理，不把研究当固定仪式。
无选项的自然答复登记后会回到 needs_intent_assessment，这是宿主吸收已有回答的步骤，不是再次提问。保留 answered 记录及真实来源，先更新 Need 的目标、主体、理解和研究问题，再写引用该回答或后续用户指令的 direction_review；仅登记 answered 或复制旧判断不等于已理解。仍有其它关键未知时保留队列，只问当前最关键的一项，不重复已解决的问题。
'''
    return text


if __name__ == '__main__':
    raise SystemExit(main())
