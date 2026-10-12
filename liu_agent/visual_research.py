"""Task-specific visual study, separate from image-input admission.

This gate verifies provenance and decision use, not aesthetic quality or whether
the host really looked. No keyword classifier guesses user intent, no search or
upload is performed, and old packets are never migrated on read.
"""
from copy import deepcopy
from urllib.parse import urlsplit

from .contracts import VisualResearchAssessment
from .io import AgentError, digest
from .provenance import resolve_refs


def creative_context_enabled(packet):
    """Only new tasks opt into typed research and compilation evidence."""
    version = packet.get('creative_context_version')
    if 'creative_context_version' not in packet:
        return False
    if type(version) is not int or version != 1:
        raise AgentError('未知 creative_context_version；不能降级跳过创作证据')
    return True


def enabled(packet):
    version = packet.get('visual_research_version')
    if version is None:
        return False
    if type(version) is not int or version not in (1, 2):
        raise AgentError('未知 visual_research_version；不能静默跳过视觉研究')
    return True


def _validate_limited_exit_inputs(packet):
    """Research limits cannot replace missing inputs or grant execution rights.

    Check current inputs in status/design, not assessment: refresh assesses the
    new Need before rebuilding its source-bound reference policy. Attempts are
    host reports, not authenticated retrieval or quality evidence.
    """
    need, brief = packet.get('need', {}), packet.get('brief', {})
    targets = brief.get('targets')
    if (not _nonblank(need.get('intent')) or not isinstance(targets, list) or not targets
            or any(not _nonblank(target) for target in targets)
            or brief.get('scope') not in ('create', 'edit', 'color_only', 'relight', 'scene')
            or need.get('questions')
            or any(row.get('status') == 'open' for row in need.get('question_records', []))):
        raise AgentError('needs_input：limited_exit 不能替代目标、模型、身份、文字或范围的决定性输入；先解决已有未决问题')
    # Preserve required identity/transfer material and the actual edit parent.
    # The design/dispatch callers also retain their source, binding, permission
    # and budget checks; research disclosure cannot supersede any of them.
    from .reference_gate import validate_reference_policy
    validate_reference_policy(packet)


def assessment(packet):
    current_evidence = creative_context_enabled(packet)
    if not enabled(packet):
        return None
    raw = packet.get('need', {}).get('visual_research')
    if raw is None:
        return None
    try:
        value = VisualResearchAssessment.model_validate(raw)
    except ValueError as exc:
        raise AgentError('视觉研究判断无效；按本题原话重做 Need.visual_research') from exc
    if not value.reason.strip():
        raise AgentError('视觉研究判断需要本题的具体理由')
    expected = {'study': {'open_brief', 'explicit_research', 'rejected_direction'},
                'specified_direction': {'user_specified_target'},
                'bounded_edit': {'local_change'}, 'user_waived': {'user_prohibited_study'}}
    if value.trigger not in expected[value.route]:
        raise AgentError('明确研究要求或被拒绝的方向不能降为免研究路径')
    if value.route == 'bounded_edit' and packet.get('brief', {}).get('scope') == 'create':
        raise AgentError('开放创作不能冒充局部编辑以跳过研究')
    if value.route == 'study' and not value.visual_question.strip():
        raise AgentError('研究须指出本题要解决的视觉问题，不能只有风格标签')
    messages = resolve_refs(packet.get('input_sources'), value.source_refs)
    if any(row['actor'] != 'user' for row in messages):
        raise AgentError('研究判断须引用用户原话；宿主提案不等于用户指定方向')
    if (value.route != 'study' or value.trigger != 'open_brief') and not messages:
        raise AgentError('免研究或明确研究/否定方向的判断必须绑定真实用户来源')
    if current_evidence and value.route == 'study':
        if value.evidence_requirement is None:
            raise AgentError('needs_visual_research：说明本题需要反馈复盘、视觉参考还是专业作品分析，不能混称已研究')
        if value.evidence_requirement == 'feedback_review' and (
                value.trigger == 'open_brief' or not messages):
            raise AgentError('仅复盘已有图须有本题用户来源；未定方向的开放创作不能据此免除寻找视觉参考')
        if value.evidence_requirement == 'professional_analysis' and not messages:
            raise AgentError('专业分析职责须绑定本题用户来源，不能把宿主提案变成永久研究要求')
    if value.limited_exit is not None:
        if (value.route != 'study' or value.trigger != 'open_brief' or
                value.evidence_requirement not in (None, 'visual_reference')):
            raise AgentError('limited_exit 只适用于 study/open_brief 的开放视觉研究；不能绕过 explicit_research、rejected_direction 或 professional_analysis')
        from .workflow_policy import policy as workflow_policy
        selected = workflow_policy(packet)
        if selected is None or selected['version'] != 2:
            raise AgentError('limited_exit 仅限完整流程 policy v2；旧任务保留原研究门控，不自动迁移')
    return value


