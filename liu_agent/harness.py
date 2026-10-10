"""Workflow-as-code over the existing evidence kernel, with one local step at a time."""
from copy import deepcopy
from pathlib import Path

from .io import AgentError, digest, read_json, sha, within


# Model judgments are checkpoints. Code owns order, evidence and side effects.
# The definition is descriptive of existing entrypoints, never an authorization.
_STEPS = (
    ('intent', 'host_model', ('prepare', 'refresh'), ('user_message',), ('packet.json',)),
    ('references', 'host_model', ('observe', 'add-reference', 'attach-research'), ('packet.json',), ('reference_analysis',)),
    ('design', 'host_model', (), ('packet.json', 'reference_analysis'), ('solution.json',)),
    ('compile', 'host_model', ('prepare-compilation',), ('solution.json',), ('model_compilation',)),
    ('verify', 'code', ('preflight', 'freeze-input'), ('solution.json',), ('acceptance.json',)),
    ('authorize', 'code', ('authorize-generation', 'resume'), ('user_message',), ('generation-authorization.json',)),
    ('execute', 'executor', ('dispatch', 'execute-comfy', 'execute-pippit', 'query-comfy', 'query-pippit', 'record-result'), ('frozen_request',), ('execution_receipt',)),
    ('evaluate', 'host_model', ('prepare-eval', 'record-test', 'choose'), ('candidate_image', 'acceptance.json'), ('image_evaluation',)),
    ('deliver', 'code', ('finalize',), ('verified_evidence',), ('result.json',)),
    ('feedback', 'user', ('record-feedback',), ('candidate_image',), ('user-feedback.json',)),
)
_LOCAL_COMMANDS = frozenset(('preflight', 'prepare-eval', 'choose', 'finalize', 'resume'))
_STAGE_STEPS = {'needs_parse': 'intent', 'needs_input': 'intent', 'needs_intent_assessment': 'intent',
                'needs_observation': 'references', 'needs_reference': 'references',
                'needs_research': 'references', 'needs_visual_research': 'references',
                'needs_selection_assessment': 'references', 'needs_reference_selection': 'references',
                'needs_reference_expansion': 'references',
                'needs_design': 'design', 'needs_compile': 'compile',
                'needs_preflight': 'verify', 'needs_final_input': 'verify',
                'needs_authorization': 'authorize', 'ready_for_dispatch': 'execute',
                'awaiting_result': 'execute', 'needs_eval': 'evaluate', 'needs_compare': 'evaluate',
                'ready_to_export': 'deliver', 'generation_failed': 'deliver',
                'needs_review_requirements': 'check_requirements', 'needs_image_check': 'image_check',
                'needs_review_input': 'visual_review', 'needs_visual_review': 'visual_review',
                'quality_not_met': 'visual_review', 'needs_generation': 'execute'}
_GATES = {'intent': 'provenance.validate_need', 'references': 'reference_gate.validate_reference_policy',
          'design': 'workflow.validate_solution', 'compile': 'model_adaptation.validate_adaptation',
          'verify': 'workflow.freeze_contract', 'authorize': 'generation_authorization.read_authorization',
          'execute': 'generation_authorization.submission_guard', 'evaluate': 'workflow.validate_trial',
          'deliver': 'run_status._verify_export', 'feedback': 'feedback.verified_feedback'}


