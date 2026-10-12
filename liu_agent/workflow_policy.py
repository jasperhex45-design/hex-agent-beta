"""Versioned completion requirements over the existing HEX evidence kernel.

No model is called here. Local checks execute real checks; visual judgments must
be supplied by the host and remain distinct from explicit user acceptance.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from .io import AgentError, digest, read_json, sha, within, write_json

PROFILE = 'full-flow-v1'
KINDS = ('prompt_only', 'image', 'local_edit')
_ERRORS = (AgentError, OSError, ValueError, TypeError, KeyError, AttributeError)


def build_policy(name='legacy', deliverable_kind=None, *, scope='create', delivery_level=None):
    if name == 'legacy':
        if deliverable_kind is not None:
            raise AgentError('deliverable-kind 需要 full-flow-v1，不能给旧策略静默添加交付要求')
        return None
    if name != PROFILE:
        raise AgentError('未知 workflow-profile；不能静默降级')
    kind = deliverable_kind or ('image' if scope == 'create' else 'local_edit')
    if kind not in KINDS or (kind == 'local_edit' and scope == 'create'):
        raise AgentError('交付类型无效；local_edit 必须使用真实编辑范围与输入')
    stages = ['intent', 'references', 'design', 'compile', 'verify']
    if kind != 'prompt_only':
        stages += ['execute', 'check_requirements', 'image_check', 'visual_review']
    value = {'version': 1, 'name': PROFILE, 'deliverable_kind': kind,
             'required_stages': stages + ['deliver']}
    if delivery_level is not None:
        if delivery_level not in ('exploratory', 'formal', 'high_risk'):
            raise AgentError('未知交付级别；不能静默降级')
        if delivery_level == 'exploratory' and scope != 'create':
            raise AgentError('严格修改范围任务不能降为探索自检；使用 formal 或 high_risk')
        value.update(version=2, delivery_level=delivery_level)
    return value


def policy(packet):
    value = packet.get('workflow_policy')
    if value is None:
        return None
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value.get('version') not in (1, 2) or value.get('name') != PROFILE):
        raise AgentError('未知或损坏的完整流程策略；请按原版本恢复')
    level = value.get('delivery_level') if value['version'] == 2 else None
    brief = packet.get('brief', {})
    if value['version'] == 2:
        if level is None or level != (brief.get('delivery_level') or 'formal'):
            raise AgentError('交付级别与任务起始声明不一致；不能在失败后降级')
        if level == 'exploratory' and any(r.get('role') == 'identity' for r in packet.get('references', [])):
            raise AgentError('身份保持任务须独立评审，不能降为探索自检')
    expected = build_policy(PROFILE, value.get('deliverable_kind'),
                            scope=brief.get('scope', 'create'), delivery_level=level)
    if value != expected:
        raise AgentError('完整流程必要阶段或交付类型与版本定义不一致')
    return value


def require_image_deliverable(packet):
    value = policy(packet)
    if value and value['deliverable_kind'] == 'prompt_only':
        raise AgentError('本题只交付提示词，不能由完整流程自动增加生图；新生成需求须建立相应任务')


def host_instruction(packet):
    selected = policy(packet)
    if selected is None:
        return ''
    return f'''## 完整流程继续执行

当前流程 full-flow-v1，合同版本 {selected['version']}；交付类型 {selected['deliverable_kind']}，交付级别 {selected.get('delivery_level', 'legacy_strict')}。workflow status 是当前下一步的依据。
使用 python -m hex_agent workflow drive --run RUN 推进本地检查；返回宿主步骤后继续执行该动作，再 drive。
prompt_only 在设计、编译与冻结标准通过后直接 workflow complete，不提交图片调用。
image/local_edit 必须取得真实图片；用 workflow requirements --file FILE 登记文字检查适用性，绑定真实 source_ids 与冻结 criterion_id。用户要求保持默认 user_requirement；宿主自拟文字显式用 text.source_kind=design_proposal，引用 assistant 提案并绑定非 hard 设计标准，不冒充用户硬要求。只有明确应出现的画内文字才填 OCR exact_text；无要求说明 not_applicable，不能用空项躲过已冻结文字。
workflow check 自动运行当前图片检查与必要 OCR；默认保留已安装的 Paddle 路线。实际使用系统 Tesseract 须显式 --ocr-backend tesseract --ocr-language eng，回执保留真实后端；不可用、低置信度或错误不算通过。准备 workflow prepare-review 的输入后必须实际打开候选和必要参考，按冻结标准填写 review_record_template，使用 workflow record-review --report FILE 登记。
准备复核输入不等于看图；退出码正常不等于检查项通过。未满足或不确定时处理真实问题，保留原请求与失败记录，不以降低标准或自动补图获得通过。
所有必要证据绑定当前需求、合同、候选与请求后，workflow complete 才可正式交付。机器看图判断仍是宿主报告，用户是否接受另行记录。
此策略不改原 execution-profile：显式研究配置仍须原生 Eval/比较；默认生产配置只追加完整流程复核，不用机器判断决定补图或挑图。
{('新合同允许探索首稿使用实际作者自检，标明未独立评审；正式/高风险及用户准确文字检查要求隔离评审，不能因不可用降级。只有硬要求、文件/OCR及整体目标均成立时，才可披露作者非硬性局部偏差，使用用户对该披露消息的真实回复登记accepted_by_user，再workflow complete。原not_met保持，不把接受变成全部质量通过。研究limited_exit沿真实尝试披露不足，不伪造acceptable；显式必做研究和未决关键输入不适用。' if selected['version'] == 2 else '旧合同不自动启用研究受阻、自检或带偏差收下出口。')}
'''


def _task(run):
    from .workflow import read_packet, validate_solution
    from .contracts import Solution
    from .design import acceptance
    packet = read_packet(run)
    if not policy(packet):
        raise AgentError('此任务没有完整流程策略；保留旧合同，不静默迁移')
    solution = Solution.model_validate(read_json(run / 'solution.json'))
    validate_solution(packet, solution)
    contract = read_json(run / 'acceptance.json')
    if contract != acceptance(packet, solution.design):
        raise AgentError('当前方案与冻结验收标准不一致')
    return packet, contract, solution


def _candidates(run, packet, contract, *, derived=True):
    """Use each route's existing proof, including pending/failed reservations."""
    from .task_review import _candidate
    pending = []
    if (run / 'api-state.json').exists():
        from .api_recovery import load_api_state, _evidence
        state = load_api_state(run)
        _, _, trials, _, _ = _evidence(run, state)
        pending = [r['id'] for r in state['requests'] if r['state'] in ('pending', 'outcome_unknown')]
        raw = [{'file': t['file'], 'sha256': t['sha256'], 'target': t.get('target', 'image')} for t in trials]
    else:
        from .host import read_dispatches
        from .run_status import _verify_attempt
        attempts = read_dispatches(run)
        raw = []
        for attempt in attempts:
            _verify_attempt(packet, run, contract, attempt)
            if attempt['status'] in ('pending', 'outcome_unknown'):
                pending.append(attempt['request']['id'])
            if attempt['status'] == 'completed':
                raw.append({**attempt['output'], 'target': attempt['request']['target']})
    candidates = []
    for row in raw:
        image = within(run, row['file'])
        canonical, evidence, source_sha = _candidate(run, packet, contract, image)
        if canonical != row['sha256']:
            raise AgentError('完成检查的候选与原执行字节摘要不同')
        candidates.append({**row, 'request_id': evidence['request_id'], 'route': evidence['route'],
                           'candidate_file_sha256': source_sha, 'evidence_digest': evidence['digest']})
    if derived:
        from .bounded_repair import delivery_asset
        asset = delivery_asset(run)
        if asset is not None:
            if pending or len(candidates) != 1 or candidates[0]['sha256'] != asset['parent']['candidate_sha256']:
                raise AgentError('最终派生图不对应唯一原模型首稿')
            canonical, evidence, source_sha = _candidate(run, packet, contract, within(run, asset['file']))
            candidates = [{**asset, 'request_id': asset['parent']['request_id'], 'route': 'local_transform',
                           'target': candidates[0]['target'], 'candidate_file_sha256': source_sha,
                           'evidence_digest': evidence['digest']}]
    return candidates, pending


