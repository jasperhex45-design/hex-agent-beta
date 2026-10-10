"""Read-only, evidence-bound language shared by the host, CLI and reports.

This projection is not a new workflow and never authorizes or executes a call.
Historical result files remain evidence, not the authority for current claims.
"""
from pathlib import Path

from .io import AgentError, read_json, sha, within


STATUS_LABELS = {
    'prompt_only_not_tested': '提示词已准备 · 尚未生成候选图',
    'needs_revision': '未达到本轮标准 · 需要修改',
    'evidence_insufficient': '证据不足 · 暂不能判断',
    'review_incomplete': '已取得候选 · 复查未完成',
    'image_generated_waiting_user': '首图已返回 · 等待用户判断',
    'exploratory_candidate_unvalidated': '探索候选 · 尚无视觉基准',
    'candidate_for_user_review': '机器建议候选 · 个人审美未验收',
    'storyboard_frame_for_user_review': '分镜静帧待确认 · 连续动作未测试',
    'exploratory_without_baseline': '探索方案 · 缺少视觉基准',
    'candidate_ready_for_user': '候选供用户判断',
    'candidate_observed_not_human_accepted': '候选复查意见已记录 · 待用户判断',
    'candidate_needs_revision': '候选仍有待修改项',
    'candidate_not_accepted': '候选未达到当前标准',
    'candidate_uncertain': '候选证据不足 · 暂不能判断',
    'candidate_selection_uncertain': '两版取舍尚不确定',
    'completed_user_confirmed': '已记录用户选择保留这一版',
    'user_rejected': '用户已否决这一版 · 待重新诊断',
    'generation_failed': '生成尝试失败 · 尚未取得候选图',
    'generation_pending': '请求已准备 · 尚未确认服务接收',
    'generation_outcome_unknown': '上次生成结果尚不确定',
    'generation_result_incomplete': '已记录生成返回 · 候选文件尚待核对',
    'generation_evidence_incomplete': '生成记录不完整 · 待核对',
    'needs_input': '有一个关键问题待确认',
    'needs_intent_assessment': '正在判断需求与研究目标',
    'needs_visual_research': '需要查找并观察本题参考',
    'needs_selection_assessment': '需要判断参考是否适合本题',
    'needs_reference_expansion': '继续寻找不同的参考方向',
    'needs_reference_selection': '需要结合参考进行针对性共创',
    'needs_reference': '尚缺本次视觉参考',
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, '当前状态尚待核对')


def _optional(run, name, expected=dict):
    path = run/name
    if not path.is_file():
        return expected()
    value = read_json(path)
    if not isinstance(value, expected) or (expected is list and any(not isinstance(v, dict) for v in value)):
        raise AgentError(f'{name} 结构无效')
    return value


def _image(run, record):
    return bool(record.get('file') and record.get('sha256') and
                sha(within(run, record['file']).read_bytes()) == record['sha256'])


def _review_evidence(run, packet, contract, trial):
    from .workflow import validate_trial
    validate_trial(packet, run, contract, trial)
    if not trial.get('review'):
        return 'not_performed', []
    if trial.get('origin') == 'host_reported_not_api_verified':
        from .host import validate_trial_dispatch
        validate_trial_dispatch(packet, run, trial)
        stored = _optional(run, 'host-image-tests.json', list)
        if not any(t == trial for t in stored):
            raise AgentError('呈现的复查意见与当前候选登记不一致')
        return 'host_reported', ['host-image-tests.json', trial['file'], 'acceptance.json']
    if trial.get('origin') == 'api_image_bytes' and (run/'api-state.json').exists():
        from .api_recovery import _evidence, load_api_state
        state = load_api_state(run)
        # Validate the actual evaluation snapshot, image bytes and returned opinion.
        _, _, verified, _, _ = _evidence(run, state)
        bound = next((t for t in verified if t['round'] == trial['round']), None)
        if bound is None or any(bound.get(key) != trial.get(key) for key in
                                ('sha256', 'review', 'solution', 'generation_inputs', 'eval_input_images')):
            raise AgentError('呈现的复查意见与 API 返回不一致')
        row = next((r for r in state['requests'] if r['phase'] == 'evaluate'
                    and r['round'] == trial['round'] and r['state'] == 'completed'), None)
        if row:
            return 'api_recorded', ['api-state.json', row['snapshot'], row['output']['file'], trial['file']]
    return 'unverified', []


