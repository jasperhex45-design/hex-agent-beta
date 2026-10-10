"""Read-only inspection of durable evidence, never a request/retry executor."""
from __future__ import annotations

from pathlib import Path

from .contracts import PairComparison, Solution
from .design import acceptance, quality_status
from .io import AgentError, digest, read_json, sha, within
from .reference_gate import validate_generation_inputs, validate_reference_policy
from .revision import select_pair
from .execution_state import execution_facts, execution_quality_status
from .workflow import compile_texts, generation_references, read_packet, validate_solution, validate_trial


_EVIDENCE_ERRORS = (AgentError, OSError, ValueError, TypeError, KeyError, AttributeError)


def _read(run: Path, name: str, expected: type):
    value = read_json(within(run, name))
    if not isinstance(value, expected) or (expected is list and any(not isinstance(v, dict) for v in value)):
        raise AgentError(f'{name} 结构无效')
    return value


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentError('调用预算计数必须是非负整数')
    return value


def _budget(run: Path, attempts: list[dict], blockers: list[str]) -> dict:
    images = calls = len(attempts)
    api = (run/'calls.json').exists() or (run/'image-tests.json').exists() or (run/'api').exists()
    safe = True
    if api:
        # Old API runs save calls only in finally, with no pre-request reservation
        # or persisted limits. Expose the lower bound without inventing safe reuse.
        safe = False
        blockers.append('API旧记录缺少请求前预留及原始预算上限；不能据此安全恢复或重发')
        if (run/'calls.json').exists():
            try:
                ledger = _read(run, 'calls.json', dict)
                events = ledger['events']
                if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
                    raise AgentError('calls.json events结构无效')
                calls += max(_count(ledger['attempted_calls']), len(events))
                images += max(_count(ledger['attempted_images']), sum(e.get('kind') == 'image' for e in events))
            except _EVIDENCE_ERRORS as exc:
                blockers.append(_error('calls.json', exc))
                calls = images = None
        else:
            calls = images = None
    budget = {'attempted_calls': calls, 'attempted_images': images,
            'max_calls': None, 'max_images': None if api else 2,
            'remaining_images': None if api or images is None else max(0, 2-images),
            'recovery_safe': safe, 'counts_failed_and_unknown': True,
            'cost': None, 'cost_status': 'not_available',
             'notice': '次数不是金额；失败及未知结果占用已尝试次数。API旧计数仅是已落盘下界。'}
    from .native_budget import budget_for_run
    shared = None
    try:
        shared = budget_for_run(run)
        if shared:
            budget.update(shared)
    except _EVIDENCE_ERRORS as exc:
        blockers.append(_error('原生预算转接', exc))
        budget.update(attempted_calls=None, attempted_images=None, remaining_images=None, recovery_safe=False)
    if not api and (run/'packet.json').exists():
        try:
            packet = read_packet(run)
            if packet.get('source_contract_version') == 1:
                from .generation_authorization import read_authorization
                maximum = read_authorization(run, packet)['max_images'] if (run/'generation-authorization.json').exists() else 0
                if shared and shared.get('explicit_revision_grant'):
                    maximum = shared['max_images']
                elif shared:
                    owner = Path(shared['budget_source'])
                    maximum = min(maximum, read_authorization(owner, read_packet(owner))['max_images'])
                used = budget['attempted_images']
                budget.update(product_cap=2, max_images=maximum,
                              remaining_images=max(0, maximum-used) if used is not None else None,
                              authorization_status='recorded' if maximum else 'not_recorded')
                if shared:
                    budget['dispatch_remaining_images'] = min(shared['dispatch_remaining_images'], budget['remaining_images'] or 0)
        except _EVIDENCE_ERRORS as exc:
            blockers.append(_error('生成授权', exc))
            budget.update(remaining_images=0, recovery_safe=False)
    return budget


def _error(name: str, exc: Exception) -> str:
    detail = str(exc) if isinstance(exc, AgentError) else '文件缺失、损坏或字段不符合当前契约'
    return f'{name}: {detail}'


