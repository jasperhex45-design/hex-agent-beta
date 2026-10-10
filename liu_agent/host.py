"""Codex/file-host bridge. Local receipts do not prove remote tool execution."""
from pathlib import Path
from .contracts import Solution, ReferenceAnalysis, ImageEval, PairComparison
from .io import AgentError, read_json, write_json, normalize_image, within, sha, digest
from .workflow import read_packet, validate_solution, freeze_contract, attach_observation, eval_input, finish
from .design import validate_eval
from .revision import revision_prompt, select_pair
from .workflow import compile_texts, generation_references
from .run_lock import dispatch_lock
import uuid
import json


def read_dispatches(run: Path) -> list[dict]:
    path = run/'host-dispatches.json'
    attempts = read_json(path) if path.exists() else []
    if not isinstance(attempts, list) or any(not isinstance(a, dict) for a in attempts):
        raise AgentError('生成前调用账本结构无效；不能推断剩余预算')
    for attempt in attempts:
        if digest(attempt['request']) != attempt['request_digest']:
            raise AgentError('生成前记录被改写；不能伪装原始调用')
    marker = run/'dispatch.json'
    if marker.exists():
        last = read_json(marker)
        if (not isinstance(last, dict) or not isinstance(last.get('request'), dict)
                or not any(a['request'] == last['request'] and
                           a['request_digest'] == last.get('request_digest') for a in attempts)):
            raise AgentError('存在未被调用账本覆盖的dispatch记录；不能将未知调用或预算重置为零')
    return attempts


def validate_receipt(run: Path, attempt: dict) -> None:
    request = attempt['request']
    receipt = attempt.get('actual_arguments') or {}
    if (receipt.get('request_id') != request['id'] or
            receipt.get('digest') != digest(request['arguments']) or not receipt.get('file')):
        raise AgentError('实际工具参数receipt缺失或与dispatch不一致；旧任务只作历史记录')
    raw = within(run, receipt['file']).read_bytes()
    actual = json.loads(raw.decode('utf-8-sig'))
    if isinstance(actual, dict) and 'arguments' in actual:
        if actual.get('request_id') not in (None, request['id']):
            raise AgentError('实际工具参数request_id不一致')
        actual = actual['arguments']
    if sha(raw) != receipt.get('file_sha256') or actual != request['arguments']:
        raise AgentError('实际工具参数文件已改变，不能验收')


