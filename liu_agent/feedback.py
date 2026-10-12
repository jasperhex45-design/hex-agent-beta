"""Explicit user feedback, bound to one selected image and contract, never inferred from Eval."""
from pathlib import Path
from .io import AgentError, read_json, write_json, digest, now, sha, within
from . import feedback_sources


def _binding(result: dict) -> dict:
    trial = next((t for t in result.get('trials', []) if t['round'] == result.get('selected_trial_round')), None)
    if trial is None:
        raise AgentError('没有已登记的选中候选，不能记录用户图像验收')
    return {'candidate_sha256': (result.get('delivery_asset') or trial)['sha256'],
            'contract_digest': result['acceptance']['digest'], 'packet_digest': result['packet_digest']}


def _delivered_candidate(run: Path, result: dict, trial: dict) -> dict:
    """Keep native trials immutable while feedback refers to the final asset."""
    if not result.get('delivery_asset'):
        return trial
    from .bounded_repair import delivery_asset
    asset = delivery_asset(run)
    if (asset != result['delivery_asset'] or asset['parent']['candidate_sha256'] != trial['sha256']
            or asset['parent']['request_id'] != trial['request_id']):
        raise AgentError('反馈最终资产与原首稿及本地变换回执不一致')
    return {**trial, 'file': asset['file'], 'sha256': asset['sha256'],
            'delivery_asset': asset, 'delivery_origin': 'local_deterministic_transform',
            'native_parent_file': trial['file'], 'native_parent_sha256': trial['sha256']}


def _read_feedback(run: Path, binding: dict) -> dict | None:
    """Verify a receipt independently of whether delivery has been exported."""
    path = run/'user-feedback.json'
    if not path.exists():
        return None
    receipt = read_json(path)
    if not isinstance(receipt, dict) or not isinstance(receipt.get('feedback'), dict):
        raise AgentError('用户反馈记录结构无效')
    payload = receipt['feedback']
    if receipt.get('digest') != digest(payload):
        raise AgentError('用户反馈记录已改变，不能推断认可')
    if any(payload.get(k) != v for k, v in binding.items()):
        return None
    if payload.get('decision') not in ('accepted_by_user', 'rejected_by_user'):
        raise AgentError('用户反馈状态无效')
    if any(not isinstance(payload.get(key), str) or not payload[key].strip()
           for key in ('user_quote', 'source', 'recorded_at')):
        raise AgentError('用户反馈缺少原话或来源')
    if payload.get('format') == 'liu.user-feedback.v2':
        if payload.get('source_evidence') == feedback_sources.EVIDENCE:
            evidence = feedback_sources.verify_reference(run, payload, binding)
        elif payload.get('source_evidence') == feedback_sources.LEGACY_EVIDENCE and not any(
                key in payload for key in ('source_id', 'source_digest', 'decision_digest')):
            evidence = {'source_evidence': feedback_sources.LEGACY_EVIDENCE,
                        'acceptance_scope': feedback_sources.SCOPE}
        else:
            raise AgentError('反馈来源证据等级无效')
        if payload.get('acceptance_scope') != feedback_sources.SCOPE:
            raise AgentError('反馈范围无效，不能扩为全任务验收')
    elif 'format' in payload or any(key in payload for key in ('source_id', 'source_digest', 'decision_digest')):
        raise AgentError('未知反馈格式或来源证据')
    else:
        evidence = {'source_evidence': feedback_sources.LEGACY_EVIDENCE,
                    'acceptance_scope': feedback_sources.SCOPE}
    return {**payload, **evidence}


def _generation_binding(run: Path, packet: dict, contract: dict, candidate: dict) -> dict:
    from .task_review import _candidate, _generation_binding as make_binding
    canonical, evidence, source_sha = _candidate(run, packet, contract, within(run, candidate['file']))
    if canonical != candidate['sha256']:
        raise AgentError('用户反馈候选与原生成图像摘要不一致')
    return make_binding(canonical, evidence, source_sha)