def _packet_grant(packet: dict) -> dict | None:
    """Find only an explicit grant sealed in this packet's user messages."""
    from .generation_authorization import _grant
    from .provenance import verified_sources
    for message in reversed(verified_sources(packet['input_sources'])):
        if message.get('generation_grant') is not None:
            return {'source_id': message['id'], 'max_images': _grant(message)}
    return None


def _verify_attempt(packet: dict, run: Path, contract: dict, attempt: dict) -> None:
    from .host import validate_receipt
    request = attempt['request']
    if not isinstance(request, dict) or digest(request) != attempt['request_digest']:
        raise AgentError('生成前请求快照摘要不一致')
    if attempt['status'] not in ('pending', 'outcome_unknown', 'failed', 'completed'):
        raise AgentError('未知调用状态')
    if not request['id'] or request['packet_digest'] != packet['digest'] or request['contract_digest'] != contract['digest']:
        raise AgentError('调用绑定的任务包或验收标准不一致')
    solution = Solution.model_validate(request['solution'])
    validate_solution(packet, solution)
    if acceptance(packet, solution.design) != contract:
        raise AgentError('生成前方案与固定标准不一致')
    validate_generation_inputs(packet, contract['baseline_reference_ids'], request['generation_inputs'])
    images = request['images_in_order']
    if request['generation_inputs'] != [{k: row[k] for k in ('id', 'role', 'sha256')} for row in images]:
        raise AgentError('生成图片清单与生成前输入记录不一致')
    for image in images:
        if sha(within(run, image['file']).read_bytes()) != image['sha256']:
            raise AgentError('生成前输入图片缺失或已改变')
    if attempt['status'] == 'completed':
        validate_receipt(run, attempt)
        output = attempt['output']
        image = within(run, output['file']).read_bytes()
        if sha(image) != output['sha256']:
            raise AgentError('候选输出图片缺失或已改变')
        from .generation_evidence import verify_host_parameters
        verify_host_parameters(packet, attempt, image)


def _selection(run: Path, trials: list[dict]) -> dict | None:
    if not (run/'selection.json').exists():
        return None
    selection = _read(run, 'selection.json', dict)
    if selection.get('candidate_hashes') != [t['sha256'] for t in trials]:
        raise AgentError('成对比较绑定的图片与当前候选不同')
    raw = selection['comparison']
    report = PairComparison.model_validate({'choice': raw.get('reported_choice', raw.get('choice')),
                                           'evidence': raw['evidence'], 'regressions': raw['regressions']})
    index, expected = select_pair(report, trials)
    if selection['selected_index'] != index or raw != expected:
        raise AgentError('成对选择与比较证据不一致')
    return selection