def dispatch(run: Path, target: str, tool: str, *, provider_options: dict | None = None):
    if not tool.strip():
        raise AgentError('记录现场实际可用的工具名称')
    from .generation_spec import validate_tool_target
    validate_tool_target(tool, target)
    from .comfy_connector import TOOL_TARGETS
    if provider_options is not None and tool not in {'pippit.generate-image.seedream-5-pro', *TOOL_TARGETS}:
        raise AgentError('provider_options 仅用于已接入的小云雀或本地 ComfyUI 连接器')
    from .native_budget import dispatch_budget_lock
    with dispatch_budget_lock(run) as shared_budget:
        from .generation_authorization import require_not_stopped
        require_not_stopped(run, shared_budget)
        if any((run/name).exists() for name in ('api-state.json', 'calls.json', 'image-tests.json', 'api')):
            raise AgentError('此目录已有API调用证据；不能切换宿主入口另起预算，请按原API记录恢复')
        attempts = read_dispatches(run)
        if shared_budget and shared_budget['dispatch_remaining_images'] <= 0:
            raise AgentError('原有预算已转出或父子累计次数已用完；不能另起额度')
        if any(a['status'] in ('pending', 'outcome_unknown') for a in attempts):
            raise AgentError('上次调用尚未确定结果，不能重复提交；核对同一request-id')
        if len(attempts) >= 2:
            raise AgentError('两次调用预算已用完，失败也计数')
        trials = read_json(run/'host-image-tests.json') if (run/'host-image-tests.json').exists() else []
        completed = [a for a in attempts if a['status'] == 'completed']
        from .visual_decision import record_only
        packet = read_packet(run)
        from .workflow_policy import require_image_deliverable
        require_image_deliverable(packet)
        from .user_feedback_context import require_current
        require_current(packet, stage='dispatch')
        comparison = packet['brief'].get('model_comparison', False)
        if comparison:
            from .contracts import Brief
            Brief.model_validate(packet['brief'])
            if not record_only(packet) or packet.get('requirement_parent'):
                raise AgentError('模型比较仅支持新的生产创建任务')
            if target not in packet['brief']['targets']:
                raise AgentError('比较目标不在冻结需求中')
            if any(a['request']['target'] == target for a in attempts):
                raise AgentError('此模型首图已经尝试；失败也占额度，不允许重抽')
            # Each target has one primary image; another target is not a revision.
            completed = []
        if completed and record_only(packet):
            prefix = ('首图已返回，按 workflow status 完成检查与复核后交付；' if packet.get('workflow_policy')
                      else '首图已返回，先交由用户接受或拒绝；')
            raise AgentError(prefix + '机器复查不能授权补图，明确编辑需求须建立关联版本')
        if completed:
            prior = completed[-1]
            trial = next((t for t in trials if t.get('request_id') == prior['request']['id']), None)
            if not trial:
                raise AgentError('先实际看图并登记上一候选的Eval，再决定是否修订')
            review = trial['review']
            if review['decision'] == 'uncertain':
                raise AgentError('上一候选复查为uncertain；先核对不确定原因，不能直接重画或占用修订额度')
            if review['diagnosis'] in ('intent', 'reference'):
                raise AgentError('需求或参考变化需新任务，不能在原标准下假装微调')
        packet = read_packet(run)
        if packet.get('model_adaptation_version') == 1:
            from .generation_spec import host_capabilities
            if packet.get('compilation_runtimes', {}).get(target) != host_capabilities(tool):
                raise AgentError('实际工具与编译时能力不同或未登记；先prepare-compilation --target/--tool并重新编译')
        if completed:
            from .workflow import validate_trial
            validate_trial(packet, run, read_json(run/'acceptance.json'), trial)
            validate_trial_dispatch(packet, run, trial, attempts)
        if packet.get('requirement_parent') and (not shared_budget or
                shared_budget['budget_source'] != packet['requirement_parent']['run']):
            raise AgentError('关联需求版本尚未核验原预算转接；不能把新目录作为新额度')
        from .provenance import validate_need
        validate_need(packet)
        from .generation_authorization import read_authorization
        from .generation_permission import require_image_permission
        authorization = read_authorization(run, packet)
        authorized_max = authorization['max_images']
        if shared_budget and not shared_budget.get('explicit_revision_grant'):
            owner = Path(shared_budget['budget_source'])
            authorized_max = min(authorized_max, read_authorization(owner, read_packet(owner))['max_images'])
        decision = None
        if (run/'user-feedback.json').exists():
            from .feedback import verified_feedback
            decision = verified_feedback(run)[2]['decision']
        permission_used = len(attempts)
        if shared_budget:
            permission_used = (shared_budget['grant_attempted_images'] if shared_budget.get('explicit_revision_grant')
                               else shared_budget['attempted_images'])
        require_image_permission(max_images=authorized_max,
                                 attempted_images=permission_used,
                                 review=review if completed else None, user_decision=decision)
        solution = Solution.model_validate(read_json(run/'solution.json'))
        adaptive = packet.get('model_adaptation_version') == 1
        if adaptive:
            from .model_adaptation import require_revision, revision_context
            expected = revision_context(prior['output']['sha256'], 'edit_target', review) if completed else None
            require_revision(solution.compilation, expected)
        contract = freeze_contract(run, packet, solution)
        from .execution_request import build_input
        prepared = build_input(run, packet, solution, target, tool,
                               provider_options=provider_options,
                               prior=prior if completed else None, review=review if completed else None)
        from .car_photography import consume_frozen_input
        prepared = consume_frozen_input(run, packet, solution, target, tool, prepared)
        request = {'id': uuid.uuid4().hex, 'target': target, 'tool': tool,
                   'authorization_digest': authorization['digest'],
                   'packet_digest': packet['digest'], 'contract_digest': contract['digest'],
                   'solution': solution.model_dump(), **prepared}
        attempt = {'request': request, 'request_digest': digest(request), 'status': 'pending',
                   'origin': 'host_reported_not_api_verified'}
        attempts.append(attempt)
        write_json(run/'host-dispatches.json', attempts)
        from .io import atomic_text
        atomic_text(run/'dispatch-prompt.txt', request['prompt'], newline='')
        write_json(run/'dispatch.json', attempt)
        return attempt