def _key(candidate):
    return digest({'request_id': candidate['request_id'], 'sha256': candidate['sha256']})


def _native_evaluation(run, packet, contract, candidates):
    """Keep research profiles' original review/selection contract intact.

    The production profile does not use a machine judge to select an image.
    An explicitly chosen legacy research profile still needs its native
    review receipts; a workflow sidecar cannot forge an API model response.
    """
    from .visual_decision import record_only
    if record_only(packet) or not candidates:
        return True, {c['sha256'] for c in candidates}, []
    if (run / 'api-state.json').exists():
        from .api_recovery import load_api_state, _evidence
        _, _, trials, index, comparison = _evidence(run, load_api_state(run))
        ready = all(t.get('review') for t in trials) and (len(trials) < 2 or comparison is not None)
    else:
        from .workflow import validate_trial
        from .host import validate_trial_dispatch
        from .run_status import _selection
        path = run / 'host-image-tests.json'
        trials = read_json(path) if path.exists() else []
        for trial in trials:
            validate_trial(packet, run, contract, trial)
            validate_trial_dispatch(packet, run, trial)
        selected = _selection(run, trials)
        index = selected['selected_index'] if selected else 0
        comparison = selected['comparison'] if selected else None
        ready = (len(trials) == len(candidates) and all(t.get('review') for t in trials)
                 and (len(trials) < 2 or selected is not None))
    selected_hashes = {trials[index]['sha256']} if ready and trials else set()
    failures = []
    if ready and trials and trials[index]['review'].get('decision') != 'ready_for_user':
        failures.append('所选候选的原生研究评审仍为 ' + str(trials[index]['review'].get('decision'))
                        + '；后续阶段报告不能静默覆盖原合同未满足项')
    if ready and comparison and comparison.get('choice') == 'uncertain':
        failures.append('原生成对比较仍有未解决的取舍，不能声称候选选择已完成')
    return ready, selected_hashes, failures


def _path(run, section, candidate):
    return within(run, f'workflow/{section}/{_key(candidate)}.json')


def _seal(value):
    body = {k: v for k, v in value.items() if k != 'digest'}
    return {**body, 'digest': digest(body)}


def _read_sealed(path):
    value = read_json(path)
    if not isinstance(value, dict) or value.get('digest') != digest({k: v for k, v in value.items() if k != 'digest'}):
        raise AgentError('完整流程报告已改变或缺少完整摘要：' + str(path))
    return value


def _save(path, value):
    value = _seal(value)
    if path.is_file() and read_json(path) == value:
        return value
    if path.is_file():
        old = path.read_bytes()
        archive = within(path.parent, 'history/' + sha(old) + '.json')
        if not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(old)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)
    return value