def _verify_export(run: Path, packet: dict, contract: dict, solution: Solution,
                   trials: list[dict], attempts: list[dict], selection: dict | None) -> str:
    result = _read(run, 'result.json', dict)
    from .workflow_policy import require_completion_ready
    completion = require_completion_ready(run, packet=packet)
    if completion is not None and result.get('workflow_completion') != completion:
        raise AgentError('交付的完整流程回执与当前图片、合同或阶段证据不一致')
    from .visual_decision import record_only
    if record_only(packet) and result.get('visual_decision_policy') != packet['visual_decision_policy']:
        raise AgentError('导出生产策略与冻结任务不一致')
    index = selection['selected_index'] if selection else 0
    selected = trials[index] if trials else None
    snapshot = selected['solution'] if selected else solution.model_dump()
    selected_solution = Solution.model_validate(snapshot)
    prompts = compile_texts(packet, selected_solution.spec, selected_solution.compilation,
                            design=selected_solution.design)
    for trial in (trials if packet['brief'].get('model_comparison') else [selected] if selected else []):
        if trial.get('target') in prompts:
            prompts[trial['target']]['target_model_test'] = 'trial_recorded_not_human_accepted'
    if packet.get('model_adaptation_version') == 1 and result.get('model_adaptation') != {
            target: row['adaptation'] for target, row in prompts.items()}:
        raise AgentError('导出适配解释与实际编译依据不一致；不能单独改写解释')
    review = selected['review'] if selected else None
    status = quality_status(contract, review, bool(trials))
    comparison = selection['comparison'] if selection else None
    if comparison and comparison['choice'] == 'uncertain' and not record_only(packet):
        status = 'candidate_selection_uncertain'
    if not trials and attempts and all(a['status'] == 'failed' for a in attempts):
        status = 'generation_failed'
    # Old exports remain verifiable under their original contract. New exports
    # must bind all execution facts, including API failures and reservations.
    fact_keys = ('generation_status', 'generation_evidence_scope', 'known_generation_attempts')
    if any(key in result for key in fact_keys):
        facts = execution_facts(run, attempts=attempts, trials=trials)
        if any(result.get(key) != facts[key] for key in fact_keys):
            raise AgentError('导出执行事实与当前调用证据不一致')
        status = execution_quality_status(status, facts, bool(trials))
    expected = {'status': status, 'packet_digest': packet['digest'], 'acceptance': contract,
                'trials': trials, 'selected_trial_round': selected['round'] if selected and not packet['brief'].get('model_comparison') else None}
    from .feedback import apply_feedback, apply_relative_preference, verified_feedback
    if (run/'user-feedback.json').exists():
        verified_feedback(run)
    apply_feedback(run, expected)
    apply_relative_preference(run, expected)
    feedback_axes = ('machine_status', 'acceptance_scope', 'task_acceptance', 'feedback_source_evidence')
    if any(key in result for key in feedback_axes):
        if any(result.get(key) != expected.get(key) for key in feedback_axes):
            raise AgentError('导出反馈范围或机器复查状态与当前证据不一致')
    legacy_feedback = ('acceptance_scope' not in result and 'task_acceptance' not in result
                       and (run/'user-feedback.json').exists()
                       and result.get('user_feedback') == read_json(run/'user-feedback.json')['feedback'])
    legacy_user_confirmation = (result.get('status') == 'completed_user_confirmed'
                                and 'acceptance_scope' not in result
                                and 'task_acceptance' not in result
                                and expected['human_acceptance'] == 'accepted_by_user')
    if (result.get('format') != 'liu.aesthetic.result.v3' or
            result.get('packet_digest') != packet['digest'] or result.get('acceptance') != contract or
            result.get('trials') != trials or result.get('generation_attempts') != attempts or
            result.get('spec') != snapshot['spec'] or result.get('design') != snapshot['design'] or
            result.get('prompts') != prompts or
            result.get('selected_trial_round') != expected['selected_trial_round'] or
            result.get('comparison') != comparison or
            (result.get('status') != expected['status'] and not legacy_user_confirmation) or
            result.get('human_acceptance') != expected['human_acceptance'] or
            (result.get('user_feedback') != expected.get('user_feedback') and not legacy_feedback)):
        raise AgentError('导出结果与当前任务、调用或候选证据不一致；先核对后重新导出')
    previous_relative = result.get('relative_preference')
    current_relative = expected.get('relative_preference')
    relative_pending = previous_relative != current_relative
    if relative_pending:
        if not current_relative:
            raise AgentError('导出相对偏好与当前用户消息或候选证据不一致')
        old_messages = (previous_relative or {}).get('messages', [])
        new_messages = current_relative['messages']
        if (not isinstance(old_messages, list) or old_messages != new_messages[:len(old_messages)]
                or len(old_messages) >= len(new_messages)):
            raise AgentError('导出相对偏好不是当前消息记录的合法前缀')
        if previous_relative:
            if not old_messages:
                raise AgentError('旧相对偏好投影缺少消息')
            latest_old = old_messages[-1]
            current_pair = {current_relative['binding']['preferred_request_id']:
                            current_relative['binding']['preferred_sha256'],
                            current_relative['binding']['compared_request_id']:
                            current_relative['binding']['compared_sha256']}
            old_binding = previous_relative.get('binding', {})
            if (old_binding.get('preferred_request_id') != latest_old['preferred_request_id']
                    or old_binding.get('compared_request_id') != latest_old['compared_request_id']
                    or {old_binding.get('preferred_request_id'): old_binding.get('preferred_sha256'),
                        old_binding.get('compared_request_id'): old_binding.get('compared_sha256')} != current_pair
                    or old_binding.get('packet_digest') != current_relative['binding']['packet_digest']
                    or old_binding.get('contract_digest') != current_relative['binding']['contract_digest']
                    or previous_relative.get('source_evidence') != current_relative['source_evidence']
                    or previous_relative.get('scope') != current_relative['scope']):
                raise AgentError('旧相对偏好投影与已核验消息不一致')
    from .candidate_feedback import projection_pending
    positive_pending = projection_pending(result, expected)
    return expected['human_acceptance'], relative_pending or positive_pending