def _verify_generation_binding(run: Path, packet: dict, contract: dict,
                               candidate: dict, feedback: dict) -> None:
    if 'recording_phase' not in feedback and 'generation_binding' not in feedback:
        return  # Existing exported-image receipts retain their original contract.
    if (feedback.get('recording_phase') != 'before_completion'
            or feedback.get('decision') not in ('rejected_by_user', 'accepted_by_user')
            or feedback.get('source_evidence') != feedback_sources.EVIDENCE
            or feedback.get('generation_binding') != _generation_binding(run, packet, contract, candidate)):
        raise AgentError('完成前用户反馈与原请求、候选或来源证据不一致')
    if feedback['decision'] == 'accepted_by_user':
        from .delivery_review import accepted_deviation
        if accepted_deviation(run, feedback['generation_binding']['request_id']) is None:
            raise AgentError('完成前接受缺少已披露、已确认的局部偏差证据')


def _unexported_candidate(run: Path) -> tuple[dict, dict, dict]:
    """A sole completed image is an unambiguous feedback target, not an export."""
    from .workflow_policy import _task, _candidates
    packet, contract, _ = _task(run)
    candidates, pending = _candidates(run, packet, contract)
    if pending or len(candidates) != 1 or packet['brief'].get('model_comparison'):
        raise AgentError('完成前的否决需要本任务唯一已完成候选；不猜选多图或未决请求')
    return packet, contract, candidates[0]


def _save_feedback(run: Path, payload: dict, source_snapshot: dict | None) -> None:
    path = run/'user-feedback.json'
    history = read_json(run/'user-feedback-history.json') if (run/'user-feedback-history.json').exists() else []
    if not isinstance(history, list):
        raise AgentError('用户反馈历史结构无效')
    if path.exists():
        history.append(read_json(path))
        write_json(run/'user-feedback-history.json', history)
    if source_snapshot is not None:
        write_json(run/feedback_sources.FILE, source_snapshot)
    write_json(path, {'feedback': payload, 'digest': digest(payload)})


def _record_unexported_rejection(run: Path, quote: str, source: str, *,
                                 sources_file: Path | None, source_id: str | None) -> dict:
    if not source_id:
        raise AgentError('完成前登记否决须绑定真实用户消息快照；不能用无来源文字代替')
    packet, contract, candidate = _unexported_candidate(run)
    binding = {'candidate_sha256': candidate['sha256'], 'contract_digest': contract['digest'],
               'packet_digest': packet['digest']}
    snapshot, evidence = feedback_sources.prepare_snapshot(
        run, sources_file, source_id, 'rejected_by_user', quote, binding)
    generation = _generation_binding(run, packet, contract, candidate)
    payload = {**binding, 'decision': 'rejected_by_user', 'user_quote': quote, 'source': source,
               'origin': 'explicit_user_feedback_host_reported', 'recorded_at': now(),
               'format': 'liu.user-feedback.v2', 'recording_phase': 'before_completion',
               'generation_binding': generation, **evidence}
    _save_feedback(run, payload, snapshot)
    return {'human_acceptance': 'rejected_by_user', 'status': 'user_rejected', **binding,
            'acceptance_scope': feedback_sources.SCOPE, 'task_acceptance': 'not_recorded',
            'source_evidence': evidence['source_evidence'], 'generation_binding': generation,
            'machine_status': 'not_changed_by_feedback', 'completion_granted': False,
            'notice': '已记录你对这张图的否决；原图、请求与反馈保留，尚未完成的检查仍待处理，不必重复选择。'}