def definition(workflow_profile: str | None = None) -> dict:
    steps = list(_STEPS)
    gates = dict(_GATES)
    if workflow_profile == 'full-flow-v1':
        index = next(i for i, row in enumerate(steps) if row[0] == 'deliver')
        steps[index:index] = [
            ('check_requirements', 'host_model', ('workflow',), ('bound_user_sources', 'acceptance.json'), ('workflow/requirements.json',)),
            ('image_check', 'code', ('workflow',), ('candidate_image', 'workflow/requirements.json'), ('workflow/checks',)),
            ('visual_review', 'host_model', ('workflow',), ('candidate_image', 'references', 'acceptance.json'), ('workflow/reviews',)),
        ]
        gates.update(check_requirements='workflow_policy._requirements',
                     image_check='workflow_policy._bound_stage', visual_review='task_review.validate_task_review')
    body = {'id': 'hex.visual-creation', 'version': 2, 'scheduling': 'serial_checkpoints',
            'parallel_analysis': 'host_optional_not_automatically_dispatched',
            'steps': [{'id': ident, 'owner': owner, 'commands': list(commands),
                       'inputs': list(inputs), 'outputs': list(outputs), 'gate': gates[ident],
                       'judgment': 'model_or_user' if owner in ('host_model', 'user') else 'deterministic_evidence',
                       'gate_scope': 'managed_submission_only' if ident == 'execute' else 'existing_stage_entrypoints'}
                      for ident, owner, commands, inputs, outputs in steps],
            'limits': {'advance_max_steps': 1, 'automatic_generation': False,
                       'automatic_repair': False, 'automatic_memory_retrieval': False,
                       'budget_source': 'existing_task_ledger', 'unknown_result': 'query_same_request',
                       'visual_judgment': 'host_model_and_user',
                       'retry_policy': {'outcome_unknown': 'query_same_request', 'pending': 'recover_same_request',
                                        'confirmed_failure': 'manual_only_with_remaining_allowance',
                                        'stop': 'no_new_submission_preserve_reservations'}}}
    if workflow_profile == 'full-flow-v1':
        body.update(version=3, completion_policy=workflow_profile)
        body['limits'].update(drive_max_steps=8, drive_hard_limit=32)
    return {**body, 'definition_digest': digest(body)}


def _input_hashes(run: Path) -> dict:
    # No recursive workspace/private-memory scan. Bind task files consumed by
    # local recovery, and the execution sidecars whose absence changes recovery.
    names = ('packet.json', 'solution.json', 'acceptance.json', 'preflight.json',
             'prepared-request.json', 'generation-authorization.json',
             'host-dispatches.json', 'dispatch.json', 'api-state.json', 'calls.json',
             'host-image-tests.json', 'image-tests.json', 'selection.json',
             'result.json', 'export-manifest.json', 'user-feedback.json',
             'relative-preference.json', 'native-budget-transfer.json', 'native-budget-receipt.json',
             'generation-stop.json')
    result = {name: sha((run / name).read_bytes()) if (run / name).is_file() else None for name in names}
    for directory in ('comfy', 'pippit'):
        for name in ('execution.json', 'actual-arguments.json', 'submit-response.json', 'submission-started.json'):
            for path in sorted((run / directory).glob('*/' + name)):
                result[str(path.relative_to(run))] = sha(path.read_bytes())
    requirement = run / 'workflow/requirements.json'
    if requirement.is_file():
        result['workflow/requirements.json'] = sha(requirement.read_bytes())
    for section in ('checks', 'review-inputs', 'reviews', 'review-sources'):
        for path in sorted((run / 'workflow' / section).glob('*.json')):
            result[str(path.relative_to(run))] = sha(path.read_bytes())
    return result