def _validate_text(packet, contract, value):
    from .adapters.local_ocr_runtime import validate_ocr_requirements
    from .provenance import verified_sources
    if not isinstance(value, dict):
        raise AgentError('先按本题来源明确 text 检查要求，不由关键词猜测')
    state = value.get('applicability')
    if state not in ('required', 'not_applicable'):
        raise AgentError('text.applicability 必须明确 required 或 not_applicable')
    if not isinstance(value.get('reason'), str) or not value['reason'].strip():
        raise AgentError('文字检查是否适用必须说明本题依据')
    kind = value.get('source_kind', 'user_requirement')
    if kind not in ('user_requirement', 'design_proposal'):
        raise AgentError('text.source_kind 必须为 user_requirement 或 design_proposal')
    ids = value.get('source_ids')
    actor = 'assistant' if kind == 'design_proposal' else 'user'
    sources = {m['id']: m for m in verified_sources(packet.get('input_sources')) if m['actor'] == actor}
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids) or not set(ids) <= sources.keys()):
        raise AgentError('文字检查要求必须指向当前任务真实' +
                         (' assistant 提案' if kind == 'design_proposal' else '用户消息') + ' source_ids')
    items = validate_ocr_requirements(contract, value.get('items', []))
    if (state == 'required') != bool(items):
        raise AgentError('required 必须有逐字绑定标准的 OCR 项；not_applicable 必须为空')
    if kind == 'design_proposal':
        checks = {row['id']: row for row in contract['checks']}
        if state != 'required' or any(checks[row['criterion_id']]['dimension'] == 'hard' for row in items):
            raise AgentError('design_proposal 只登记应出现的设计文字，不能替代用户硬要求或声明无文字要求')
        if any(not any(row['exact_text'] in sources[ident]['text'] for ident in ids) for row in items):
            raise AgentError('设计文字须逐字存在于所引用的 assistant 提案，不能事后补造来源')
    result = {'applicability': state, 'reason': value['reason'].strip(), 'source_ids': ids, 'items': items}
    # Omitted kind retains the exact legacy shape, so old sealed sidecars remain
    # readable without a silent normalization or contract migration.
    if 'source_kind' in value:
        result['source_kind'] = kind
    return result


def requirements(run, file=None):
    from .run_lock import dispatch_lock
    run = Path(run).resolve()
    packet, contract, _ = _task(run)
    if policy(packet)['deliverable_kind'] == 'prompt_only':
        raise AgentError('提示词任务无需像素或 OCR 检查要求')
    if file is None:
        return {'packet_digest': packet['digest'], 'contract_digest': contract['digest'],
                'text': {'applicability': 'undetermined', 'items': [], 'reason': '', 'source_ids': []},
                'sources': packet.get('input_sources', {}).get('messages', []), 'checks': contract['checks'],
                'instruction': '根据真实来源填写 text。默认 user_requirement 引用 user 消息；自拟文字显式 source_kind=design_proposal，引用含 exact_text 的 assistant 提案并绑定非 hard 设计标准，不冒充用户要求。明确应出现文字才 required；items 按 criterion_id/exact_text/text_required:true 绑定冻结标准。无文字要求说明用户依据并选 not_applicable；勿把禁字要求反转为要生成文字。保存JSON后用 workflow requirements --file 登记。'}
    with dispatch_lock(run):
        packet, contract, _ = _task(run)
        value = read_json(Path(file))
        if not isinstance(value, dict) or value.get('packet_digest') != packet['digest'] or value.get('contract_digest') != contract['digest']:
            raise AgentError('检查要求须绑定当前 packet_digest 和 contract_digest')
        text = _validate_text(packet, contract, value.get('text'))
        return _save(within(run, 'workflow/requirements.json'), {
            'format': 'hex.workflow-review-requirements.v1', 'packet_digest': packet['digest'],
            'contract_digest': contract['digest'], 'text': text,
            'authority': ('host_interpretation_of_bound_design_proposal' if text.get('source_kind') == 'design_proposal'
                          else 'host_interpretation_of_bound_user_sources')})


def _requirements(run, packet, contract):
    value = _read_sealed(within(run, 'workflow/requirements.json'))
    if value.get('packet_digest') != packet['digest'] or value.get('contract_digest') != contract['digest']:
        raise AgentError('检查要求属于旧需求或旧合同；先重新登记本题检查要求')
    if value.get('text') != _validate_text(packet, contract, value.get('text')):
        raise AgentError('检查要求格式或逐字引用不一致')
    return value


def _choose(candidates, request_id):
    if request_id is None:
        if len(candidates) != 1:
            raise AgentError('没有唯一候选；用 --request-id 明确本次图片，不自动选择最后一张')
        return candidates[0]
    matches = [c for c in candidates if c['request_id'] == request_id]
    if len(matches) != 1:
        raise AgentError('request-id 没有唯一、已完成且可验证的候选图片')
    return matches[0]


def _envelope(packet, contract, candidate, req, report):
    return {'format': 'hex.workflow-stage-evidence.v1', 'packet_digest': packet['digest'],
            'contract_digest': contract['digest'], 'candidate_sha256': candidate['sha256'],
            'request_id': candidate['request_id'], 'requirements_digest': req['digest'], 'report': report}