def _record_unexported_acceptance(run: Path, quote: str, source: str, *,
                                 sources_file: Path | None, source_id: str | None) -> dict:
    from .delivery_review import deviation_evidence, require_disclosure
    if not source_id:
        raise AgentError('接受仍须绑定真实用户消息与已披露偏差，不能用无来源文字代替')
    try:
        evidence = deviation_evidence(run)
    except (AgentError, OSError, ValueError, KeyError) as exc:
        raise AgentError('接受仍须绑定已导出的候选，或完整核验的新策略局部偏差：' + str(exc)) from exc
    packet, contract, candidate = _unexported_candidate(run)
    binding = {key: evidence[key] for key in ('candidate_sha256', 'contract_digest', 'packet_digest')}
    snapshot, reference = feedback_sources.prepare_snapshot(
        run, sources_file, source_id, 'accepted_by_user', quote, binding)
    require_disclosure(snapshot, source_id, evidence)
    payload = {**binding, 'decision': 'accepted_by_user', 'user_quote': quote, 'source': source,
               'origin': 'explicit_user_feedback_host_reported', 'recorded_at': now(),
               'format': 'liu.user-feedback.v2', 'recording_phase': 'before_completion',
               'generation_binding': _generation_binding(run, packet, contract, candidate),
               'deviation_acceptance': evidence, **reference}
    _save_feedback(run, payload, snapshot)
    return {'human_acceptance': 'accepted_by_user', 'status': 'accepted_with_deviations', **binding,
            'acceptance_scope': feedback_sources.SCOPE, 'task_acceptance': 'not_recorded',
            'source_evidence': reference['source_evidence'], 'machine_status': 'not_changed_by_feedback',
            'completion_granted': False,
            'notice': '已记录对披露偏差的接受；原检查不改写，执行workflow complete完成交付，不再自动返修。'}