def _repair_contract(run: Path, status: dict, packet: dict) -> dict:
    """Explain existing permission gates; never run a repair or judge an image."""
    budget = status['budget']
    remaining = budget.get('dispatch_remaining_images', budget.get('remaining_images'))
    body = {'format': 'hex.repair-contract.v1', 'state': 'not_applicable',
            'automatic_execution': False, 'eligible_under_existing_allowance': False,
            'permission_is_submission_readiness': False, 'remaining_attempts': remaining,
            'budget_source': 'existing_task_ledger', 'changes': [], 'preserve': [],
            'revision_targets': [], 'next_entrypoints': [],
            'notice': '修订资格不证明编译、输入或权限仍有效；dispatch与提交边界重新核验，修订后实际看图并重新验收。'}
    if status['blockers']:
        return {**body, 'state': 'blocked', 'reason': 'evidence_incomplete'}
    if status.get('generation_control', {}).get('state') == 'stopped':
        return {**body, 'state': 'generation_stopped'}
    if status['stage'] == 'awaiting_result':
        command = status['next_action'].get('command')
        return {**body, 'state': 'await_original_request' if command and command.startswith('execute-') else 'query_same_request',
                'next_entrypoints': [command] if command else []}
    from .visual_decision import record_only
    if record_only(packet) and status['candidates']:
        return {**body, 'state': 'explicit_task_revision_required',
                'next_entrypoints': ['record-feedback', 'refresh', 'grant-native-revision']}
    name = 'image-tests.json' if (run / 'api-state.json').exists() else 'host-image-tests.json'
    trials = read_json(run / name) if (run / name).exists() else []
    if not trials:
        if status['candidates']:
            return {**body, 'state': 'needs_failure_evidence', 'next_entrypoints': ['prepare-eval', 'record-test']}
        return body
    review = trials[-1].get('review')
    if not review:
        return {**body, 'state': 'needs_failure_evidence'}
    body.update(changes=review.get('changes', []), preserve=review.get('preserve', []),
                revision_targets=review.get('revision_targets', []),
                diagnosis=review.get('diagnosis'), review_digest=digest(review))
    if status.get('human_acceptance') in ('accepted_by_user', 'rejected_by_user'):
        return {**body, 'state': 'explicit_task_revision_required'}
    if remaining == 0:
        return {**body, 'state': 'budget_exhausted',
                'next_entrypoints': ['choose', 'finalize'] if len(trials) == 2 else ['finalize']}
    if review.get('decision') == 'ready_for_user':
        return {**body, 'state': 'retain_candidate', 'next_entrypoints': ['finalize', 'record-feedback']}
    if review.get('decision') != 'revise':
        return {**body, 'state': 'needs_failure_evidence', 'next_entrypoints': ['prepare-eval', 'record-test']}
    if review.get('diagnosis') in ('intent', 'reference', 'unknown'):
        return {**body, 'state': 'needs_upstream_review'}
    if type(remaining) is not int:
        return {**body, 'state': 'blocked', 'reason': 'budget_unverified'}
    used = budget.get('grant_attempted_images', budget['attempted_images'])
    from .generation_permission import require_image_permission
    try:
        require_image_permission(max_images=used + remaining, attempted_images=used, review=review,
                                 user_decision=status.get('human_acceptance'))
    except AgentError as exc:
        return {**body, 'state': 'blocked', 'reason': str(exc)}
    return {**body, 'state': 'needs_preserving_revision', 'eligible_under_existing_allowance': True,
            'next_entrypoints': ['prepare-compilation', 'preflight', 'dispatch', 'prepare-eval', 'record-test', 'choose', 'finalize']}


def _current_host_context(packet: dict, status: dict) -> dict | None:
    """Project the inspected next action; never infer or advance task state."""
    from .workflow import production_host_context
    if status.get('blockers') or 'production_context_version' not in packet:
        return None
    current = status['stage']
    stages = {
        'needs_parse': 'parse', 'needs_intent_assessment': 'direction',
        'needs_input': 'direction', 'needs_reference_selection': 'direction',
        'needs_observation': 'observe', 'needs_selection_assessment': 'observe',
        'needs_visual_research': 'research', 'needs_research': 'research',
        'needs_reference_expansion': 'research',
        'needs_design': 'design', 'needs_compile': 'model_compile',
    }
    if current == 'needs_visual_review':
        review_input = status['next_action'].get('review_input')
        return (production_host_context(packet, 'review', input_files=[review_input])
                if review_input else None)
    stage = stages.get(current)
    if stage is None:
        return None
    if current == 'needs_intent_assessment' and packet.get('need_mode') == 'offline_heuristic':
        stage = 'parse'
    return production_host_context(packet, stage)