def check(run, request_id=None, *, ocr_timeout=180, ocr_backend='paddleocr', ocr_language='eng'):
    from .run_lock import dispatch_lock
    from .task_review import check_task_image
    run = Path(run).resolve()
    with dispatch_lock(run):
        packet, contract, _ = _task(run)
        req = _requirements(run, packet, contract)
        candidates, pending = _candidates(run, packet, contract)
        if pending:
            raise AgentError('仍有结果未知的原请求；先查询原请求，不把当前候选当全部完成')
        candidate = _choose(candidates, request_id)
        if candidate['route'] == 'local_transform':
            originals, _ = _candidates(run, packet, contract, derived=False)
            original = _choose(originals, request_id)
            if _path(run, 'checks', original).exists():
                _bound_stage(run, 'checks', packet, contract, original, req)
            else:
                original_report = check_task_image(run, within(run, original['file']),
                    ocr_requirements=req['text']['items'], ocr_timeout_seconds=ocr_timeout,
                    ocr_backend=ocr_backend, ocr_language=ocr_language)
                _save(_path(run, 'checks', original), _envelope(packet, contract, original, req, original_report))
        report = check_task_image(run, within(run, candidate['file']),
                                  ocr_requirements=req['text']['items'], ocr_timeout_seconds=ocr_timeout,
                                  ocr_backend=ocr_backend, ocr_language=ocr_language)
        if report.get('generation_binding', {}).get('state') != (
                'verified_local_transform' if candidate['route'] == 'local_transform' else 'verified_existing_generation'):
            raise AgentError('未绑定执行证据的本地诊断不能登记为完整流程检查')
        evidence = _save(_path(run, 'checks', candidate), _envelope(packet, contract, candidate, req, report))
        card = None
        try:
            reviewed = _bound_stage(run, 'reviews', packet, contract, candidate, req)
        except _ERRORS:
            # An absent or superseded review stays pending. A new file check
            # must not reuse its old visual judgments to make a feedback card.
            reviewed = None
        if reviewed is not None:
            from .feedback_card import export_card
            card = export_card(run, candidate, contract, reviewed, evidence)
        return {'evidence': str(_path(run, 'checks', candidate)), 'report': report, 'digest': evidence['digest'],
                'check_passed': not _check_failures(report, req), 'feedback_card': card}


def prepare_review(run, request_id=None):
    from .run_lock import dispatch_lock
    from .task_review import prepare_task_review
    run = Path(run).resolve()
    with dispatch_lock(run):
        packet, contract, _ = _task(run)
        req = _requirements(run, packet, contract)
        candidates, pending = _candidates(run, packet, contract)
        if pending:
            raise AgentError('先核对原请求结果，不重复生成')
        candidate = _choose(candidates, request_id)
        report = prepare_task_review(run, within(run, candidate['file']))
        from .delivery_review import self_check_allowed
        if not self_check_allowed(contract, req):
            from .instructions import GOAL_FIRST_REVIEW
            report['instructions'] = GOAL_FIRST_REVIEW
            report['payload']['review_mode'] = 'goal_first_isolated'
            report['payload']['notice'] = '正式或高风险交付须实际独立隔离评审；不可用时保留阻塞，不降级。'
        _save(_path(run, 'review-inputs', candidate), _envelope(packet, contract, candidate, req, report))
        return {'review_input': str(_path(run, 'review-inputs', candidate)), 'report': report,
                'model_call_state': 'not_run', 'next': '实际打开候选及必要参考，用原标准填写 review_record_template，再 workflow record-review。准备材料不等于完成看图。'}


def record_review(run, report_file, request_id=None):
    from .run_lock import dispatch_lock
    from .task_review import validate_task_review
    run = Path(run).resolve()
    with dispatch_lock(run):
        packet, contract, _ = _task(run)
        req = _requirements(run, packet, contract)
        candidates, _ = _candidates(run, packet, contract)
        candidate = _choose(candidates, request_id)
        raw = Path(report_file).read_bytes()
        report = validate_task_review(run, within(run, candidate['file']), Path(report_file))
        from .delivery_review import require_review_context
        require_review_context(contract, report['review'], req)
        if report['review'].get('holistic', {}).get('image_access') != 'viewed':
            raise AgentError('完整流程必须实际看当前图片；准备输入或未看图的报告不能替代')
        if sha(raw) != report['source_report_sha256']:
            raise AgentError('登记期间评审原文件已变化')
        source = within(run, f'workflow/review-sources/{sha(raw)}.json')
        source.parent.mkdir(parents=True, exist_ok=True)
        if source.exists() and source.read_bytes() != raw:
            raise AgentError('已保存评审源文件不一致')
        if not source.exists():
            source.write_bytes(raw)
        # The durable source copy is independently revalidated on every finish.
        report = validate_task_review(run, within(run, candidate['file']), source)
        evidence = _save(_path(run, 'reviews', candidate), _envelope(packet, contract, candidate, req, report))
        try:
            checked = _bound_stage(run, 'checks', packet, contract, candidate, req)
        except _ERRORS:
            # A valid visual observation can still be recorded when the file
            # check needs to run again. The card marks it unverified; the
            # existing completion gate continues to require the real check.
            checked = None
        from .feedback_card import export_card
        card = export_card(run, candidate, contract, evidence, checked)
        return {'evidence': str(_path(run, 'reviews', candidate)), 'digest': evidence['digest'],
                'review_decision': report['review']['decision'], 'human_acceptance': 'not_granted_by_machine',
                'feedback_card': card}