def validate_refresh(previous, current, sources, *, current_brief=None):
    """A saved explicit research requirement cannot disappear on refresh."""
    if not enabled(previous):
        return
    old = assessment(previous)
    new_packet = {**previous, 'need': current, 'input_sources': sources}
    if current_brief is not None:
        new_packet['brief'] = current_brief
    new = assessment(new_packet)
    if old and old.trigger in ('explicit_research', 'rejected_direction'):
        if new is None:
            raise AgentError('已记录的明确研究要求不能通过省略字段撤销')
        if new.route != 'study' or new.trigger != old.trigger:
            from .provenance import verified_sources
            old_ids = {row['id'] for row in verified_sources(previous.get('input_sources'))}
            if not any(ref.source_id not in old_ids for ref in new.source_refs):
                raise AgentError('撤销明确研究要求需要新的用户指令，不能复用旧原话改判')
    if old and old.route == 'study' and (
            creative_context_enabled(previous) or old.evidence_requirement is not None):
        if new is None:
            raise AgentError('已有研究职责不能通过省略字段撤销；变更须保留研究判断并引用新的用户指令')
        levels = {'feedback_review': 0, 'visual_reference': 1, 'professional_analysis': 2}
        weaker = (new.route != 'study' or
                  levels.get(new.evidence_requirement, -1) < levels.get(old.evidence_requirement, -1))
        if weaker:
            from .provenance import verified_sources
            old_ids = {row['id'] for row in verified_sources(previous.get('input_sources'))}
            if not any(ref.source_id not in old_ids for ref in new.source_refs):
                raise AgentError('已有研究职责不能降为旧图复盘；降低证据要求须引用新的用户指令')


def _https_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _source_evidence(packet, ref, finding, requirement):
    """Bind an observation to saved evidence; never authenticate expertise.

    Generated results remain valid feedback evidence. A professional claim also
    needs an observed work and an excerpt from a separately saved source record;
    declaring ``seen`` or typing an attractive interpretation is insufficient.
    """
    kind = finding.get('source_kind')
    if kind not in ('generated_candidate', 'user_reference', 'external_work'):
        return None
    proof = {'reference_id': ref['id'], 'source_kind': kind,
             'source_url': ref.get('source_url', ''), 'credit': ref.get('credit', '')}
    for key in ('sha256', 'metadata_digest'):
        if key in ref:
            proof[key] = ref[key]
    if requirement == 'feedback_review':
        return proof
    if kind == 'generated_candidate':
        return None
    if requirement == 'visual_reference' and kind == 'user_reference':
        return proof
    # An external work must be inspectable by its saved pixels and attributable
    # to an actual source, not only a host-attested conversation-image label.
    if (kind != 'external_work' or not ref.get('sha256') or
            not _https_url(ref.get('source_url')) or not _nonblank(ref.get('credit'))):
        return None
    if requirement == 'visual_reference':
        return proof
    if requirement != 'professional_analysis':
        return None
    url, excerpt = finding.get('professional_source_url'), finding.get('professional_excerpt')
    if not _https_url(url) or not _nonblank(excerpt):
        return None
    sources = (packet.get('research') or {}).get('sources', [])
    if not isinstance(sources, list):
        return None
    matches = [source for source in sources if isinstance(source, dict) and source.get('url') == url]
    if len(matches) != 1:
        return None
    source = matches[0]
    if (not all(_nonblank(source.get(key)) for key in ('title', 'author', 'excerpt')) or
            not isinstance(source.get('reference_ids'), list) or ref['id'] not in source['reference_ids'] or
            excerpt not in source['excerpt']):
        return None
    proof['professional_source'] = {
        'url': url, 'title': source['title'], 'author': source['author'],
        'excerpt': excerpt, 'source_document_digest': digest(source)}
    return proof