def plan(run: Path, *, _dispatch_lock_held: bool = False) -> dict:
    """Read only; inspect_run is the sole authority for the next action."""
    from .run_status import inspect_run
    from .messages import build_presentation
    from .workflow import read_packet
    from .executors import catalog
    run = Path(run).resolve()
    status = inspect_run(run, _dispatch_lock_held=_dispatch_lock_held)
    presentation = build_presentation(run, inspection=status)
    packet = {}
    if not status['blockers']:
        packet = read_packet(run)
    action = deepcopy(status['next_action'])
    command = action.get('command')
    workflow = definition(packet.get('workflow_policy', {}).get('name'))
    stage_step = _STAGE_STEPS.get(status['stage'])
    steps = [step for step in workflow['steps'] if step['id'] == stage_step] if stage_step else [
        step for step in workflow['steps'] if command in step['commands']]
    local_stages = ('needs_preflight', 'needs_eval', 'needs_compare', 'ready_to_export', 'generation_failed')
    allowed = (not status['blockers'] and status.get('generation_control', {}).get('state') != 'stopped'
               and command in _LOCAL_COMMANDS
               and ((run / 'api-state.json').exists() or status['stage'] in local_stages))
    next_step = {**action, 'workflow_step': steps[0]['id'] if steps else None,
                 'owner': action.get('owner', steps[0]['owner'] if steps else 'host_model'),
                 'local_advance_available': allowed,
                 'submission_permitted_by_plan': False}
    context = _current_host_context(packet, status)
    methods = context.get('method_context') if context else None
    if packet and 'production_context_version' not in packet:
        from .method_context import build_method_context
        methods = build_method_context(packet, 'design')
    body = {'format': 'hex.harness-plan.v1', 'run': str(run), 'workflow': workflow,
            'source': {'packet_digest': packet.get('digest'), 'input_hashes': _input_hashes(run),
                       'policy': packet.get('visual_decision_policy', 'legacy'),
                       **({'workflow_policy': packet['workflow_policy']} if packet.get('workflow_policy') else {})},
            'current': status, 'next': next_step, 'executors': catalog(),
            'skills': {'context': methods, 'host_reading': 'not_verified',
                       'evidence_refs': ['production-context.json', 'creation-instructions.md']},
            'memory': {'scope': 'current_task', 'automatic_retrieval': 'disabled',
                       'confirmed_constraints': packet.get('need', {}).get('must_keep', []),
                       'evidence_refs': ['packet.json', 'user-feedback.json']},
            'repair': _repair_contract(run, status, packet),
            'evaluation': {'integrity': 'blocked' if status['blockers'] or presentation['diagnostic_details'] else 'verified_local_evidence',
                           **presentation['state'], 'visual_validation': status.get('visual_validation'),
                           'evidence_refs': presentation['evidence_refs'],
                           'diagnostics': presentation['diagnostic_details']},
            'notice': '流程计划不授权调用；本地推进只复用 resume，判断与生成分别在原入口执行。'}
    if context is not None:
        body['host_context'] = context
    return {**body, 'checkpoint': digest(body)}


def advance(run: Path, expected_checkpoint: str) -> dict:
    """Resume one existing local action. Never supply a model or call an executor."""
    before = plan(run)
    if expected_checkpoint != before['checkpoint']:
        raise AgentError('流程检查点已变化；重新读取计划，不能按旧决定继续')
    if not before['next']['local_advance_available']:
        return {'format': 'hex.harness-advance.v1', 'action': 'checkpoint',
                'requests_submitted': 0, 'plan': before}
    from .resume import resume_run
    def verify_locked():
        if plan(run, _dispatch_lock_held=True)['checkpoint'] != expected_checkpoint:
            raise AgentError('流程检查点在取得写入锁前变化；重新读取计划')
    result = resume_run(Path(run), provider=None, _before_local_action=verify_locked)
    return {'format': 'hex.harness-advance.v1',
            'action': result.get('action', 'local_recovery_prepared' if result.get('resumed') else 'checkpoint'),
            'requests_submitted': 0, 'recovery': result, 'plan': plan(run)}