def record_result(run: Path, request_id: str, status: str, image_path: Path | None, tool_note: str,
                  actual_arguments_path: Path | None = None):
    if status not in ('completed', 'failed', 'outcome_unknown') or not tool_note.strip():
        raise AgentError('结果须说明completed/failed/outcome_unknown及实际工具记录')
    with dispatch_lock(run):
        attempts = read_dispatches(run)
        attempt = next((a for a in attempts if a['request']['id'] == request_id), None)
        if attempt is None or attempt['status'] not in ('pending', 'outcome_unknown'):
            raise AgentError('调用不存在或已记录确定结果，不能覆盖')
        if status == 'completed':
            if image_path is None:
                raise AgentError('completed必须有实际图片')
            if actual_arguments_path is None or not actual_arguments_path.exists():
                raise AgentError('completed必须提供实际工具参数JSON（--actual-arguments），不能只凭图片登记')
            raw_args = actual_arguments_path.read_bytes()
            try:
                actual = json.loads(raw_args.decode('utf-8-sig'))
            except Exception as exc:
                raise AgentError('实际工具参数必须是有效JSON') from exc
            if isinstance(actual, dict) and 'arguments' in actual:
                if actual.get('request_id') not in (None, request_id):
                    raise AgentError('实际工具参数中的request_id与请求不一致')
                actual = actual['arguments']
            if actual != attempt['request']['arguments']:
                raise AgentError('实际工具参数与dispatch快照不一致，不能登记completed')
            attempt['actual_arguments'] = {'file_sha256': sha(raw_args),
                                           'digest': digest(actual),
                                           'request_id': request_id,
                                           'origin': 'host_reported_not_api_verified'}
            usage = dict(attempt['request'].get('reference_usage', {}))
            usage['submission_evidence'] = 'host_reported_not_api_verified'
            attempt['reference_usage'] = usage
            raw = image_path.read_bytes(); image = normalize_image(raw)
            packet = read_packet(run)
            if any(sha(image) == r['sha256'] for r in packet['references']):
                raise AgentError('参考原图不能登记为新生成结果')
            if any(a.get('output', {}).get('sha256') == sha(image) for a in attempts):
                raise AgentError('相同输出不能登记为另一次生成')
            for ref in attempt['request']['images_in_order']:
                if sha(within(run, ref['file']).read_bytes()) != ref['sha256']:
                    raise AgentError('调用期间生成参考已改变，无法确认实际输入')
            args_rel = f'tests/actual-arguments-{request_id}.json'
            args_file = within(run, args_rel); args_file.parent.mkdir(exist_ok=True)
            args_file.write_bytes(raw_args)
            attempt['actual_arguments']['file'] = args_rel
            rel = f'tests/generated-{request_id}.png'
            path = within(run, rel); path.parent.mkdir(exist_ok=True); path.write_bytes(image)
            attempt['output'] = {'file': rel, 'sha256': sha(image), 'original_sha256': sha(raw)}
            if attempt['request'].get('generation_spec') is not None:
                from .generation_spec import GenerationSpec, image_facts, verify_returned_parameters
                returned = image_facts(raw)
                saved = image_facts(image)
                attempt['generation_parameters'] = {
                    'requested': attempt['request']['generation_spec'],
                    'actually_sent': actual,
                    'returned': returned, 'saved': saved,
                    'controls': attempt['request']['capability_snapshot']['parameters'],
                    'source': 'host_reported_not_api_verified',
                    'returned_verification_version': 1,
                    'verified': verify_returned_parameters(
                        GenerationSpec.model_validate(attempt['request']['generation_spec']), returned, {},
                        display_facts=saved),
                }
        elif image_path is not None:
            raise AgentError('非completed不能附候选图片')
        attempt.update(status=status, tool_note=tool_note)
        write_json(run/'host-dispatches.json', attempts)
        return attempt