def private_context(packet: dict, design: dict) -> list[dict]:
    """Expose selected task context, never scan or promote a private collection."""
    profile = packet.get('private_style_profile') or {}
    if profile.get('format') != 'liu.private-aesthetic-context.v1':
        return []
    selections = {s['rule_id']: s for s in profile.get('selection', [])}
    labels = {'confirmed_positive': '样本有明确认可；喜欢原因仍是解释',
              'supported_interest': '有兴趣记录支持；当前适用性待判断',
              'experimental': '实验性解释；尚待验证'}
    rows = []
    for rule in profile.get('rules', []):
        selection = selections.get(rule['id'], {})
        references = selection.get('references', [])
        source_ids = selection.get('matched_evidence_ids', [])
        if not source_ids or not references:
            continue
        ref_ids = {r['reference_id'] for r in references}
        choices = [c['id'] for c in design.get('choices', [])
                   if any(u['reference_id'] in ref_ids for u in c.get('reference_uses', []))]
        rows.append({'rule_id': rule['id'], 'direction': rule['direction'],
                     'source_ids': source_ids, 'references': references, 'related_choices': choices,
                     'confidence': rule['confidence'], 'confidence_text': labels.get(rule['confidence'], '解释依据待核对'),
                     'scope': 'current_task_advisory', 'adoption': 'not_proven_by_retrieval',
                     'notice': '只解释本轮候选来源；当前要求优先，不自动形成长期规则。'})
    return rows


def diagnosis_context(review: dict, contract: dict) -> list[dict]:
    criteria = {c['id']: c for c in contract.get('checks', [])}
    items = {i['criterion_id']: i for i in review.get('items', [])}
    rows = []
    for target in review.get('revision_targets', [])[:2]:
        item = items.get(target['criterion_id'], {})
        criterion = criteria.get(target['criterion_id'], {})
        if item.get('verdict') != 'not_met' or not criterion:
            continue
        rows.append({'criterion_id': target['criterion_id'],
                     'observed': item.get('candidate_evidence', ''),
                     'reference_evidence': item.get('reference_evidence', ''),
                     'criterion': criterion.get('question', ''),
                     'success_condition': criterion.get('expected', ''),
                     'action': target.get('change', target.get('action', '')),
                     'preserve': review.get('preserve', []),
                     'impact': '由宿主依据可见现象说明；现有结构记录不独立证明因果解释'})
    return rows