def evidence_context(packet):
    """Compact frozen source context for the compiler; no live retrieval."""
    if not creative_context_enabled(packet):
        return {}
    value = assessment(packet)
    if value is None or value.route != 'study':
        return {'version': 1, 'assessment': value.model_dump() if value else None, 'sources': []}
    from .host_references import research_references
    refs = {row['id']: row for row in research_references(packet)}
    findings = {row['id']: row for row in (packet.get('reference_analysis') or {}).get('findings', [])}
    sources = [_source_evidence(packet, refs[ident], findings[ident], value.evidence_requirement)
               for ident in observed_sources(packet)]
    return {'version': 1, 'assessment': value.model_dump(), 'sources': deepcopy(sources),
            'authority': 'saved_source_and_observation_binding_not_verified_expertise_or_quality'}


def observed_sources(packet):
    """Only task-bound, currently observed visual cases count as research.

    Identity/detail/edit canvas alone are not photography/style study. Sources
    marked evaluation_only are usable here without becoming generation assets.
    """
    from .host_references import research_references, observed_ids
    refs = {r['id']: r for r in research_references(packet)}
    analysis = packet.get('reference_analysis') or {}
    observed = observed_ids(packet)
    value = assessment(packet) if creative_context_enabled(packet) else None
    seen = []
    for finding in analysis.get('findings', []):
        ref = refs.get(finding['id'])
        if (ref and ref.get('role') not in ('identity', 'detail')
                and finding.get('role_in_task') not in ('identity', 'canvas')
                and finding.get('seen') and finding.get('visible_facts', '').strip()
                and ref['id'] in observed):
            if value and value.route == 'study' and _source_evidence(
                    packet, ref, finding, value.evidence_requirement) is None:
                continue
            seen.append(ref['id'])
    return seen


def status(packet):
    if not enabled(packet):
        return None
    value = assessment(packet)
    observed = observed_sources(packet)
    if value is None:
        state = 'needs_assessment'
    elif value.limited_exit is not None:
        _validate_limited_exit_inputs(packet)
        state = 'limited_with_disclosure'
    elif value.route != 'study':
        state = 'skipped_by_user' if value.route == 'user_waived' else 'not_required'
    elif packet['visual_research_version'] == 2 and observed:
        selection = packet.get('reference_analysis') or {}
        state = {'exploring': 'needs_reference_expansion',
                 'acceptable': 'observed_pending_decision',
                 'uncertain': 'needs_reference_selection',
                 'no_suitable_reference': 'needs_reference_selection'}.get(selection.get('selection_state'))
        if not state or not selection.get('selection_reason', '').strip():
            state = 'needs_selection_assessment'
        elif selection.get('selection_state') == 'acceptable' and not any(
                row.get('id') in observed and row.get('useful')
                and row.get('role_in_task') not in ('reject', 'negative')
                for row in selection.get('findings', [])):
            # The host itself rejected every seen case. Do not present that
            # contradictory declaration as a usable direction before Plan.
            state = 'needs_reference_selection'
    else:
        state = 'observed_pending_decision' if observed else 'needs_observation'
    result = {'version': packet['visual_research_version'], 'state': state,
            'assessment': value.model_dump() if value else None,
            'observed_reference_ids': observed,
            'authority': 'host_interpretation_and_observation_not_aesthetic_proof'}
    if packet['visual_research_version'] == 2:
        selection = packet.get('reference_analysis') or {}
        result.update(selection_state=selection.get('selection_state'),
                      selection_reason=selection.get('selection_reason', ''))
    if creative_context_enabled(packet):
        result['evidence_requirement'] = value.evidence_requirement if value else None
    if state == 'limited_with_disclosure':
        limitation = value.limited_exit.model_dump()
        reason = {'sources_unavailable': '来源不可访问',
                  'no_decision_gain': '继续研究无决策增量'}[limitation['reason']]
        disclosure = (f'视觉研究有限：{reason}。已尝试：' + '；'.join(limitation['attempts'])
                      + '。未解决：' + limitation['unresolved']
                      + '。首稿仅沿已有授权继续，不代表研究充分、审美已验证或新增生成许可。')
        result.update(limited_exit=limitation, disclosure=disclosure,
                      disclosure_required=True, research_sufficient=False)
    return result