def _bound_stage(run, section, packet, contract, candidate, req):
    value = _read_sealed(_path(run, section, candidate))
    expected = _envelope(packet, contract, candidate, req, value.get('report'))
    if value != _seal(expected):
        raise AgentError('阶段报告绑定的需求、图片、合同或检查要求已变化')
    report = value['report']
    binding = report.get('generation_binding', {})
    expected_binding = {'state': ('verified_local_transform' if candidate['route'] == 'local_transform'
                                   else 'verified_existing_generation'), 'route': candidate['route'],
                        'request_id': candidate['request_id'], 'candidate_sha256': candidate['sha256'],
                        'candidate_file_sha256': candidate['candidate_file_sha256'],
                        'packet_digest': packet['digest'], 'contract_digest': contract['digest'],
                        'evidence_digest': candidate['evidence_digest']}
    if candidate['route'] == 'local_transform':
        expected_binding.update(asset_id=candidate['asset_id'], parent=candidate['parent'],
                                transform_receipt_digest=candidate['transform']['receipt_digest'])
    if any(binding.get(k) != v for k, v in expected_binding.items()) or report.get('contract_digest') != contract['digest']:
        raise AgentError('阶段报告没有绑定当前真实生成证据')
    if section == 'reviews':
        from .task_review import validate_task_review
        source = within(run, f"workflow/review-sources/{report['source_report_sha256']}.json")
        verified = validate_task_review(run, within(run, candidate['file']), source)
        from .delivery_review import require_review_context
        require_review_context(contract, verified['review'], req)
        if verified != report or verified.get('source_contract_binding') != 'explicit_current_contract':
            raise AgentError('视觉评审原报告或合同绑定与登记证据不一致')
    return value


def registered_review(run, request_id=None):
    """Read a full-flow review without requiring successful completion.

    Only absence permits a caller to use the historical native review path.
    An existing but invalid sidecar must not silently fall back to older text.
    The candidate's original generation snapshot, not a later draft solution,
    supplies the image/request/contract binding.
    """
    from .workflow import read_packet
    run = Path(run).resolve()
    packet = read_packet(run)
    if policy(packet) is None:
        return None
    contract = read_json(run / 'acceptance.json')
    candidates, _ = _candidates(run, packet, contract)
    candidate = _choose(candidates, request_id)
    path = _path(run, 'reviews', candidate)
    if not path.exists():
        return None
    req = _requirements(run, packet, contract)
    evidence = _bound_stage(run, 'reviews', packet, contract, candidate, req)
    return {'candidate': candidate, 'evidence': evidence,
            'evidence_file': str(path.relative_to(run))}


def _check_failures(report, req):
    failures = []
    image = report.get('image_checks', {})
    if image.get('state') != 'completed' or not image.get('checks'):
        failures.append('图片读取检查未完成')
    failures += ['图片检查未满足：' + str(row.get('id')) for row in image.get('checks', []) if row.get('verdict') != 'met']
    ocr = report.get('ocr_checks', {})
    if req['text']['applicability'] == 'required':
        expected = {row['criterion_id'] for row in req['text']['items']}
        items = ocr.get('items', [])
        if ocr.get('state') != 'completed' or len(items) != len(expected) or {row.get('criterion_id') for row in items} != expected:
            failures.append('本题必要 OCR 未完成或漏项：' + str(ocr.get('state')))
        failures += ['文字未确认满足：' + str(row.get('criterion_id')) for row in items if row.get('verdict') != 'met']
    elif ocr.get('state') != 'not_applicable' or ocr.get('items'):
        failures.append('OCR 报告与已登记适用范围不一致')
    return failures


def _action(run, action, description, *, candidate=None, owner='code', executable=True, requires_user_input=False):
    argv = [sys.executable, '-m', 'hex_agent', 'workflow', action, '--run', str(run)] if action else None
    if argv and candidate:
        argv += ['--request-id', candidate['request_id']]
    return {'command': 'workflow' if action else None, 'action': action, 'argv': argv,
            'description': description, 'owner': owner, 'executable': executable,
            'requires_user_input': requires_user_input, 'wait_for_existing_request': False}


def _derived_completion(run, packet, contract, req, candidate, final_receipt):
    """Keep first-draft failures intact; prove only the narrowly resolved size."""
    originals, _ = _candidates(run, packet, contract, derived=False)
    original = _choose(originals, candidate['request_id'])
    checked = _bound_stage(run, 'checks', packet, contract, original, req)
    if _check_failures(checked['report'], req) != ['图片检查未满足：image.size']:
        raise AgentError('本地缩小只解决首稿尺寸不符；其它失败不能被最终副本隐藏')
    old_review = None
    if _path(run, 'reviews', original).exists():
        old_review = _bound_stage(run, 'reviews', packet, contract, original, req)
        if old_review['report']['review']['decision'] != 'ready_for_user':
            raise AgentError('原图存在视觉分歧，尺寸变换不能替代展示或消除原审美失败')
    size = next((row for row in final_receipt['image_checks']['checks'] if row['id'] == 'image.size'), None)
    if size is None or size['verdict'] != 'met':
        raise AgentError('最终派生图仍未满足原冻结尺寸，停止交付')
    from .bounded_repair import delivery_asset
    return {'asset': delivery_asset(run), 'original_check': checked, 'original_review': old_review,
            'original_check_file': str(_path(run, 'checks', original).relative_to(run)),
            'resolved_failures': [{'check_id': 'image.size', 'original_verdict': 'not_met',
                                   'final_verdict': 'met', 'final_check_digest': final_receipt['check_digest'],
                                   'transform_receipt_digest': candidate['transform']['receipt_digest']}],
            'notice': '首稿的not_met保持不变；最终图经单次本地变换、独立字节检查和当前视觉评审交付。'}


def repair(run):
    """Workflow/CLI entrypoint; execute only the current eligible local step."""
    from .host import execute_repair
    state = inspect_completion(run)
    if state['state'] != 'needs_bounded_repair':
        raise AgentError('当前workflow没有可执行的单次本地返修')
    assessed = state['repair_assessment']
    return execute_repair(Path(run), assessed['digest'], assessed['parent_request_id'])