def drive(run: Path, *, max_steps: int = 8) -> dict:
    """Advance bounded local work and return the next real host action.

    No arbitrary command execution, model dispatch, generation, guessed OCR
    applicability, or synthetic visual judgment is permitted in this loop.
    Every operation reloads and validates the existing task evidence.
    """
    from .run_status import inspect_run
    from .resume import resume_run
    from .workflow_policy import check, prepare_review, complete
    if type(max_steps) is not int or not 1 <= max_steps <= 32:
        raise AgentError('drive --max-steps 必须为 1 至 32；停止不推进的循环')
    run = Path(run).resolve()
    events, seen = [], set()
    reason = 'step_limit'
    for _ in range(max_steps):
        status = inspect_run(run)
        action = status['next_action']
        if status['blockers']:
            reason = 'blocked_or_stopped'
            break
        if status['stage'] == 'exported' and (not status.get('workflow_completion', {}).get('applicable')
                                             or status['workflow_completion']['complete']):
            reason = 'complete'
            break
        if status['stage'] == 'awaiting_result':
            reason = ('blocked_or_stopped' if action.get('action') == 'generation_stopped' else
                      'host_action_required' if action.get('executable') and
                      action.get('wait_for_existing_request') is False else 'wait_for_original_request')
            break
        if status.get('generation_control', {}).get('state') == 'stopped':
            # A generation stop preserves completed pixels and their local
            # checks. It does not resume native design/evaluation or submit.
            local_completion = status.get('workflow_completion', {}).get('applicable') and (
                (action.get('command') == 'workflow' and action.get('action') in (
                    'requirements', 'check', 'prepare-review', 'complete'))
                or (status['stage'] == 'needs_visual_review' and action.get('owner') == 'host_model'))
            if not local_completion:
                reason = 'blocked_or_stopped'
                break
        fingerprint = digest({'stage': status['stage'], 'action': action, 'inputs': _input_hashes(run)})
        if fingerprint in seen:
            reason = 'no_progress'
            break
        seen.add(fingerprint)
        if action.get('requires_user_input'):
            reason = 'requires_user_input'
            break
        if action.get('command') == 'workflow' and action.get('owner') == 'code' and action.get('executable'):
            operation = action.get('action')
            request_id = None
            argv = action.get('argv', [])
            if '--request-id' in argv:
                request_id = argv[argv.index('--request-id') + 1]
            if operation == 'check':
                result = check(run, request_id)
            elif operation == 'prepare-review':
                result = prepare_review(run, request_id)
            elif operation == 'complete':
                result = complete(run)
            else:
                reason = 'host_action_required'
                break
            events.append({'action': operation, 'evidence': result.get('evidence', result.get('review_input'))})
            continue
        # resume also restores an already recorded user grant. It never invents
        # authorization or submits a generation. Research reviews stay native.
        safe_stages = ('needs_preflight', 'needs_eval', 'needs_compare', 'ready_to_export', 'generation_failed')
        if ((action.get('command') in _LOCAL_COMMANDS and status['stage'] in safe_stages)
                or (status['stage'] == 'needs_authorization' and action.get('command') == 'resume')):
            recovered = resume_run(run, provider=None)
            events.append({'action': recovered.get('action', 'local_recovery'), 'stage': status['stage']})
            if status['stage'] in ('needs_eval', 'needs_compare'):
                reason = 'host_action_required'
                break
            continue
        reason = 'host_action_required'
        break
    status = inspect_run(run)
    if status['stage'] == 'exported' and (not status.get('workflow_completion', {}).get('applicable')
                                         or status['workflow_completion']['complete']):
        reason = 'complete'
    body = {'format': 'hex.workflow-drive.v1', 'run': str(run), 'reason': reason,
            'steps_executed': len(events), 'events': events, 'requests_submitted': 0,
            'status': status, 'next_action': status['next_action'],
            'continue_in_host': reason == 'host_action_required',
            'notice': '本地步骤已推进；宿主继续执行 next_action 的创作、观察或登记动作。只有完整证据与正式交付均通过才完成。'}
    if not status['blockers']:
        from .workflow import read_packet
        context = _current_host_context(read_packet(run), status)
        if context is not None:
            body['host_context'] = context
    return body