def validate_design(packet, design):
    current = status(packet)
    if current is None:
        return None
    if current['state'] == 'needs_assessment':
        raise AgentError('needs_visual_research：先按真实原话填写 Need.visual_research；开放创作不自动免研究')
    if current['state'] in ('not_required', 'skipped_by_user'):
        return current
    limited = current['state'] == 'limited_with_disclosure'
    if not current['observed_reference_ids'] and not limited:
        if creative_context_enabled(packet):
            raise AgentError('needs_visual_research：实际观察尚未满足本题研究职责；旧AI图仅能复盘，专业分析须绑定作品像素、出处及已保存正文中的摘录')
        raise AgentError('needs_visual_research：先取得并实际观察本题视觉案例；身份图或文字检索不能替代')
    if current['state'] == 'needs_selection_assessment':
        raise AgentError('needs_selection_assessment：实际看图后在 ReferenceAnalysis 记录 selection_state 与本题 selection_reason，不能把已看过当成有合适方向')
    if current['state'] == 'needs_reference_expansion':
        raise AgentError('needs_reference_expansion：依据本题视觉问题继续找有实际关系差异的参考并观察；不凑固定数量或重复同类素材')
    if current['state'] == 'needs_reference_selection':
        raise AgentError('needs_reference_selection：方向仍拿不准或没有合适参考，进入 copilot 用已有 question_records 澄清取舍，不能强行生成')
    used = {use.reference_id for choice in design.choices for use in choice.reference_uses}
    connected = sorted(used.intersection(current['observed_reference_ids']))
    if not connected and (current['observed_reference_ids'] or not limited):
        raise AgentError('needs_visual_research：实际观察须改变一个设计决定，沿 reference_uses 连接可见关系、选择与检查；不适用可明确 reject 并说明原因')
    if limited:
        # Keep any real reference bindings, including reasoned rejections, but
        # never upgrade a disclosed shortfall to a sufficient selected direction.
        return {**current, 'decision_reference_ids': connected}
    if current['version'] == 2 and not any(
            use.disposition == 'adopt' and use.reference_id in connected
            for choice in design.choices for use in choice.reference_uses):
        raise AgentError('needs_reference_selection：全部舍弃的案例不能证明已有可用方向；继续找不同参考或进入 copilot 澄清，不得把拒绝证据当成审美选定')
    if current['version'] == 1 and current['assessment']['trigger'] in ('open_brief', 'rejected_direction'):
        selection = design.creative_directions
        if (selection is None or len(selection.drafts) != 2
                or any(d.proposal_kind != 'direction' for d in selection.drafts)
                or not selection.alternatives_reason.strip()):
            raise AgentError('needs_visual_research：开放题或被拒绝方向须比较两个不同创意方向并说明取舍；角度/色温变体不算另一方向，也无需生成两张图')
    # The caller validates each relation against actual observation and its
    # linked check. A connected source is evidence of use, never of quality.
    return {**current, 'state': 'decision_connected', 'decision_reference_ids': connected}