def observe(run: Path, report_path: Path, tool_note: str):
    if not tool_note.strip():
        raise AgentError('请记录实际看图工具/操作；不能只说已看图')
    packet = read_packet(run)
    if (run/'host-image-tests.json').exists():
        raise AgentError('已有候选，重新观察/换参考请开新任务，避免重写验收依据')
    analysis = ReferenceAnalysis.model_validate(read_json(report_path))
    return attach_observation(packet, run, analysis, 'host_reported_not_api_verified', tool_note)


def preflight(run: Path, solution_path: Path):
    with dispatch_lock(run):
        return _preflight_locked(run, solution_path)


def _validate_compilation_output_options(packet, design, runtime):
    if runtime is None or runtime.get('status') == 'legacy_adapter_without_capabilities':
        return  # No exposed capabilities; dispatch still requires its own check.
    from .generation_spec import validate_output_options
    from .workflow import generation_settings
    settings = generation_settings(packet, design)
    try:
        validate_output_options(quality=settings['quality'], output_format=settings['output_format'],
                                capabilities=runtime)
    except AgentError as exc:
        raise AgentError(f'{exc}；请修正生效的 generation_options（brief 优先于 design），再准备编译') from exc


def prepare_compilation(run: Path, plan_path: Path, target: str = 'image', tool: str | None = None):
    """Prepare model-aware input locally; the host supplies the intelligent prose."""
    from .contracts import Plan, Compilation
    from .workflow import compilation_input, ensure_references_mutable, save_packet, validate_spec
    from .design import validate_design
    from .generation_spec import host_capabilities, validate_tool_target
    with dispatch_lock(run):
        packet = read_packet(run)
        if packet['brief'].get('model_comparison') and read_dispatches(run):
            raise AgentError('模型比较已开始；沿用首次调用前冻结的两份正文，不再编译，'
                             '也不将第一张反馈用于第二个模型。未完成调用仅查询原请求。')
        from .user_feedback_context import require_current
        require_current(packet, stage='compile')
        plan = Plan.model_validate(read_json(plan_path))
        if target not in packet['model_profiles']:
            raise AgentError('没有目标模型档案：' + target)
        runtime = packet.get('compilation_runtimes', {}).get(target)
        if tool is not None:
            validate_tool_target(tool, target)
            runtime = host_capabilities(tool)
        _validate_compilation_output_options(packet, plan.design, runtime)
        if tool is not None:
            previous = packet.get('compilation_runtimes', {}).get(target)
            if previous != runtime:
                ensure_references_mutable(run)
                packet.setdefault('compilation_runtimes', {})[target] = runtime
                packet = save_packet(packet, run)
        validate_spec(packet, plan.spec)
        validate_design(packet, plan.spec, plan.design)
        revision = None
        if packet.get('model_adaptation_version') == 1:
            completed = [a for a in read_dispatches(run) if a['status'] == 'completed']
            if completed:
                prior = completed[-1]
                trials = read_json(run/'host-image-tests.json') if (run/'host-image-tests.json').exists() else []
                trial = next((t for t in trials if t.get('request_id') == prior['request']['id']), None)
                if not trial:
                    raise AgentError('先登记上一候选的实际看图Eval，再编译返修提示词')
                from .workflow import validate_trial
                from .model_adaptation import revision_context
                validate_trial(packet, run, read_json(run/'acceptance.json'), trial)
                validate_trial_dispatch(packet, run, trial, read_dispatches(run))
                revision = revision_context(prior['output']['sha256'], 'edit_target', trial['review'])
        payload = compilation_input(packet, plan.spec, plan.design, revision=revision)
        write_json(run/'compilation-input.json', payload)
        write_json(run/'compilation.schema.json', Compilation.model_json_schema())
        return {'packet_digest': packet['digest'], 'compilation_input': str(run/'compilation-input.json'),
                'schema': str(run/'compilation.schema.json'),
                'next': '宿主读取当前编译输入，为每个模型写完整正文和对应适配依据；无需用户选择写法。'}