def inspect_completion(run, packet=None):
    """Read-only projection. It does not run OCR, a model, or any submission."""
    from .workflow import read_packet
    run = Path(run).resolve()
    base = {'applicable': False, 'ready': False, 'complete': False, 'state': 'legacy',
            'missing': [], 'blockers': [], 'next_action': None}
    try:
        packet = packet if packet is not None else read_packet(run)
        selected = policy(packet)
        if selected is None:
            return base
        base.update(applicable=True, policy=selected, state='needs_creation')
        if packet.get('need_mode') == 'offline_heuristic':
            return {**base, 'missing': ['intent']}
        from .co_creation import copilot_enabled, early_blockers, initial_assessment_required
        if copilot_enabled(packet):
            questions = early_blockers(packet)
            if questions:
                return {**base, 'state': 'needs_input', 'missing': ['intent'], 'questions': questions,
                        'next_action': _action(run, None, questions[0], owner='host_model',
                                               requires_user_input=True)}
            if initial_assessment_required(packet):
                return {**base, 'state': 'needs_intent_assessment', 'missing': ['intent'],
                        'next_action': _action(run, None, '先按用户原话判断是否已有明确研究目标，记录 Need.direction_review；明确则直接研究，重要含糊则登记并提出一个关键问题。缺判断本身不是追问理由。', owner='host_model')}
        from .visual_research import status as research_status
        study = research_status(packet)
        if study and study['state'] == 'needs_reference_expansion':
            return {**base, 'state': 'needs_reference_expansion', 'missing': ['references'], 'visual_research': study,
                    'next_action': _action(run, None, '按已明确需求继续找并实际观察不同表达关系的案例，评价是否形成可选方向；不限定参考数量，不重复追问已回答目标。', owner='host_model')}
        if study and study['state'] in ('needs_selection_assessment', 'needs_reference_selection'):
            description = ('实际评价已看案例对本题的适用性与审美方向，记录选择状态及理由；由宿主判断，不为缺字段机械追问。'
                           if study['state'] == 'needs_selection_assessment' else
                           '现有案例不合适或重要偏好仍未决：沿当前 Need/question_records 发起有针对性的 Copilot 问答，可用自然问题或有实见依据的参考选项；保留已确认需求，不猜选、不重复抽图。')
            return {**base, 'state': study['state'], 'missing': ['references'], 'visual_research': study,
                    'next_action': _action(run, None, description, owner='host_model')}
        if study and study['state'] in ('needs_assessment', 'needs_observation'):
            question = (study.get('assessment') or {}).get('visual_question', '')
            description = ('按真实用户来源填写 Need.visual_research 并 refresh；未定方向的开放题须研究，不能预先锁定宿主第一想法。'
                           if study['state'] == 'needs_assessment' else
                           '围绕本题视觉问题主动查找可用案例并实际看图，沿 add-reference/observe 保存来源与观察；仅供研究可 evaluation_only，不自动上传。随后比较方向，将采用或舍弃落实到 choices/reference_uses/checks。')
            return {**base, 'state': 'needs_visual_research', 'missing': ['references'],
                    'visual_research': study, 'visual_question': question,
                    'next_action': _action(run, None, description, owner='host_model')}
        if not (run / 'solution.json').exists() or not (run / 'acceptance.json').exists():
            return {**base, 'missing': ['design', 'compile', 'verify']}
        packet, contract, solution = _task(run)
        candidates, pending = _candidates(run, packet, contract)
        if pending:
            return {**base, 'state': 'awaiting_result', 'missing': ['execute'], 'pending_request_ids': pending}
        if selected['deliverable_kind'] == 'prompt_only' and candidates:
            raise AgentError('提示词任务出现实际图片调用；核对执行范围，不能据此完成')
        receipts = []
        feedback_cards = []
        accepted_deviations = []
        failures = []
        if selected['deliverable_kind'] != 'prompt_only':
            targets = set(packet['brief']['targets']) if packet['brief'].get('model_comparison') else None
            if not candidates or (targets is not None and {c['target'] for c in candidates} != targets):
                return {**base, 'state': 'needs_generation', 'missing': ['execute']}
            native_ready, delivered_hashes, native_failures = _native_evaluation(run, packet, contract, candidates)
            if not native_ready:
                return {**base, 'state': 'needs_native_review', 'missing': ['visual_review'],
                        'notice': '当前显式研究配置仍须原生 Eval/比较；新阶段记录不能伪造原API评审请求。'}
            failures.extend(native_failures)
            try:
                req = _requirements(run, packet, contract)
            except _ERRORS as exc:
                return {**base, 'state': 'needs_review_requirements', 'missing': ['check_requirements'],
                        'next_action': _action(run, 'requirements', '按本题原话登记结构化检查要求：' + str(exc), owner='host_model')}
            for candidate in candidates:
                try:
                    checked = _bound_stage(run, 'checks', packet, contract, candidate, req)
                except _ERRORS as exc:
                    return {**base, 'state': 'needs_image_check', 'missing': ['image_check'],
                            'next_action': _action(run, 'check', '运行并登记当前图片的真实检查：' + str(exc), candidate=candidate)}
                if candidate['sha256'] in delivered_hashes:
                    failures += _check_failures(checked['report'], req)
                try:
                    reviewed = _bound_stage(run, 'reviews', packet, contract, candidate, req)
                except _ERRORS:
                    try:
                        prepared = _bound_stage(run, 'review-inputs', packet, contract, candidate, req)
                        if prepared['report'].get('model_call_state') != 'not_run':
                            raise AgentError('复核准备状态无效')
                    except _ERRORS:
                        return {**base, 'state': 'needs_review_input', 'missing': ['visual_review'],
                                'next_action': _action(run, 'prepare-review', '准备当前候选与参考的视觉复核输入', candidate=candidate)}
                    return {**base, 'state': 'needs_visual_review', 'missing': ['visual_review'],
                            'next_action': {**_action(run, None, '实际打开候选及参考，填写输入中的 review_record_template，再用 workflow record-review 登记；不能只准备材料就结束。', owner='host_model'),
                                            'request_id': candidate['request_id'], 'image': str(within(run, candidate['file'])),
                                            'review_input': str(_path(run, 'review-inputs', candidate))}}
                review = reviewed['report']['review']
                from .feedback_card import saved_card
                card = saved_card(run, candidate, reviewed, checked)
                if card is not None:
                    feedback_cards.append(card)
                if (candidate['sha256'] in delivered_hashes and
                        (review.get('holistic', {}).get('image_access') != 'viewed' or review.get('decision') != 'ready_for_user')):
                    from .delivery_review import accepted_deviation
                    accepted = accepted_deviation(run, candidate['request_id']) if selected['version'] == 2 else None
                    if accepted:
                        accepted_deviations.append(accepted)
                    else:
                        failures.append('视觉复核尚未达到当前标准：' + candidate['request_id'])
                receipts.append({'request_id': candidate['request_id'], 'candidate_sha256': candidate['sha256'],
                                 'route': candidate['route'], 'requirements_digest': req['digest'],
                                 'check_digest': checked['digest'], 'review_digest': reviewed['digest'],
                                 'delivered': candidate['sha256'] in delivered_hashes,
                                 'image_checks': checked['report']['image_checks'],
                                 'ocr_checks': checked['report']['ocr_checks'], 'review': review,
                                 'check_evidence_file': str(_path(run, 'checks', candidate).relative_to(run)),
                                 'review_evidence_file': str(_path(run, 'reviews', candidate).relative_to(run))})
        if failures:
            if failures == ['图片检查未满足：image.size'] and len(candidates) == 1 and candidates[0]['route'] == 'host':
                from .bounded_repair import evaluate
                assessed = evaluate(run, candidates[0]['request_id'])
                if assessed['eligible']:
                    return {**base, 'state': 'needs_bounded_repair', 'repair_assessment': assessed,
                            'candidate_receipts': receipts, 'feedback_cards': feedback_cards,
                            'next_action': _action(run, 'repair', '已有独立用户返修许可，按冻结尺寸在原run占用一次剩余槽位并本地同比缩小；首稿及失败记录保留。', candidate=candidates[0])}
            followup = (' 先结合原图与用户反馈做 Copilot 诊断；关键表达或偏好不明确时提出一个针对性问题，已有明确修改就沿已知范围修正，不重复问。'
                        if copilot_enabled(packet) else '')
            return {**base, 'state': 'quality_not_met', 'blockers': failures,
                    'candidate_receipts': receipts, 'feedback_cards': feedback_cards,
                    'next_action': _action(run, None, '检查已运行但存在未满足或不确定项。展示真实图片与问题，沿现有来源和预算制定关联修订；未获新范围许可不补图，不把本题标为完成。' + followup, owner='host_model', executable=False)}
        receipt = {'format': 'hex.workflow-completion.v1', 'workflow_policy': selected,
                   'packet_digest': packet['digest'], 'contract_digest': contract['digest'],
                   'candidate_receipts': receipts, 'required_stages_verified': True,
                   'human_acceptance': 'separate_explicit_user_feedback',
                   'visual_judgment_authority': 'host_reported_untrusted' if receipts else 'not_applicable'}
        if candidates and candidates[0]['route'] == 'local_transform':
            receipt['derived_delivery'] = _derived_completion(run, packet, contract, req, candidates[0], receipts[0])
        if selected['version'] == 2:
            receipt.update(delivery_level=selected['delivery_level'],
                           independent_review=(all(row['review']['holistic']['review_context'] == 'isolated'
                                                   for row in receipts) if receipts else None),
                           quality_standards_met=not accepted_deviations,
                           delivery_outcome='accepted_with_deviations' if accepted_deviations else 'required_checks_completed',
                           accepted_deviations=accepted_deviations)
        if study is not None:
            from .visual_research import validate_design as validate_study
            receipt['visual_research'] = validate_study(packet, solution.design)
        complete = False
        if (run / 'result.json').is_file():
            exported = read_json(run / 'result.json')
            if exported.get('workflow_completion') == receipt:
                from .run_status import _export_files_valid
                complete = _export_files_valid(run, verified_relative=bool(
                    exported.get('candidate_feedback') or exported.get('relative_preference')))
        return {**base, 'state': 'complete' if complete else 'ready_to_export', 'ready': True,
                'complete': complete, 'receipt': receipt, 'feedback_cards': feedback_cards,
                'next_action': None if complete else
                _action(run, 'complete', '必要步骤的当前证据齐全，执行正式交付')}
    except _ERRORS as exc:
        return {**base, 'state': 'blocked', 'blockers': [str(exc)],
                'next_action': _action(run, None, '核对完整流程证据：' + str(exc), executable=False)}