def build_presentation(run: Path, result: dict | None = None, inspection: dict | None = None) -> dict:
    """Project only current local facts; failed verification reduces claim strength."""
    run = Path(run).resolve()
    errors = []
    claims = []

    def claim(kind, refs):
        claims.append({'kind': kind, 'evidence_refs': list(dict.fromkeys(refs))})

    packet = {}
    try:
        if (run/'packet.json').is_file():
            from .workflow import read_packet
            packet = read_packet(run)
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    try:
        current = result if result is not None else _optional(run, 'result.json')
        if not isinstance(current, dict):
            raise AgentError('导出结果结构无效')
        if current and (not packet or current.get('packet_digest') != packet.get('digest')):
            current = {}
            errors.append('旧导出不属于当前需求版本')
        trials = (current.get('trials', []) if result is not None else
                  _optional(run, 'host-image-tests.json', list) or _optional(run, 'image-tests.json', list))
        if not isinstance(trials, list) or any(not isinstance(t, dict) for t in trials):
            raise AgentError('候选记录结构无效')
        if not trials and result is None and packet and (run/'api-state.json').is_file():
            from .api_recovery import _evidence, load_api_state
            _, _, trials, _, _ = _evidence(run, load_api_state(run))
        if not packet:
            trials = []
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        current, trials = {}, []
        errors.append(str(exc))
    images = []
    try:
        for trial in trials:
            contract = _optional(run, 'acceptance.json')
            if (trial.get('solution', {}).get('packet_digest') == packet.get('digest')
                    and trial.get('contract_digest') == contract.get('digest') and _image(run, trial)):
                images.append(trial)
        # A returned image may precede the registration of its evaluation record.
        from .host import read_dispatches
        attempts = read_dispatches(run)
        for attempt in attempts:
            if attempt['status'] == 'completed' and packet and attempt['request']['packet_digest'] == packet['digest']:
                from .host import validate_receipt
                validate_receipt(run, attempt)
                output = attempt['output']
                if _image(run, output) and not any(i['sha256'] == output['sha256'] for i in images):
                    images.append(output)
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    try:
        from .execution_state import execution_facts
        facts = execution_facts(run, trials=trials)
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        facts = {'generation_status': 'evidence_incomplete', 'known_generation_attempts': []}
        errors.append(str(exc))
    execution = facts['generation_status']
    attempt_refs = [ref for row in facts['known_generation_attempts'] for ref in row.get('evidence_refs', [])]
    execution_text = {
        'not_attempted': '尚未记录生成尝试，目前没有已核对的候选图。',
        'pending': '请求已准备，尚未确认服务接收；先核对这次请求的实际结果。',
        'outcome_unknown': '上次生成结果尚不确定；先找回原请求，不重复提交。',
        'failed': '生成尝试失败，尚未取得候选图；失败记录仍保留。',
        'completed': '已有生成返回记录，候选文件仍待核对。',
        'evidence_incomplete': '生成记录不完整，目前不能确认执行结果。',
    }.get(execution, '生成状态尚待核对。')
    if execution in ('failed', 'pending', 'outcome_unknown'):
        claim('generation_' + execution, attempt_refs)
    if images:
        count_text = f'已核对 {len(images)} 张候选图文件。'
        execution_text = (execution_text.replace('尚未取得候选图；', '') + count_text
                          if execution in ('pending', 'outcome_unknown', 'failed') else count_text)
        claim('candidate_available', [r['file'] for r in images])
    failed = [r for r in facts['known_generation_attempts'] if r['status'] == 'failed']
    if images and failed:
        execution_text += f'本轮另有 {len(failed)} 次生成失败，已有候选仍保留。'
        if execution != 'failed':
            claim('generation_failed', [ref for r in failed for ref in r['evidence_refs']])
    selected = next((t for t in trials if t.get('round') == current.get('selected_trial_round')), None)
    try:
        if len(trials) == 2 and (run/'selection.json').is_file():
            from .run_status import _selection
            selection = _selection(run, trials)
            selected = trials[selection['selected_index']]
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        selected = None
        errors.append(str(exc))
    if selected is None and len(trials) == 1:
        selected = trials[0]
    review_state, review_refs = 'not_performed', []
    try:
        if selected and any(i['sha256'] == selected['sha256'] for i in images):
            contract = _optional(run, 'acceptance.json')
            review_state, review_refs = _review_evidence(run, packet, contract, selected)
        elif not selected and len(trials) == 2 and len(images) == 2:
            evidence = [_review_evidence(run, packet, _optional(run, 'acceptance.json'), t) for t in trials]
            if all(refs for _, refs in evidence):
                review_state = 'selection_pending'
                review_refs = [ref for _, refs in evidence for ref in refs]
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        review_state = 'unverified'
        errors.append(str(exc))
    review_text = {
        'not_performed': '尚未完成这版候选的视觉复查。',
        'host_reported': '复查意见与图片已绑定；实际看图由宿主报告，尚未独立核实。',
        'api_recorded': '已核对这版图片的 API 复查输入和返回意见；机器判断仍需用户判断。',
        'unverified': '复查证据不足，目前不能确认这版的视觉检查结论。',
        'selection_pending': '两张候选均有绑定的复查意见；尚待比较选择，意见的正确性仍需判断。',
    }[review_state]
    if review_refs:
        claim('review_' + review_state, review_refs)
    review = (selected or {}).get('review') or {}
    machine = review.get('decision') if review_refs else None
    from .visual_decision import record_only
    production = record_only(packet)
    if machine == 'ready_for_user':
        review_text += '机器建议交给你判断。'
    elif machine == 'revise':
        review_text += '机器认为仍有项目需要修改。'
    elif machine == 'uncertain':
        review_text += '证据不足，机器暂不能判断。'
    if production:
        review_text = ('机器研究意见已另行记录，不参与验收、排序或补图。' if review_refs else
                       '机器评审为可选研究记录；当前图片直接交由你判断。' if images else
                       '尚无候选图可供视觉判断；机器评审尚未进行。')
        if images and packet.get('workflow_policy'):
            try:
                from .workflow_policy import registered_review
                completed_ids = [row['id'] for row in facts['known_generation_attempts']
                                 if row['status'] == 'completed']
                records = [registered_review(run, request_id) for request_id in completed_ids]
                review_text = ('当前候选的完整流程视觉复核已登记；具体未满足项、交付检查和用户选择分别保留。'
                               if records and all(record is not None for record in records) else
                               '本题的视觉复核是必要步骤，当前尚未完成全部候选的复核登记；出图不代表流程已完成。')
            except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
                review_text = '本题的视觉复核是必要步骤；当前复核证据尚未核验，不能声称已完成。'
                errors.append('完整流程视觉复核未核验：' + str(exc))
    user_state, source_level = 'not_recorded', 'not_recorded'
    try:
        if (run/'user-feedback.json').is_file():
            from .feedback import verified_feedback
            _, chosen, feedback = verified_feedback(run)
            matches = (selected and chosen['sha256'] == selected['sha256'] if current else
                       len(images) == 1 and images[0]['sha256'] == chosen['sha256'])
            if matches:
                user_state = feedback['decision']
                source_level = feedback.get('source_evidence', 'legacy_host_report_unverified')
                claim('user_' + user_state, ['user-feedback.json', chosen['file']])
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        user_state = 'unverified'
        errors.append(str(exc))
    positive = None
    try:
        if current and (run/'candidate-feedback.json').is_file():
            from .candidate_feedback import verified_candidate_feedback, projection
            receipt = verified_candidate_feedback(run)
            positive = projection(receipt)
            claim('candidate_positive_feedback', ['candidate-feedback.json',
                  *[t['file'] for t in trials if t.get('sha256') == receipt['binding']['candidate_sha256']]])
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append('正向反馈未核验：' + str(exc))
    actions = None
    try:
        if current and (run/'candidate-actions.json').is_file():
            from .candidate_actions import inspect as inspect_actions
            actions = inspect_actions(run)
            claim('candidate_actions', ['candidate-actions.json',
                  *[t['file'] for t in trials if t.get('sha256') == actions['binding']['candidate_sha256']]])
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append('候选动作未核验：' + str(exc))
    relative = None
    try:
        if current and (run/'relative-preference.json').is_file():
            from .feedback import verified_relative_preference

            receipt = verified_relative_preference(run)
            relative = {'binding': receipt['binding'], 'messages': receipt['messages'],
                        'scope': 'current_run_comparison'}
            claim('relative_preference', ['relative-preference.json',
                  *[t['file'] for t in trials if t.get('request_id') in
                    (receipt['binding']['preferred_request_id'], receipt['binding']['compared_request_id'])]])
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append('相对偏好未核验：' + str(exc))
    user_text = {
        'not_recorded': '尚未记录你对这一版的选择。',
        'accepted_by_user': '已记录你选择保留这一版；认可范围仅为这张图，任务整体验收未另行确认。',
        'rejected_by_user': '已记录你否决这一版，先对照画面与参考重新诊断。',
        'unverified': '用户反馈与当前版本的关联尚待核对。',
    }[user_state]
    if positive:
        recorded = '已记录你对这一版的正向评价。原话：' + '；'.join(
            '“' + message['quote'] + '”' for message in positive['messages'])
        user_text = (recorded if user_state == 'not_recorded' else user_text + ' ' + recorded)
    if production and len(images) == 2 and packet['brief'].get('model_comparison') and user_state == 'not_recorded':
        user_text = '尚未记录你对两张图的接受或拒绝；相对偏好可以作为下一步目标，不能当作最终接受。'
    if relative:
        preferred = next((i + 1 for i, t in enumerate(trials)
                          if t.get('request_id') == relative['binding']['preferred_request_id']), None)
        user_text += f' 已记录本轮相对偏好：最新为第{preferred}张更接近。原话：' + '；'.join(
            f'第{next(i + 1 for i, t in enumerate(trials) if t.get("request_id") == message["preferred_request_id"])}张：“{message["quote"]}”'
            for message in relative['messages'])
    if user_state in ('accepted_by_user', 'rejected_by_user') and source_level == 'legacy_host_report_unverified':
        user_text += '现有反馈为宿主记录，尚无独立消息快照。'
    retained = [name for name in ('solution.json', 'result.html', 'result.md') if (run/name).is_file()]
    # Presence is deliberately weaker than complete, consistent delivery.
    delivery = ('report_prepared' if result is not None else 'files_present'
                if 'result.html' in retained and 'result.md' in retained else 'not_exported')
    delivery_text = {'report_prepared': '本报告已整理已有成果；视觉检查与用户选择分别保留。',
                     'files_present': '报告文件已保存，交付文件存在不代表视觉目标已完成。',
                     'not_exported': '当前尚未保存完整的交付报告。'}[delivery]
    if retained:
        claim('files_retained', retained)
    intake, need = packet.get('intake', {}), packet.get('need', {})
    question = intake.get('next_question')
    active = None
    if question:
        record = next((q for q in need.get('question_records', []) if q.get('question') == question), {})
        active = {'question': question, 'id': record.get('id'), 'affected_decision': record.get('affected_decision', '')}
    stage = (inspection or {}).get('stage')
    next_action = {
        'needs_parse': '先整理当前需求与已有素材。',
        'needs_reference': '先补齐本次需要的参考图，再判断生成条件。',
        'needs_observation': '接下来实际查看当前参考，确认可采用的关系。',
        'needs_design': '接下来形成有依据的视觉方案。',
        'needs_compile': '接下来把已选方案整理成生成指令。',
        'needs_authorization': '方案可先保留；生成前需要本次调用的明确许可。',
        'needs_eval': '接下来对照参考检查已有候选图。',
        'needs_compare': '接下来比较两版的收益和损失，再决定保留哪版。',
        'ready_to_export': '接下来保存已有成果和复查范围。',
    }.get(stage, '接下来核对已有材料与当前任务的下一步。')
    if images:
        next_action = ('接下来对照参考检查已有候选图。' if review_state in ('not_performed', 'unverified')
                       else '请判断是否保留这一版，或指出最需要改的地方。')
    if machine == 'revise':
        changes = '；'.join(review.get('changes', [])[:2])
        next_action = ('先核对本轮修改方案：' + changes + '。保留已有候选；执行前核对修改依据与剩余许可。'
                       if changes else '先核对已知失败项与修改依据，再决定如何修订。')
    elif machine == 'uncertain':
        next_action = '先核实复查中尚不确定的内容，不自动重新生成。'
    if review_state == 'selection_pending' or stage == 'needs_compare':
        next_action = '接下来比较两版的收益和损失，再决定保留哪版。'
    elif stage == 'ready_to_export':
        next_action = '接下来保存已有成果和复查范围。'
    if production and not images and current.get('status') == 'prompt_only_not_tested':
        next_action = '当前只导出提示词，尚无候选图；可在本任务许可与预算有效时继续执行，不会自动生成。'
        if inspection and inspection.get('stage') == 'exported':
            next_action = inspection['next_action']['description']
    if production and images:
        next_action = '请查看原图，直接告诉 Hex Agent 接受或拒绝这一版；原因可以不说明。'
        if len(images) == 2 and packet['brief'].get('model_comparison'):
            next_action = '请直接说哪张更接近、哪里还需调整；也可以逐张表示接受或拒绝。'
    if user_state == 'accepted_by_user':
        next_action = '保留这版；继续修改前需核对新的修改意图、可用路线和剩余许可。'
    elif user_state == 'rejected_by_user':
        next_action = '先依据实际画面定位偏差，保留旧图和原反馈；修改方案不自动触发生图。'
    elif relative and production and len(images) == 2:
        next_action = '已记录本轮相对偏好和理由；如需调整，请说明新目标。不会自动补图或视为最终接受。'
    elif positive:
        next_action = '沿当前候选和已有反馈继续；修改前明确要改的部分，并保留已成立的部分。'
    if execution in ('pending', 'outcome_unknown'):
        next_action = '先核对原请求的结果，保持现有次数记录，不重复提交。'
    elif execution == 'failed' and not images:
        next_action = '先核对失败原因和剩余许可；已有方案可保留，再次生成需要有效许可。'
    elif images and facts['known_generation_attempts'] and facts['known_generation_attempts'][-1]['status'] == 'failed':
        next_action = '先核对后续生成的失败原因和剩余许可，保留已有候选；不会自动重试。'
    if active:
        next_action = '现在只需确认：' + active['question']
    workflow_policy = packet.get('workflow_policy')
    if workflow_policy:
        if (workflow_policy['deliverable_kind'] == 'prompt_only'
                and current.get('workflow_completion', {}).get('required_stages_verified')):
            next_action = '提示词已完成本题交付。后续如需生成图片，按新的成图需求建立相应任务。'
            review_text = '本题只交付提示词，图片检查与视觉复核不适用。'
        elif inspection and inspection.get('workflow_completion', {}).get('applicable'):
            description = inspection.get('next_action', {}).get('description')
            if not inspection['workflow_completion'].get('complete') and description:
                next_action = description
    if errors or stage == 'blocked':
        next_action = '先核对当前记录中缺失或不一致的证据，再继续；这次检查没有重发请求。'
    facets = dict(execution=execution_text, review=review_text, user_decision=user_text, delivery=delivery_text)
    summary = [execution_text, user_text] if production else [execution_text, review_text, user_text]
    if errors:
        summary.append('部分证据尚未核对成功，上述已核实文件不代表整条流程通过。')
    if current.get('producer', '').startswith('authored_demo'):
        summary.insert(0, '这是作者编写的安装演示，没有调用模型生成图片。')
    presentation = {
        'version': 'liu.presentation.v1', 'packet_digest': packet.get('digest'),
        'state': {'execution': execution, 'review': review_state, 'machine_decision': machine,
                  'user_decision': user_state, 'feedback_source': source_level, 'delivery': delivery,
                  'task_acceptance': 'not_recorded'},
        'facets': facets, 'summary': summary, 'claims': claims, 'active_question': active,
        'next_action': next_action, 'evidence_refs': sorted({r for c in claims for r in c['evidence_refs']}),
        'relative_preference': relative,
        'candidate_feedback': positive,
        'candidate_actions': actions,
        'understanding': {'goal': need.get('intent'), 'confirmed_constraints': need.get('must_keep', []),
                          'assumptions': need.get('assumptions', []), 'assumptions_status': 'provisional_not_user_confirmed'},
        'private_context': private_context(packet, current.get('design', {})),
        'diagnosis': diagnosis_context(review, _optional(run, 'acceptance.json')) if review_refs and not production else [],
        'diagnostic_details': errors,
    }
    validate_message_claims(presentation)
    return presentation