def _prior_relative_export_digest_matches(result: dict, old_digest: str) -> bool:
    """Recognize only a prior projection of the same verified neutral receipt."""
    relative = result.get('relative_preference')
    if not isinstance(relative, dict) or not isinstance(relative.get('messages'), list):
        return False
    messages = relative['messages']
    binding = relative.get('binding', {})
    pair = {binding.get('preferred_request_id'): binding.get('preferred_sha256'),
            binding.get('compared_request_id'): binding.get('compared_sha256')}
    for count in range(len(messages)):
        old_result = dict(result)
        if count == 0:
            old_result.pop('relative_preference', None)
        else:
            last = messages[count - 1]
            old_binding = {**binding,
                           'preferred_request_id': last['preferred_request_id'],
                           'preferred_sha256': pair[last['preferred_request_id']],
                           'compared_request_id': last['compared_request_id'],
                           'compared_sha256': pair[last['compared_request_id']]}
            old_result['relative_preference'] = {**relative,
                                                 'binding': old_binding,
                                                 'messages': messages[:count]}
        if digest(old_result) == old_digest:
            return True
    return False


def _export_files_valid(run: Path, *, verified_relative: bool = False) -> bool:
    """An interrupted/damaged rendering can be regenerated from verified evidence."""
    if not (run/'export-manifest.json').exists():
        return False
    manifest = _read(run, 'export-manifest.json', dict)
    result = _read(run, 'result.json', dict)
    if manifest.get('result_digest') != digest(result):
        from .candidate_feedback import prior_export_digest_matches
        if verified_relative and (_prior_relative_export_digest_matches(result, manifest.get('result_digest'))
                                  or prior_export_digest_matches(result, manifest.get('result_digest'))):
            return False
        raise AgentError('导出JSON与交付清单不一致；先核对被改写的结果')
    expected = {'result.html', 'result.md', *[f'prompts/{key}.txt' for key in result['prompts']]}
    if result.get('model_adaptation'):
        expected.add('design-explanation.md')
    hashes = manifest.get('files')
    if not isinstance(hashes, dict) or set(hashes) != expected:
        return False
    return all(within(run, name).is_file() and sha(within(run, name).read_bytes()) == value
               for name, value in hashes.items())


def inspect_run(run: Path, *, _dispatch_lock_held: bool = False) -> dict:
    if (Path(run) / 'api-state.json').exists():
        from .api_recovery import inspect_api_run
        return inspect_api_run(run, _dispatch_lock_held=_dispatch_lock_held)
    from .workflow_policy import apply_to_status
    return apply_to_status(run, _apply_generation_control(
        run, _inspect_run(run, _dispatch_lock_held=_dispatch_lock_held)))