def _preflight_locked(run: Path, solution_path: Path):
    """Shared implementation for callers already holding this run's dispatch lock."""
    from .native_budget import has_transfer
    if has_transfer(run):
        raise AgentError('已有或未完成的预算关联；不能重新preflight覆盖关联标记')
    packet = read_packet(run)
    solution = Solution.model_validate(read_json(solution_path))
    report = validate_solution(packet, solution)
    for target in packet['brief']['targets']:
        _validate_compilation_output_options(
            packet, solution.design, packet.get('compilation_runtimes', {}).get(target))
    contract = freeze_contract(run, packet, solution)
    write_json(run/'solution.json', solution.model_dump())
    write_json(run/'preflight.json', report)
    return {**report, 'contract_digest': contract['digest']}


def prepare_eval(run: Path, image_path: Path):
    packet = read_packet(run)
    contract = read_json(run/'acceptance.json')
    image = normalize_image(image_path.read_bytes())
    from .generation_evidence import for_host
    evidence = for_host(run, packet, contract, image)
    solution = Solution.model_validate(evidence['solution'])
    calls = read_dispatches(run)
    matches = [a for a in calls if a['status'] == 'completed' and a.get('output', {}).get('sha256') == sha(image)]
    if len(matches) != 1:
        raise AgentError('候选图未对应completed dispatch输出；不能登记未注册Eval图')
    validate_receipt(run, matches[0])
    rel = 'tests/review-input.png'
    path = within(run, rel); path.parent.mkdir(exist_ok=True); path.write_bytes(image)
    payload = eval_input(packet, contract, image, generation_evidence=evidence)
    payload['candidate_file'] = rel
    payload['solution_digest'] = digest(solution.model_dump())
    write_json(run/'eval-input.json', payload)
    write_json(run/'eval.schema.json', ImageEval.model_json_schema())
    if contract.get('quality_contract_version') == 1:
        write_json(run/'diagnosis-input.json', {'format': 'hex.diagnosis-availability.v1',
                   'state': 'waiting_first_review', 'request_id': evidence['request_id'],
                   'generation_evidence_digest': evidence['digest'],
                   'next': '登记隔离首评后，python -m hex_agent.failure_diagnosis prepare --run RUN --request-id ID --out OUTSIDE_RUN.json',
                   'notice': '此处只保存绑定，不提前导出作者方案；第二阶段不覆盖首评。'})
    return {'eval_input': str(run/'eval-input.json'), 'candidate_sha256': sha(image),
            'next': ('使用隔离上下文，仅提供eval-input.json、eval.schema.json及列出的真实图片。先整体后局部；首次评审完成前不读diagnosis-input.json、原方案或旧评价。'
                     if contract.get('quality_contract_version') == 1 else
                     '实际打开其中列出的参考图和候选图，再写符合eval.schema.json的评估；不得只读提示词')}