def host_instruction(packet):
    if not enabled(packet):
        return ''
    instruction = '''视觉研究与生成基准分开：open_creation 允许无身份图，不代表无需研究。
新任务在 Need.visual_research 明确 route/trigger/reason/visual_question/source_refs。
未定方向的开放题走 study/open_brief；明确要求研究或否定现有方向分别用 explicit_research/rejected_direction，保留真实用户引文。
只有用户已指定可执行视觉目标才用 specified_direction/user_specified_target；范围确定的局部修改才用 bounded_edit/local_change。两种免研究路径必须引用用户原话，不能把宿主自行补的场景当指定目标。
用户明确禁止研究时用 user_waived/user_prohibited_study 并引用原话，记录为 skipped_by_user，不能声称已研究或已验证审美收益。
study 使用真实案例：先解决具体视觉问题，实际看图、observe 后比较方向，说明选择和舍弃的依据，再选一个最终方向。搜索命中、文字卡片和身份图不等于完成审美研究。
把观察中的可见关系写入 choices[].reference_uses，连接同参考的检查项。未采用案例可用 disposition=reject 和 reason，说明它为何改变本题的选择；不强迫采用无关知识。仅供观察的作品保留 evaluation_only，不自动上传。
参考缺失、不可访问或没有帮助时如实报告缺口，继续研究或说明阻塞；不能删研究要求、补空字段或增加形容词冒充完成。
'''
    if packet['visual_research_version'] == 2:
        instruction += '''参考是随本题扩展的风格库，不是固定风格词菜单。先理解用途、想传达的感觉和真实限制，再带着视觉问题找案例；用实际形态、人物关系、空间、光色及表达效果筛选，不以来源名气代替判断。
研究目标明确与已有可执行视觉方案分别判断。direction_review=direct 只表示不需先追问；题材、风格词、作品名或气质线索不自动允许 specified_direction。若具体画面来自宿主提案，按开放创作研究，不能把内部预设方案或已冻结的实验设计当作用户指定证据。是否需要提问仍由本题的重要未知决定，不因出现作品名而机械追问。
在同一次 ReferenceAnalysis 中记录 selection_state 与 selection_reason：有具体可用关系时 acceptable；需要扩大差异化搜索时 exploring；探索后仍拿不准时 uncertain；没有合适案例时 no_suitable_reference。exploring 继续找不同真实案例并观察，不要求固定数量或固定两个草案；uncertain/no_suitable_reference 进入已有 copilot 问答澄清偏好或目标，再按回答研究。不能为了填 acceptable 而声称参考好看。
需求开始就模糊且会改变主导表达时，先用 question_records 问清关键目标再找参考。需要共创时只问本题关键分歧；已回答的选择与限制保留，不把搜索扩展当作重选许可。
有足够依据可直接形成一个最终方向；已由用户选定的共创方案只展开原选中草案。choices/reference_uses/checks 必须连接真正采用的观察；全部拒绝案例不能冒充找到了可用审美方向。代码只验证来源和决策连接，不证明美观或用户接受。
'''
    if packet.get('intake_policy_version') == 1:
        instruction += '''需求明确与视觉方案已定分开判断：direction_review=direct 只表示无需先问用户，不等于 specified_direction，也不免除开放题的参考研究。“做张咖啡海报”可以有明确目标，但未提供具体视觉方案时仍走 study；“风格你定”是允许宿主找依据作设计决定，不能反写成用户已指定场景、机位或光线。
研究前先解决会改变目标的未知；用户已经交出的实现选择由宿主承担。研究时寻找可借鉴的真实作品，实际观察它如何组织主体、空间、光色与阅读重点，再把适用关系迁移到本题。用户没有要求复刻时，不把完整身份、布局或元素组合照搬成新硬锁；无需为形式重新搜索已看过且适用的作品。
'''
    if creative_context_enabled(packet):
        instruction += '''本题使用 creative_context_version=1。在 Need.visual_research.evidence_requirement 区分 feedback_review（用户要求复盘已有图）、visual_reference（寻找可用视觉作品）和 professional_analysis（学习对具体作品的专业分析）；不要把旧AI图复盘替代后两种职责，已记录职责只有新的用户指令可以降低。
observe 时每项 finding 填 source_kind：generated_candidate、user_reference 或 external_work。外部作品复用参考的 source_url、credit 与真实像素；同一张作品可以继续用，不为形式重复搜索。专业分析还须通过 attach-research 保存 sources 中该作品的 url/title/author/excerpt/reference_ids；finding.professional_source_url 指向其中唯一来源，professional_excerpt 摘录该来源正文。图片必须实际看过，摘要或 seen=true 不能替代它；来源记录和引用绑定也不自动证明专业判断适用于本题。
'''
    if (packet.get('workflow_policy') or {}).get('version') == 2:
        instruction = instruction.replace(
            '参考缺失、不可访问或没有帮助时如实报告缺口，继续研究或说明阻塞；不能删研究要求、补空字段或增加形容词冒充完成。',
            '参考缺失先做必要研究；实际尝试后来源仍不可访问或持续无决策增量，可沿下面的研究受限出口，不能伪造完成。')
        instruction = instruction.replace(
            'uncertain/no_suitable_reference 进入已有 copilot 问答澄清偏好或目标，再按回答研究。',
            'uncertain/no_suitable_reference 中确有关键用户选择未决时才进入 copilot；需求已明确且研究受阻时可披露 limited_exit。')
        instruction += '''\n开放研究可按真实情况提供 limited_exit={reason:sources_unavailable或no_decision_gain, attempts:非空真实尝试列表, unresolved:未解决事项}。
仅study/open_brief的视觉参考研究可用；明确必做研究、被否决方向的再研究、专业分析及关键目标/身份/文字/父图/范围未决不能走此出口。
取得 limited_with_disclosure 后将 disclosure 实际展示，保留已有参考的采用/舍弃绑定，沿原授权和预算自主首稿；不把受限状态写成acceptable、研究充分或审美通过。\n'''
    return instruction