def case_record(run: Path, request_id: str | None = None) -> dict:
    """Derive one reproducible case from verified current-task evidence only.

    No cross-run scan, preference update, model call, source-file write, or
    inferred price. Historical failures keep their original request/candidate.
    """
    from . import __version__
    from .run_status import inspect_run
    from .workflow import read_packet
    from .executors import catalog
    run = Path(run).resolve()
    status = inspect_run(run)
    if status['blockers']:
        raise AgentError('案例证据未通过现有检查；先核对原任务，不能导入经验')
    packet = read_packet(run)
    route = 'api' if (run / 'api-state.json').exists() else 'host'
    snapshot_digest = None
    if route == 'api':
        from .api_recovery import load_api_state, _verified
        ledger = load_api_state(run)
        rows = [row for row in ledger['requests'] if row['kind'] == 'image']
    else:
        from .host import read_dispatches
        rows = read_dispatches(run)
    if request_id is None:
        if len(rows) != 1:
            raise AgentError('案例须明确--request-id；不自动选取多次调用中的最后一次')
        request_id = rows[0]['id'] if route == 'api' else rows[0]['request']['id']
    matches = [(index, row) for index, row in enumerate(rows)
               if (row['id'] if route == 'api' else row['request']['id']) == request_id]
    if len(matches) != 1:
        raise AgentError('案例request-id必须对应唯一的原生图请求')
    index, row = matches[0]
    timing = {'seconds': None, 'scope': 'not_recorded', 'authority': 'not_available'}
    if route == 'api':
        snapshot = _verified(within(run, row['snapshot']))
        snapshot_digest = snapshot['digest']
        request = snapshot['request']
        target, tool = 'image', None  # the existing API visual route always generates image
        spec = snapshot['generation'].get('generation_spec') or {}
        requested_model = snapshot['generation']['model_id']
        state = row['state']
        request_digest = digest(request)
        executor = {'id': ledger['provider_adapter'], 'execution': 'durable_api', 'readiness': 'not_probed'}
        timing = {'seconds': row['event'].get('elapsed_seconds'), 'scope': 'recorded_generate_wall_time',
                  'authority': 'local_reservation_ledger'}
    else:
        request = row['request']
        target, tool = request['target'], request['tool']
        spec = request.get('generation_spec') or {}
        requested_model = spec.get('model_id')
        state, request_digest = row['status'], row['request_digest']
        executor = next((entry for entry in catalog() if tool in entry['tools']),
                        {'id': 'external_host', 'execution': 'host_reported', 'readiness': 'not_probed'})
        for family in ('comfy', 'pippit'):
            path = within(run, family + '/' + request_id + '/execution.json')
            if path.is_file():
                saved = read_json(path)
                seconds = saved.get('submission_seconds') if family == 'comfy' else saved.get('diagnostic', {}).get('duration')
                if type(seconds) in (int, float) and seconds >= 0:
                    timing = {'seconds': seconds, 'scope': 'submit_wall_time' if family == 'comfy' else 'last_cli_operation',
                              'authority': 'local_executor_report', 'evidence_ref': str(path.relative_to(run))}
    output = row.get('output')
    candidate = None
    generation = None
    if state == 'completed' and output:
        image = within(run, output['file']).read_bytes()
        from . import generation_evidence
        contract = read_json(run / 'acceptance.json')
        generation = (generation_evidence.for_api(run, packet, contract, image, request_id) if route == 'api'
                      else generation_evidence.for_host(run, packet, contract, image))
        candidate = {'file': output['file'], 'sha256': generation['candidate_sha256'],
                     'generation_evidence_digest': generation['digest']}
    trials_name = 'image-tests.json' if route == 'api' else 'host-image-tests.json'
    trials = read_json(run / trials_name) if (run / trials_name).exists() else []
    trial = next((trial for trial in trials if trial.get('request_id') == request_id
                  or trial.get('generation_evidence', {}).get('request_id') == request_id), None)
    review = trial.get('review') if trial else None
    workflow_review = None
    if candidate and packet.get('workflow_policy'):
        from .workflow_policy import registered_review
        from .visual_decision import record_only
        workflow_review = registered_review(run, request_id)
        if review is not None and not record_only(packet):
            # Explicit research profiles retain their native first review.
            # Validate any sidecar above, but do not let it erase that review.
            workflow_review = None
        if workflow_review is not None:
            review = workflow_review['evidence']['report']['review']
    failure = {'layer': 'unknown', 'authority': 'execution_status_only', 'review_digest': None,
               'signal': state if state != 'completed' else 'failure_not_observed'}
    if review:
        failure.update(layer=review['diagnosis'] if review['decision'] == 'revise' else 'unknown',
                       authority='reviewer_reported_not_causally_proven', review_digest=digest(review),
                       signal=review['decision'], changes=review.get('changes', []), preserve=review.get('preserve', []))
        if workflow_review is not None:
            # First-pass visual observations can show a gap, not locate its
            # cause. Keep an authored diagnosis as a hypothesis for phase two.
            failure.update(layer='unknown', reported_hypothesis=review['diagnosis'],
                           causal_attribution='not_proven',
                           review_evidence_file=workflow_review['evidence_file'],
                           review_evidence_digest=workflow_review['evidence']['digest'])
    human_acceptance = 'not_recorded'
    if (run / 'user-feedback.json').exists() and candidate:
        from .feedback import verified_feedback
        _, selected, feedback = verified_feedback(run)
        if selected['sha256'] == candidate['sha256']:
            human_acceptance = feedback['decision']
            if feedback['decision'] == 'rejected_by_user':
                failure.update(layer='unknown', authority='user_decision_without_causal_inference',
                               signal='rejected_by_user', user_quote=feedback['user_quote'],
                               feedback_digest=digest(feedback))
    profile = packet.get('model_profiles', {}).get(target) or {}
    source = Path(__file__).resolve().parent
    modules = ('harness.py', 'executors.py', 'generation_authorization.py', 'generation_permission.py',
               'generation_evidence.py', 'host.py', 'api_recovery.py', 'comfy_execution.py', 'pippit_execution.py')
    body = {'format': 'hex.workflow-case.v1', 'route': route,
            'task': {'run': str(run), 'packet_digest': packet['digest'],
                     'contract_digest': read_json(run / 'acceptance.json')['digest'],
                     'policy': packet.get('visual_decision_policy', 'legacy')},
            'versions': {'package': __version__, 'workflow_digest': definition()['definition_digest'],
                         'code_sha256': {name: sha((source / name).read_bytes()) for name in modules}},
            'model': {'target': target, 'tool': tool, 'requested_model_id': requested_model,
                      'actual_vendor_model': 'not_available', 'profile_version': profile.get('version'),
                      'provider_version': profile.get('provider_version'), 'profile_digest': digest(profile) if profile else None,
                      'capability_snapshot_id': (request.get('capability_snapshot') or {}).get('snapshot_id')},
            'executor': executor, 'attempt': {'request_id': request_id, 'ordinal': index + 1,
                'request_digest': request_digest, 'snapshot_digest': snapshot_digest, 'state': state},
            'candidate': candidate, 'failure': failure,
            'evaluation': {'visual_quality': 'not_verified', 'reviewer_image_access':
                (review.get('holistic') or {}).get('image_access', 'not_recorded') if review else 'not_recorded',
                'human_acceptance': human_acceptance, 'status_inspection': 'local_evidence_only'},
            'budget': deepcopy(status['budget']), 'cost': {'amount': status['budget'].get('cost'),
                'currency': None, 'status': status['budget'].get('cost_status', 'not_available')},
            'timing': timing, 'evidence_sha256': _input_hashes(run),
            'learning': {'scope': 'current_task_case', 'preference_update': False, 'automatic_retrieval': False,
                         'interpretation_boundary': '整图接受或否决不是技法因果；失败层是来源声明，不自动变为通用审美规则。'}}
    return {**body, 'digest': digest(body)}