def record_test(run: Path, image_path: Path, review_path: Path, prompt_path: Path, model_label: str, tool_note: str, target: str = "image"):
    from .io import digest
    packet = read_packet(run)
    contract = read_json(run/'acceptance.json')
    review = ImageEval.model_validate(read_json(review_path)); validate_eval(contract, review)
    if target not in packet['model_profiles']:
        raise AgentError('未知测试模型档案；实际模型ID和档案标签分开记录')
    if not tool_note.strip() or not model_label.strip():
        raise AgentError('必须记录实际工具和模型标签；未知版本请明确未知')
    image = normalize_image(image_path.read_bytes())
    from .generation_evidence import for_host
    from .design import validate_diagnosis
    evidence = for_host(run, packet, contract, image)
    solution = Solution.model_validate(evidence['solution'])
    validate_diagnosis(review, evidence)
    if any(sha(image) == r['sha256'] for r in packet['references']):
        raise AgentError('不能把参考原图作为新生成的候选')
    payload = read_json(run/'eval-input.json')
    expected_payload = eval_input(packet, contract, image, generation_evidence=evidence)
    if contract.get('quality_contract_version') == 1 and set(payload) != set(expected_payload) | {'candidate_file', 'solution_digest'}:
        raise AgentError('首次复查输入只能包含冻结白名单；不能追加作者方案或旧评价')
    if any(payload.get(key) != value for key, value in expected_payload.items()):
        raise AgentError('复查输入的原话、标准、参考清单或生成证据已变化；重新prepare-eval并看图')
    if contract.get('quality_contract_version') != 1 and payload.get('generation_evidence') != evidence:
        raise AgentError('复查所依据的生成证据与真实调用不一致；重新prepare-eval')
    if payload['contract']['digest'] != contract['digest'] or payload['images_in_order'][-1]['sha256'] != sha(image):
        raise AgentError('Eval输入对应旧图或旧标准；请重新prepare-eval并看图')
    if payload['solution_digest'] != digest(solution.model_dump()):
        raise AgentError('Eval输入与当前生成方案不一致')
    path = run/'host-image-tests.json'; trials = read_json(path) if path.exists() else []
    from .visual_decision import record_only
    existing = next((t for t in trials if t['sha256'] == sha(image)), None)
    upgrade = record_only(packet) and existing is not None and existing.get('review') is None
    if len(trials) >= 2 and not upgrade:
        raise AgentError('本任务最多记录两轮，后续另建任务；不无限抽图')
    if existing is not None and not upgrade:
        raise AgentError('相同候选不能重复计为新一轮')
    actual_prompt = prompt_path.read_text(encoding='utf-8-sig').strip()
    if not actual_prompt:
        raise AgentError('实际使用的提示词不能为空')
    matches = [a for a in read_dispatches(run) if a['status'] == 'completed'
               and a.get('output', {}).get('sha256') == sha(image)]
    if len(matches) != 1:
        raise AgentError('缺少生成前dispatch和对应record-result，不能事后补造新版流程实测')
    call = matches[0]; request = call['request']
    validate_receipt(run, call)
    if (request['solution'] != solution.model_dump() or request['prompt'].strip() != actual_prompt
            or request['target'] != target or request['contract_digest'] != contract['digest']):
        raise AgentError('候选、实际提示词或方案与生成前记录不一致')
    n = existing['round'] if upgrade else len(trials)+1
    rel = existing['file'] if upgrade else f'tests/host-round-{n}.png'
    within(run, rel).write_bytes(image)
    record = {'round': n, 'file': rel, 'sha256': sha(image), 'model_id': model_label, 'target': target,
              'origin': 'host_reported_not_api_verified', 'tool_note': tool_note,
              'solution': solution.model_dump(), 'spec': solution.spec.model_dump(),
              'contract_digest': contract['digest'], 'prompt': actual_prompt,
              'review': review.model_dump(),
              'generation_evidence': evidence,
              'request_id': request['id'], 'request_digest': call['request_digest'],
              'eval_input_images': [{'id': x['id'], 'sha256': x['sha256']} for x in payload['images_in_order']],
              'generation_inputs': request.get('generation_inputs', []),
              'reference_usage': call.get('reference_usage', request.get('reference_usage', {})),
              'review_authority': 'machine_reviewed_untrusted'}
    if upgrade:
        from .workflow import validate_trial
        validate_trial(packet, run, contract, existing)
        validate_trial_dispatch(packet, run, existing)
        trials[trials.index(existing)] = record
    else:
        trials.append(record)
    write_json(path, trials)
    if upgrade and (run/'result.json').exists():
        finalize(run, run/'solution.json')
    return {'recorded_round': n, 'review': review.decision,
            'notice': '图像文件与观察材料已绑定；工具调用来源为宿主报告，非本程序API执行证明'}


def choose(run: Path, report_path: Path):
    from .visual_decision import record_only
    if record_only(read_packet(run)):
        raise AgentError('生产策略的机器评审只记录，不能决定候选排序；请对原图记录用户选择')
    trials = read_json(run/'host-image-tests.json')
    if len(trials) != 2:
        raise AgentError('成对比较需要恰好两张候选')
    report = PairComparison.model_validate(read_json(report_path))
    index, raw = select_pair(report, trials)
    selected = {'selected_index': index, 'comparison': raw,
                'candidate_hashes': [t['sha256'] for t in trials]}
    write_json(run/'selection.json', selected)
    return selected