def _apply_generation_control(run: Path, report: dict) -> dict:
    """One status projection shared by host and direct API inspections."""
    from .generation_authorization import read_stop
    try:
        stopped = read_stop(run)
        scope = 'current_version'
        budget = report['budget']
        if not stopped and budget.get('budget_source') and not budget.get('explicit_revision_grant'):
            owner = Path(budget['budget_source'])
            if owner.resolve() != Path(run).resolve():
                stopped = read_stop(owner)
                scope = 'shared_budget_owner' if stopped else scope
        report['generation_control'] = {'state': 'stopped' if stopped else 'active',
                                        'evidence': 'generation-stop.json' if stopped else None,
                                        'scope': scope}
        if stopped:
            report['generation_allowed'] = False
            if report['next_action'].get('command') in ('dispatch', 'execute-comfy', 'execute-pippit', 'authorize-generation'):
                report['next_action'] = {'command': None, 'action': 'generation_stopped',
                                         'description': '本版本生成已停止；保留原预算和证据，已提交任务只查原编号。'}
    except _EVIDENCE_ERRORS as exc:
        report['blockers'].append(_error('停止生成记录', exc))
        report.update(stage='blocked', generation_allowed=False,
                      generation_control={'state': 'invalid', 'evidence': 'generation-stop.json'},
                      next_action={'command': None, 'description': '核对停止来源；不能推断仍可生成'})
    return report