def require_completion_ready(run, *, packet=None):
    state = inspect_completion(run, packet)
    if not state['applicable']:
        if state['state'] == 'blocked':
            raise AgentError('; '.join(state['blockers']))
        return None
    if not state['ready']:
        reason = (state.get('next_action') or {}).get('description') or ', '.join(state['missing'] + state['blockers'])
        raise AgentError('HEX完整流程未完成 [' + state['state'] + ']：' + reason)
    return state['receipt']


def apply_to_status(run, report):
    """Extend the existing inspect_run authority, never run a parallel scheduler."""
    from .workflow import read_packet
    run = Path(run).resolve()
    try:
        packet = read_packet(run)
        if policy(packet) is None:
            return report
    except _ERRORS:
        return report  # Existing inspection owns malformed packets.
    status = deepcopy(report)
    completion = inspect_completion(run, packet)
    status['workflow_completion'] = completion
    action = status['next_action']
    stopped = status.get('generation_control', {}).get('state') == 'stopped'
    waiting = status['stage'] == 'awaiting_result'
    unsubmitted = (waiting and action.get('command') in ('execute-comfy', 'execute-pippit')
                   and not stopped)
    action.update(owner='host_model', executable=not (status['blockers'] or stopped),
                  requires_user_input=status['stage'] in ('needs_input', 'needs_reference'),
                  wait_for_existing_request=waiting and not unsubmitted)
    if unsubmitted:
        action['owner'] = 'executor'
    if status['stage'] == 'needs_design' and packet.get('need_mode') == 'offline_heuristic':
        status['stage'] = 'needs_parse'
        action.update(command='refresh', description='读取本题 HEX 指导，按真实用户原话形成 Need 并 refresh；离线草稿不能作为已理解。')
    if status['stage'] == 'needs_authorization':
        action['requires_user_input'] = action.get('command') != 'resume'
        action['executable'] = action.get('command') == 'resume' and not stopped
    if status['blockers'] or waiting:
        action['executable'] = bool(not status['blockers'] and waiting and
                                    (unsubmitted or action.get('command') in ('query', 'query-comfy', 'query-pippit')))
        return status
    if completion['state'] in ('needs_creation', 'needs_native_review'):
        return status
    if completion['state'] == 'needs_generation':
        if status.get('route') == 'api' and status['stage'] in ('ready_to_export', 'exported'):
            status.update(stage='needs_generation', generation_allowed=False,
                          next_action=_action(run, None, '本题要求图片但API尚无全部目标的真实成图；核对当前API生成配置与原许可，不能只导出提示词冒充完成。', executable=False))
        elif status['stage'] in ('generation_failed', 'exported') and action.get('command') in (None, 'finalize'):
            status.update(stage='needs_generation', next_action=_action(run, None, '没有本题要求的真实成图；说明失败与原预算状态，不追加调用或声称完成。', executable=False))
        return status
    if completion['next_action'] is not None:
        status.update(stage=completion['state'], next_action=completion['next_action'], generation_allowed=False)
    elif completion['complete'] and status['stage'] == 'exported':
        status['generation_allowed'] = False
        if completion['policy']['deliverable_kind'] == 'prompt_only':
            status['next_action'] = _action(
                run, None, '提示词已完成本题交付。后续如需生成图片，按新的成图需求建立相应任务。',
                owner='host_model', executable=False)
        else:
            status['next_action'].update(executable=False)
    if stopped:
        action = status['next_action']
        local_completion = (
            (action.get('command') == 'workflow' and action.get('action') in (
                'requirements', 'check', 'prepare-review', 'complete'))
            or (status['stage'] == 'needs_visual_review' and action.get('owner') == 'host_model'))
        if not local_completion:
            action['executable'] = False
    return status