def validate_message_claims(presentation: dict) -> None:
    """Protect the structural semantics; this is not an NLP truth detector."""
    state = presentation['state']
    kinds = {c['kind'] for c in presentation['claims']}
    for claim in presentation['claims']:
        if not claim['evidence_refs']:
            raise AgentError('执行或用户选择的陈述缺少证据引用')
    if any(k.startswith('review_') for k in kinds) and 'candidate_available' not in kinds:
        raise AgentError('无候选图时不能声称完成看图复查')
    if state['user_decision'] == 'accepted_by_user' and 'user_accepted_by_user' not in kinds:
        raise AgentError('机器判断不能推导用户接受')
    if state['task_acceptance'] != 'not_recorded':
        raise AgentError('当前版保留记录不能推导整体任务验收')


def render_text(presentation: dict) -> str:
    return '\n\n'.join([''.join(presentation['summary']), presentation['facets']['delivery'], presentation['next_action']])


def failure_message(run: Path | None, operation: str, detail: str) -> dict:
    steps = {'run': '生成流程', 'resume': '任务恢复', 'prepare-eval': '复查准备',
             'record-test': '复查登记', 'finalize': '交付保存', 'record-result': '生成结果登记',
             'record-feedback': '用户反馈登记', 'dispatch': '生成请求准备', 'preflight': '生成前检查'}
    presentation = build_presentation(run) if run else None
    summary = [f'{steps.get(operation, "当前操作")}停止，尚未完成这一步。']
    if presentation:
        summary += [presentation['facets']['execution'], presentation['facets']['review'], presentation['facets']['delivery']]
        if 'solution.json' in presentation['evidence_refs']:
            summary.append('已有方案文件仍保留。')
    else:
        summary.append('尚未取得可核对的任务记录，图片与保留内容仍待确认。')
    next_action = presentation['next_action'] if presentation else '先核对本次输入与任务位置，再继续当前步骤。'
    reason = ('这一步所需的输入记录不完整或不符合要求。' if detail.startswith('字段校验失败')
              else '本次操作未满足当前生成许可条件。' if '授权' in detail
              else '本次生成请求记录未通过核对。' if 'request' in detail.lower()
              else '需要的本地文件缺失或暂时无法读写。' if any(x in detail for x in ('WinError', 'Errno', 'No such file'))
              else '当前任务记录不能支持这一步，需先核对具体缺失或冲突。')
    return {'summary': summary, 'reason': reason, 'next_action': next_action,
            'user_action': '只有缺少必要素材、账户配置或新的调用许可时，才需要你补充。',
            'diagnostic_detail': detail, 'presentation': presentation}