def validate_trial_dispatch(packet: dict, run: Path, trial: dict, calls: list[dict] | None = None) -> None:
    """Bind a trial to the original dispatch, actual arguments and image inputs."""
    calls = read_dispatches(run) if calls is None else calls
    matching = next((a for a in calls if a['request']['id'] == trial.get('request_id')), None)
    if (matching is None or matching['status'] != 'completed' or
            matching['request_digest'] != trial.get('request_digest') or
            matching['output']['sha256'] != trial['sha256'] or
            matching['request']['solution'] != trial['solution']):
        raise AgentError('试图记录与生成前快照不一致')
    validate_receipt(run, matching)
    if trial.get('generation_inputs') != matching['request'].get('generation_inputs', []):
        raise AgentError('试图的generation_inputs与生成请求不一致')
    from .reference_gate import validate_generation_inputs
    validate_generation_inputs(packet, list(matching['request'].get('solution', {}).get('design', {}).get('baseline_reference_ids', [])),
                               matching['request'].get('generation_inputs', []))


def production_trials(packet: dict, run: Path, calls: list[dict], trials: list[dict]) -> list[dict]:
    """Project verified completed dispatches into candidates without requiring Eval."""
    from .generation_evidence import for_host
    from .workflow import validate_trial
    contract = read_json(run/'acceptance.json')
    result = list(trials)
    for trial in result:
        validate_trial(packet, run, contract, trial)
        validate_trial_dispatch(packet, run, trial, calls)
    for call in calls:
        if call['status'] != 'completed':
            continue
        request, output = call['request'], call['output']
        if any(t.get('request_id') == request['id'] for t in result):
            continue
        evidence = for_host(run, packet, contract, within(run, output['file']).read_bytes())
        result.append({'round': len(result)+1, 'file': output['file'], 'sha256': output['sha256'],
                       'model_id': 'unknown', 'target': request['target'],
                       'origin': 'host_reported_not_api_verified', 'tool_note': call.get('tool_note', ''),
                       'solution': request['solution'], 'spec': request['solution']['spec'],
                       'contract_digest': contract['digest'], 'prompt': request['prompt'],
                       'review': None, 'review_authority': 'not_performed', 'eval_input_images': [],
                       'generation_evidence': evidence, 'request_id': request['id'],
                       'request_digest': call['request_digest'], 'generation_inputs': request['generation_inputs'],
                       'reference_usage': call.get('reference_usage', request.get('reference_usage', {}))})
    limit = len(packet['brief']['targets']) if packet['brief'].get('model_comparison') else 1
    if len(result) > limit or len({t['target'] for t in result}) != len(result):
        raise AgentError('生产首图策略只接受一张候选；不得以机器评审筛选多张结果')
    return result


def finalize(run: Path, solution_path: Path):
    packet = read_packet(run)
    if packet.get('workflow_policy') and any((run / name).exists() for name in ('api-state.json', 'calls.json', 'image-tests.json', 'api')):
        raise AgentError('此完整流程任务属于 API 路线；使用 workflow complete / resume，不走宿主 finalize')
    solution = Solution.model_validate(read_json(solution_path))
    path = run/'host-image-tests.json'
    trials = read_json(path) if path.exists() else []
    calls = read_dispatches(run)
    from .visual_decision import record_only
    if record_only(packet):
        freeze_contract(run, packet, solution)
        trials = production_trials(packet, run, calls, trials)
    for trial in trials:
        validate_trial_dispatch(packet, run, trial, calls)
    if any(a['status'] in ('pending', 'outcome_unknown') for a in calls):
        raise AgentError('调用结果未确定，不能将任务导出为完成')
    if any(a['status'] == 'completed' and not any(t.get('request_id') == a['request']['id'] for t in trials) for a in calls):
        raise AgentError('还有候选未完成实际看图Eval，不能遗漏后交付')
    if record_only(packet):
        if trials and (not path.exists() or read_json(path) != trials):
            write_json(path, trials)
        return finish(run, solution, 'codex_or_external_host', trials)
    selection = read_json(run/'selection.json') if (run/'selection.json').exists() else {}
    if selection and selection.get('candidate_hashes') != [t['sha256'] for t in trials]:
        raise AgentError('成对比较绑定旧候选，不能复用')
    return finish(run, solution, 'codex_or_external_host', trials,
                  selection.get('selected_index'), selection.get('comparison'))