def complete(run):
    from .run_status import inspect_run
    from .resume import resume_run
    from .host import finalize
    run = Path(run).resolve()
    from .workflow import read_packet
    if policy(read_packet(run)) is None:
        raise AgentError('旧任务保留原合同；用 status/resume/finalize 恢复，不标为 full-flow 新验收')
    require_completion_ready(run)
    if (run / 'api-state.json').exists():
        resume_run(run)
    else:
        from .run_lock import dispatch_lock
        with dispatch_lock(run):
            require_completion_ready(run)
            finalize(run, run / 'solution.json')
    status = inspect_run(run)
    if status['blockers'] or not status.get('workflow_completion', {}).get('complete'):
        raise AgentError('交付文件与完整流程证据未完成一致性验证')
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(prog='hex-agent workflow')
    parser.add_argument('action', choices=['status', 'requirements', 'check', 'prepare-review', 'record-review', 'repair', 'complete', 'drive'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--request-id')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--ocr-timeout', type=float, default=180)
    parser.add_argument('--ocr-backend', choices=['paddleocr', 'tesseract'], default='paddleocr',
                        help='check 专用：显式选择本地 OCR 后端，不自动降级')
    parser.add_argument('--ocr-language', default='eng', help='Tesseract 已安装语言，例如 eng；不下载语言包')
    parser.add_argument('--max-steps', type=int, default=8)
    args = parser.parse_args(argv)
    try:
        if args.action == 'status':
            from .run_status import inspect_run
            result = inspect_run(args.run)
        elif args.action == 'requirements':
            result = requirements(args.run, args.file)
        elif args.action == 'check':
            result = check(args.run, args.request_id, ocr_timeout=args.ocr_timeout,
                           ocr_backend=args.ocr_backend, ocr_language=args.ocr_language)
        elif args.action == 'prepare-review':
            result = prepare_review(args.run, args.request_id)
        elif args.action == 'record-review':
            if args.report is None:
                raise AgentError('record-review 需要 --report，不能自动生成看图结论')
            result = record_review(args.run, args.report, args.request_id)
        elif args.action == 'repair':
            result = repair(args.run)
        elif args.action == 'drive':
            from .harness import drive
            result = drive(args.run, max_steps=args.max_steps)
        else:
            result = complete(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except _ERRORS as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