def apply_unexported_feedback_to_status(run: Path, report: dict) -> dict:
    """Expose a verified decision while preserving the actual incomplete stage."""
    if (Path(run)/'result.json').exists() or not (Path(run)/'user-feedback.json').exists():
        return report
    try:
        _, _, feedback = verified_feedback(Path(run))
    except (AgentError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        report['blockers'].append('完成前用户反馈未核验：' + str(exc))
        report.update(stage='blocked', generation_allowed=False,
                      next_action={'command': None, 'description': '核对反馈与当前图像的来源绑定，不能推断已记录用户选择。'})
        return report
    report.update(human_acceptance=feedback['decision'], user_feedback=feedback,
                  acceptance_scope=feedback_sources.SCOPE, task_acceptance='not_recorded',
                  feedback_source_evidence=feedback['source_evidence'])
    action = report.get('next_action')
    if isinstance(action, dict) and not report['blockers']:
        action = dict(action)  # Do not rewrite the completion subsystem's shared action.
        action['description'] = ('已记录本图否决，不必重复选择。' if feedback['decision'] == 'rejected_by_user'
                                 else '已记录带偏差接受，不再自动返修；原检查保持。') + action.get('description', '')
        report['next_action'] = action
    return report


def apply_feedback(run: Path, result: dict) -> None:
    # Feedback changes the selected-image decision, never machine review or task acceptance.
    selected = next((t for t in result.get('trials', [])
                     if t['round'] == result.get('selected_trial_round')), None)
    machine_status = result.get('status')
    if selected:
        from .design import quality_status
        machine_status = quality_status(result['acceptance'], selected.get('review'), True)
        if (result.get('comparison') or {}).get('choice') == 'uncertain' or result.get('status') == 'candidate_selection_uncertain':
            machine_status = 'candidate_selection_uncertain'
    result['machine_status'] = machine_status
    result['status'] = machine_status
    result['human_acceptance'] = 'not_recorded'
    result['acceptance_scope'] = 'not_recorded'
    result['task_acceptance'] = 'not_recorded'
    result['feedback_source_evidence'] = 'not_recorded'
    result.pop('user_feedback', None)
    result.pop('feedback_notice', None)
    from .candidate_feedback import apply_candidate_feedback
    apply_candidate_feedback(run, result)
    path = run/'user-feedback.json'
    if not path.exists():
        return
    payload = _read_feedback(run, _binding(result))
    if payload is None:
        result['feedback_notice'] = '历史反馈绑定另一张图或另一套标准；本候选尚未获用户确认'
        return
    if 'recording_phase' in payload or 'generation_binding' in payload:
        from .workflow import read_packet
        _verify_generation_binding(run, read_packet(run), result['acceptance'],
                                   _delivered_candidate(run, result, selected), payload)
    result['human_acceptance'] = payload['decision']
    result['user_feedback'] = payload
    result['acceptance_scope'] = feedback_sources.SCOPE
    result['feedback_source_evidence'] = payload['source_evidence']
    result['feedback_notice'] = ('反馈仅适用于选中候选；不证明机器复查通过、全任务验收或长期偏好。'
        + ('来源为旧式宿主文字记录，未绑定可复核消息快照。'
           if payload['source_evidence'] == feedback_sources.LEGACY_EVIDENCE
           else '消息与决策范围已经本地绑定；不是平台认证的用户身份或语义证明。'))
    if payload['decision'] == 'rejected_by_user':
        result['status'] = 'user_rejected'
    elif payload.get('deviation_acceptance'):
        result['status'] = 'accepted_with_deviations'
        result['feedback_notice'] += '用户已确认披露的局部偏差；机器标准仍未全部满足，不自动返修。'


def verified_candidate(run: Path, request_id: str | None = None, *,
                       comparison_member: bool = False,
                       _result: dict | None = None) -> tuple[dict, dict, dict]:
    """Read the native authoritative evidence before recording or recalling taste."""
    from .workflow import read_packet, validate_trial
    packet = read_packet(run)
    result = read_json(run/'result.json') if _result is None else _result
    if request_id is None:
        trial = next((t for t in result.get('trials', [])
                      if t['round'] == result.get('selected_trial_round')), None)
        if trial is None:
            raise AgentError('没有已登记的选中候选，须明确指定父图request_id')
    else:
        matches = [t for t in result.get('trials', []) if t.get('request_id') == request_id]
        if len(matches) != 1:
            raise AgentError('指定的父图request_id不是本任务唯一已完成候选')
        trial = matches[0]
        selected_round = result.get('selected_trial_round')
        if selected_round is not None and trial['round'] != selected_round and not comparison_member:
            raise AgentError('指定的父图不是本任务已选中候选')
        if comparison_member and (not packet['brief'].get('model_comparison')
                                  or trial.get('target') not in packet['brief']['targets']):
            raise AgentError('相对偏好只适用于当前双模型比较的候选')
    binding = {'candidate_sha256': trial['sha256'],
               'contract_digest': result['acceptance']['digest'],
               'packet_digest': result['packet_digest']}
    contract = read_json(run/'acceptance.json')
    if (packet['digest'] != result['packet_digest'] or contract != result['acceptance']
            or digest({k:v for k,v in contract.items() if k != 'digest'}) != contract['digest']):
        raise AgentError('结果对应旧需求或旧标准，先重新导出')
    validate_trial(packet, run, contract, trial)
    if trial['origin'] == 'host_reported_not_api_verified':
        from .host import validate_trial_dispatch
        validate_trial_dispatch(packet, run, trial)
        ledger = run/'host-image-tests.json'
    elif trial['origin'] == 'api_image_bytes':
        ledger = run/'image-tests.json'
    else:
        raise AgentError('候选来源不是已登记的原生流程')
    if read_json(ledger) != result.get('trials', []):
        raise AgentError('结果与本任务已登记试图不一致，先重新导出')
    if sha(within(run, trial['file']).read_bytes()) != binding['candidate_sha256']:
        raise AgentError('当前候选已改变，不能把旧反馈绑定新图片')
    return packet, result, _delivered_candidate(run, result, trial)


def verified_feedback(run: Path) -> tuple[dict, dict, dict]:
    """Return explicit feedback only while its image, request and checks still match."""
    if not (run/'result.json').exists():
        packet, contract, candidate = _unexported_candidate(run)
        binding = {'candidate_sha256': candidate['sha256'], 'contract_digest': contract['digest'],
                   'packet_digest': packet['digest']}
        feedback = _read_feedback(run, binding)
        if (not feedback or feedback.get('recording_phase') != 'before_completion'
                or feedback.get('origin') != 'explicit_user_feedback_host_reported'):
            raise AgentError('没有绑定当前已完成候选的完成前用户否决')
        _verify_generation_binding(run, packet, contract, candidate, feedback)
        return packet, candidate, feedback
    packet, result, trial = verified_candidate(run)
    apply_feedback(run, result)
    feedback = result.get('user_feedback')
    if not feedback or feedback.get('origin') != 'explicit_user_feedback_host_reported':
        raise AgentError('反馈未绑定当前候选或并非用户明确反馈')
    return packet, trial, feedback


def record_feedback(run: Path, decision: str, user_quote: str, source: str, *,
                    sources_file: Path | None = None, source_id: str | None = None,
                    sources: Path | None = None, request_id: str | None = None,
                    compared_request_id: str | None = None) -> dict:
    if sources is not None and sources_file is not None:
        raise AgentError('反馈sources与sources_file不能同时指定')
    if decision == 'positive_feedback':
        if compared_request_id is not None:
            raise AgentError('正向反馈只绑定当前候选，不指定比较对象')
        from .candidate_feedback import record_positive_feedback
        return record_positive_feedback(run, user_quote, source,
            sources_file=sources if sources is not None else sources_file,
            source_id=source_id, request_id=request_id)
    if decision == 'relative_preference':
        return record_relative_preference(run, user_quote, source,
            sources_file=sources if sources is not None else sources_file,
            source_id=source_id, request_id=request_id,
            compared_request_id=compared_request_id)
    if request_id is not None or compared_request_id is not None:
        raise AgentError('接受或否决继续仅绑定已选中候选，不接受相对比较request_id')
    if (decision not in ('accepted_by_user', 'rejected_by_user')
            or not isinstance(user_quote, str) or not user_quote.strip()
            or not isinstance(source, str) or not source.strip()):
        raise AgentError('仅在用户明确反馈后记录accepted_by_user/rejected_by_user，并保留原话与消息来源')
    if sources is not None:
        if sources_file is not None:
            raise AgentError('反馈sources与sources_file不能同时指定')
        sources_file = sources
    if sources_file is not None and not source_id:
        raise AgentError('反馈来源文件需要指定source_id')
    # Reuse the run lock so feedback cannot race a host dispatch.
    from .host import dispatch_lock
    from .exporter import export_result
    with dispatch_lock(run):
        if not (run/'result.json').exists():
            recorder = _record_unexported_rejection if decision == 'rejected_by_user' else _record_unexported_acceptance
            return recorder(run, user_quote.strip(), source.strip(), sources_file=sources_file, source_id=source_id)
        packet, result, trial = verified_candidate(run)
        binding = _binding(result)
        source_snapshot = None
        if source_id is not None:
            source_snapshot, evidence = feedback_sources.prepare_snapshot(
                run, sources_file, source_id, decision, user_quote.strip(), binding)
        else:
            if (run/feedback_sources.FILE).exists():
                raise AgentError('已有消息来源快照；更新反馈必须引用新增消息，不能退回无来源记录')
            evidence = {'source_evidence': feedback_sources.LEGACY_EVIDENCE,
                        'acceptance_scope': feedback_sources.SCOPE}
        payload = {**binding, 'decision': decision, 'user_quote': user_quote.strip(), 'source': source.strip(),
                   'origin': 'explicit_user_feedback_host_reported', 'recorded_at': now(),
                   'format': 'liu.user-feedback.v2', **evidence}
        if (decision == 'accepted_by_user'
                and result.get('workflow_completion', {}).get('delivery_outcome') == 'accepted_with_deviations'):
            from .delivery_review import deviation_evidence, require_disclosure
            if source_snapshot is None or source_id is None:
                raise AgentError('带偏差交付后的接受仍须绑定真实披露与用户回复，不能退回无来源好评')
            deviation = deviation_evidence(run)
            require_disclosure(source_snapshot, source_id, deviation)
            payload.update(recording_phase='before_completion', deviation_acceptance=deviation,
                           generation_binding=_generation_binding(run, packet, result['acceptance'], trial))
        _save_feedback(run, payload, source_snapshot)
        apply_feedback(run, result)
        apply_relative_preference(run, result)
        export_result(result, run)
        write_json(run/'run-state.json', {'status': result['status'], 'review_authority': result['review_authority'],
                                        'human_acceptance': result['human_acceptance']})
        return {'human_acceptance': result['human_acceptance'], 'status': result['status'], **binding,
                'acceptance_scope': result['acceptance_scope'], 'task_acceptance': result['task_acceptance'],
                'source_evidence': result['feedback_source_evidence'], 'machine_status': result['machine_status'],
                'notice': result['feedback_notice']}


RELATIVE_FILE = 'relative-preference.json'


def _relative_binding(run: Path, preferred_id: str, compared_id: str) -> tuple[dict, dict]:
    if not preferred_id or not compared_id or preferred_id == compared_id:
        raise AgentError('相对偏好须指定两个不同的本任务request_id')
    packet, result, preferred = verified_candidate(run, preferred_id, comparison_member=True)
    _, other_result, compared = verified_candidate(run, compared_id, comparison_member=True)
    if (not packet['brief'].get('model_comparison') or len(result.get('trials', [])) != 2
            or other_result.get('packet_digest') != result.get('packet_digest')):
        raise AgentError('相对偏好只适用于本任务已完成的双候选比较')
    binding = {'preferred_request_id': preferred_id, 'preferred_sha256': preferred['sha256'],
               'compared_request_id': compared_id, 'compared_sha256': compared['sha256'],
               'contract_digest': result['acceptance']['digest'], 'packet_digest': packet['digest']}
    return result, binding


def verified_relative_preference(run: Path) -> dict | None:
    path = run/RELATIVE_FILE
    if not path.exists():
        return None
    receipt = read_json(path)
    if (not isinstance(receipt, dict) or receipt.get('format') != 'liu.relative-preference.v1'
            or receipt.get('digest') != digest({k: v for k, v in receipt.items() if k != 'digest'})):
        raise AgentError('相对偏好记录结构或摘要无效')
    binding = receipt.get('binding')
    if not isinstance(binding, dict):
        raise AgentError('相对偏好缺少候选绑定')
    _, current = _relative_binding(run, binding.get('preferred_request_id'),
                                    binding.get('compared_request_id'))
    if binding != current:
        raise AgentError('相对偏好已不属于当前图片或合同')
    feedback_sources.verify_relative_snapshot(receipt)
    return receipt


def apply_relative_preference(run: Path, result: dict) -> None:
    receipt = verified_relative_preference(run)
    if receipt is None:
        result.pop('relative_preference', None)
    else:
        result['relative_preference'] = {'binding': receipt['binding'],
                                         'messages': receipt['messages'],
                                         'source_evidence': feedback_sources.EVIDENCE,
                                         'scope': 'current_run_comparison'}


def record_relative_preference(run: Path, user_quote: str, source: str, *,
                               sources_file: Path | None, source_id: str | None,
                               request_id: str | None,
                               compared_request_id: str | None) -> dict:
    if (not isinstance(user_quote, str) or not user_quote.strip()
            or not isinstance(source, str) or not source.strip()
            or sources_file is None or not source_id):
        raise AgentError('相对偏好需要原话、来源、sources文件及source_id')
    from .host import dispatch_lock
    from .exporter import export_result
    with dispatch_lock(run):
        result, binding = _relative_binding(run, request_id, compared_request_id)
        previous = verified_relative_preference(run)
        snapshot, message = feedback_sources.prepare_relative_snapshot(
            previous, sources_file, source_id, user_quote.strip(), binding)
        receipt = {'format': 'liu.relative-preference.v1', 'binding': binding,
                   'sources': snapshot['sources'], 'decisions': snapshot['decisions'],
                   'messages': [*(previous['messages'] if previous else []),
                                {**message, 'source': source.strip(), 'recorded_at': now()}]}
        receipt['digest'] = digest(receipt)
        write_json(run/RELATIVE_FILE, receipt)
        apply_feedback(run, result)
        apply_relative_preference(run, result)
        export_result(result, run)
        write_json(run/'run-state.json', {'status': result['status'],
                   'review_authority': result['review_authority'],
                   'human_acceptance': result['human_acceptance']})
        return {'status': result['status'], 'human_acceptance': result['human_acceptance'],
                'task_acceptance': result['task_acceptance'],
                'relative_preference': result['relative_preference']}