def _inspect_run(run: Path, *, _dispatch_lock_held: bool = False) -> dict:
    """Derive a recovery hint without writing, locking or contacting any model.

    A verified local receipt remains host-reported evidence. ``exported`` only
    describes consistent artifacts, never provider execution or visual approval.
    """
    run = Path(run).resolve()
    if (run/'api-state.json').exists():
        from .api_recovery import inspect_api_run
        return inspect_api_run(run, _dispatch_lock_held=_dispatch_lock_held)
    blockers: list[str] = []
    report = {'format': 'liu.aesthetic.run-status.v1', 'run': str(run),
              'stage': 'blocked', 'blockers': blockers, 'generation_allowed': False,
              'next_action': {'command': None, 'description': '核对阻断证据；不要重发未知请求'},
              'candidates': [], 'human_acceptance': 'not_verified',
              'visual_validation': 'not_performed_by_status',
              'notice': '只读检查本地证据；不执行恢复、生成、上传或看图，不以run-state声称完成。'}
    attempts = []
    ledger_invalid = False
    if (run/'host-dispatches.json').exists() or (run/'dispatch.json').exists():
        try:
            from .host import read_dispatches
            attempts = read_dispatches(run)
        except _EVIDENCE_ERRORS as exc:
            blockers.append(_error('host-dispatches.json', exc))
            ledger_invalid = True
    report['budget'] = _budget(run, attempts, blockers)
    if ledger_invalid:
        report['budget'].update(attempted_calls=None, attempted_images=None,
                                remaining_images=None, recovery_safe=False)

    def stage(value: str, command: str | None, description: str, **extra) -> dict:
        if command == 'dispatch' and not attempts and not blockers:
            from .visual_decision import policy
            from .visual_decision import CAR_PROFILES
            if ((policy(packet) or {}).get('profile') in CAR_PROFILES and
                    (packet.get('request_input_version') is not None or (run / 'prepared-request.json').exists())):
                from .car_photography import FROZEN_INPUT, consume_frozen_input
                try:
                    frozen = read_json(run / FROZEN_INPUT)
                    from .contracts import Solution
                    from .execution_request import build_input
                    planned = Solution.model_validate(read_json(run / 'solution.json'))
                    args = frozen['input']['arguments']
                    options = {k: args[k] for k in ('resolution', 'ratio') if k in args}
                    current = build_input(run, packet, planned, frozen['target'], frozen['tool'],
                                          provider_options=options or None)
                    consume_frozen_input(run, packet, planned, frozen['target'], frozen['tool'], current)
                except (OSError, ValueError, TypeError, KeyError, AgentError):
                    report['generation_allowed'] = False
                    return stage('needs_final_input', 'freeze-input',
                                 '先按汽车入口冻结最终正文、素材和参数；本检查不改授权、不占额度。')
        if not blockers:
            report.update(stage=value, next_action={'command': command, 'description': description, **extra})
        return report

    try:
        if ledger_invalid:
            report.update(generation_status='evidence_incomplete',
                          generation_evidence_scope='invalid_local_records', known_generation_attempts=[])
            return report
        report.update(execution_facts(run, attempts=attempts))
        packet = _read(run, 'packet.json', dict)
        packet = read_packet(run)
        if (run/'.dispatch.lock').exists() and not _dispatch_lock_held:
            blockers.append('存在dispatch写入锁；先核对原进程是否仍活动，不删除锁或重复提交')
        if len(attempts) > 2:
            raise AgentError('原生调用记录超过两次预算')
        ids = [a['request']['id'] for a in attempts]
        if len(ids) != len(set(ids)):
            raise AgentError('调用request_id重复，无法独立核算预算')
        trials = _read(run, 'host-image-tests.json', list) if (run/'host-image-tests.json').exists() else []
        if len(trials) > 2 or len({t['request_id'] for t in trials}) != len(trials):
            raise AgentError('候选记录超限或重复绑定同一次请求')
        has_generation = bool(attempts or trials or (run/'image-tests.json').exists())
        if not (run/'solution.json').exists():
            if has_generation or (run/'acceptance.json').exists() or (run/'result.json').exists():
                raise AgentError('已有后续证据但缺少solution.json；不能推断恢复方案')
            from .co_creation import blockers as direction_blockers, early_blockers, initial_assessment_required, copilot_enabled, intake_enabled
            if intake_enabled(packet) and packet.get('need_mode') == 'offline_heuristic':
                return stage('needs_parse', 'refresh', '先依据真实用户原话和已有上下文完成需求理解；离线草稿的问题不是已确认的用户问讯。明确目标或真实委托可继续，关键目标仍未知才登记并提出一个问题。')
            if copilot_enabled(packet) and early_blockers(packet):
                return stage('needs_input', 'refresh', early_blockers(packet)[0], questions=early_blockers(packet))
            if initial_assessment_required(packet):
                return stage('needs_intent_assessment', 'refresh', '先依据真实原话判断研究目标是否明确，并记录 Need.direction_review；缺判断不等于必须追问用户。')
            if direction_blockers(packet):
                return stage('needs_input', 'refresh', '可先观察已有素材并准备提案；解决关键方向后继续', questions=direction_blockers(packet))
            try:
                validate_reference_policy(packet, packet['visual_coverage']['positive_ids'])
            except AgentError as exc:
                if str(exc).startswith('needs_reference：'):
                    return stage('needs_reference', 'add-reference', str(exc))
                raise
            return stage('needs_design', None, '按当前packet完成设计与solution.json')
        solution = Solution.model_validate(_read(run, 'solution.json', dict))
        validate_solution(packet, solution)
        contract = acceptance(packet, solution.design)
        if not (run/'acceptance.json').exists():
            if has_generation or (run/'result.json').exists():
                raise AgentError('已有后续证据但缺少固定验收标准')
            return stage('needs_preflight', 'preflight', '校验方案并固定当前验收标准')
        if _read(run, 'acceptance.json', dict) != contract:
            raise AgentError('固定验收标准与当前任务或方案不同')
        for attempt in attempts:
            _verify_attempt(packet, run, contract, attempt)
            if attempt['status'] == 'completed':
                report['candidates'].append({'request_id': attempt['request']['id'],
                                             'file': attempt['output']['file'],
                                             'sha256': attempt['output']['sha256'], 'evaluated': False})
        from .visual_decision import record_only
        production = record_only(packet)
        if production:
            from .host import production_trials
            trials = production_trials(packet, run, attempts, trials)
        from .host import validate_trial_dispatch
        for trial in trials:
            validate_trial(packet, run, contract, trial)
            validate_trial_dispatch(packet, run, trial, attempts)
            if trial.get('review') is None and not production:
                raise AgentError('原生候选登记缺少Eval')
            candidate = next(c for c in report['candidates'] if c['request_id'] == trial['request_id'])
            candidate['evaluated'] = trial.get('review') is not None
        selection = None if production else _selection(run, trials)
        report.update(execution_facts(run, attempts=attempts, trials=trials))
        unresolved = [a['request']['id'] for a in attempts if a['status'] in ('pending', 'outcome_unknown')]
        if unresolved:
            report['generation_allowed'] = False
            if len(unresolved) == 1:
                from .executors import next_action
                return stage('awaiting_result', **next_action(run, unresolved[0]))
            return stage('awaiting_result', 'record-result', '核对同一request-id的真实结果；禁止重发', request_ids=unresolved)
        if production and packet['brief'].get('model_comparison'):
            remaining_targets = [t for t in packet['brief']['targets']
                                 if t not in {a['request']['target'] for a in attempts}]
            if remaining_targets and report['budget'].get('dispatch_remaining_images', report['budget']['remaining_images']) > 0:
                report['generation_allowed'] = not blockers
                return stage('ready_for_dispatch', 'dispatch', '按冻结方案执行尚未尝试的模型首图', targets=remaining_targets)
        unevaluated = [c for c in report['candidates'] if not c['evaluated']]
        if unevaluated and not production:
            return stage('needs_eval', 'prepare-eval', '先对已有候选实际看图并登记Eval', request_ids=[c['request_id'] for c in unevaluated])
        if len(trials) == 2 and not selection and not production:
            return stage('needs_compare', 'choose', '按同一原始标准比较已有两张候选；不再生成')
        if (run/'result.json').exists():
            prior = _read(run, 'result.json', dict)
            prior_trials = prior.get('trials')
            prior_attempts = prior.get('generation_attempts')
            if (isinstance(prior_trials, list) and len(prior_trials) < len(trials)
                    and prior_trials == trials[:len(prior_trials)]
                    and isinstance(prior_attempts, list)
                    and prior_attempts == attempts[:len(prior_attempts)]):
                # Verify the previous export in its own evidence epoch before
                # replacing it with the newly reviewed/selected candidates.
                _verify_export(run, packet, contract, solution, prior_trials, prior_attempts, None)
                return stage('ready_to_export', 'finalize', '旧导出证据有效；更新为当前候选及比较结果')
            acceptance_state, relative_pending = _verify_export(
                run, packet, contract, solution, trials, attempts, selection)
            if prior.get('relative_preference'):
                report['relative_preference'] = prior['relative_preference']
            if prior.get('candidate_feedback'):
                report['candidate_feedback'] = prior['candidate_feedback']
            if acceptance_state != 'not_recorded':
                report['human_acceptance'] = acceptance_state
            if relative_pending:
                # The new receipt is durable, but a prior export may remain after interruption.
                _export_files_valid(run)
                return stage('ready_to_export', 'finalize',
                             '用户反馈来源已核验；恢复导出，不重发生成请求', render_only=True)
            if _export_files_valid(run, verified_relative=bool(prior.get('relative_preference') or prior.get('candidate_feedback'))):
                from .exporter import rejection_next_action, uncertain_next_action
                reassessment = rejection_next_action(run, prior, packet) if acceptance_state == 'rejected_by_user' else None
                if reassessment:
                    return stage('exported', **reassessment)
                uncertainty = uncertain_next_action(prior)
                if uncertainty:
                    return stage('exported', **uncertainty)
                if production and not report['candidates']:
                    if attempts:
                        return stage('exported', None, '生成尝试失败，尚无候选图；先核对失败原因和剩余许可，不自动重试。')
                    grant = _packet_grant(packet)
                    if report['budget'].get('authorization_status') != 'recorded':
                        if grant:
                            return stage('exported', 'resume', '当前packet已有明确生图许可；恢复其本地登记，不重复向用户确认。')
                        return stage('exported', 'authorize-generation', '当前只导出提示词，尚无已登记的明确生图许可。')
                    if packet.get('requirement_parent') and not report['budget'].get('transfer_role'):
                        return stage('exported', 'resume', '当前子版本许可已登记；核验父子同一许可并恢复原预算关联，不追加额度。')
                    observed = {f['id'] for f in packet.get('reference_analysis', {}).get('findings', []) if f.get('seen')}
                    if any(ref['id'] not in observed for ref in
                           packet['references'] + packet.get('host_visible_references', [])):
                        return stage('exported', 'observe', '提示词已导出；先实际查看本任务参考图。')
                    generation_refs, _ = generation_references(packet, run)
                    if any(ref['rights'] != 'user_authorized' for ref in generation_refs):
                        blockers.append('生成输入图片缺少当前任务授权；不可调用生成')
                        return report
                    remaining = report['budget'].get('dispatch_remaining_images', report['budget']['remaining_images'])
                    if remaining and remaining > 0:
                        report['generation_allowed'] = not blockers
                        return stage('exported', 'dispatch', '提示词已导出、尚无候选图且许可有效；可按冻结请求执行首图，无需重复确认。')
                    return stage('exported', None, '尚无候选图，原有可用次数为零；不能自行追加生成。')
                if report.get('candidate_feedback') and acceptance_state == 'not_recorded':
                    return stage('exported', None, '已记录当前候选的正向评价与原话；沿这版继续，修改前明确改变与保留范围。')
                if prior.get('next_action') and acceptance_state == 'not_recorded':
                    return stage('exported', **prior['next_action'])
                if production:
                    if packet['brief'].get('model_comparison') and len(trials) == 2:
                        if report.get('relative_preference'):
                            return stage('exported', None, '已记录本轮相对偏好与原话；如需继续调整，请说明新目标。不会自动生成或视为最终接受。')
                        return stage('exported', None, '请直接说哪张更接近、哪里还需调整；也可以逐张表示接受或拒绝。')
                    return stage('exported', None, '请查看原图，直接说明接受或拒绝这一版；机器意见仅作为研究记录')
                return stage('exported', None, '交付文件证据一致；视觉收益及用户接受仍须单独验收')
            return stage('ready_to_export', 'finalize', '结果证据一致，修复交付文件', render_only=True)
        if trials:
            return stage('ready_to_export', 'finalize', '导出已有候选及复查证据；用户保留权不由机器代替')
        if report['budget']['remaining_images'] == 0:
            if report['budget'].get('authorization_status') == 'not_recorded':
                if _packet_grant(packet):
                    return stage('needs_authorization', 'resume', '当前packet已有明确生图许可；恢复其本地登记。')
                return stage('needs_authorization', 'authorize-generation', '尚未记录实际生图许可；已有提示词仍可交付')
            return stage('generation_failed', 'finalize', '预算已耗尽；保留失败记录并导出当前提示词')
        observed = {f['id'] for f in packet.get('reference_analysis', {}).get('findings', []) if f.get('seen')}
        if any(ref['id'] not in observed for ref in
                           packet['references'] + packet.get('host_visible_references', [])):
            return stage('needs_observation', 'observe', '实际看完本任务参考，再记录当前观察')
        generation_refs, _ = generation_references(packet, run)
        if any(ref['rights'] != 'user_authorized' for ref in generation_refs):
            blockers.append('生成输入图片缺少当前任务授权；不可调用生成')
            return report
        report['generation_allowed'] = not blockers and report['budget'].get('dispatch_remaining_images', report['budget']['remaining_images']) > 0
        return stage('ready_for_dispatch', 'dispatch', '下一步可在既有授权内固定生成请求；本检查不会调用工具')
    except _EVIDENCE_ERRORS as exc:
        blockers.append(_error('任务证据', exc))
        report['generation_allowed'] = False
        return report
