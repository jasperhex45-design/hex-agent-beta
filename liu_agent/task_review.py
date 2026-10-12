"""Explicit local task review sidecar. Never mutates a run or starts generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters.image_checks import inspect_image
from .adapters.vlm_review import prepare_input, validate_output
from .io import AgentError, normalize_image, read_json, sha
from .quality_review import task_evaluation_card


def _task(run: Path):
    from .workflow import read_packet
    packet = read_packet(run)
    contract = read_json(run / 'acceptance.json')
    # This also validates the complete frozen contract's digest.
    card = task_evaluation_card(packet, contract, packet['references'])
    return packet, contract, card


def _candidate(run, packet, contract, image):
    from .generation_evidence import for_api, for_host
    run = Path(run)
    raw = Path(image).read_bytes()
    from .bounded_repair import delivery_evidence
    derived = delivery_evidence(run, Path(image))
    if derived is not None:
        if derived['candidate_sha256'] != sha(raw) or Path(image).read_bytes() != raw:
            raise AgentError('最终派生图字节已变化，不能绑定旧变换回执')
        return sha(raw), derived, sha(raw)
    api_markers = ('api-state.json', 'calls.json', 'image-tests.json', 'api')
    if any((run / name).exists() for name in api_markers):
        if not (run / 'api-state.json').is_file():
            raise AgentError('API调用记录缺少持久化状态；不能改用宿主回执绑定候选')
        if any((run / name).exists() for name in ('host-dispatches.json', 'dispatch.json')):
            raise AgentError('任务混有API与宿主调用账本；先核对执行来源，不能猜测候选路线')
        # API evidence owns the exact bytes saved by its provider. Do not
        # normalize them again or fall back to a host-reported receipt.
        candidate = raw
        evidence = for_api(run, packet, contract, candidate)
    else:
        # Preserve the existing host bridge's normalized-candidate contract.
        candidate = normalize_image(raw)
        evidence = for_host(run, packet, contract, candidate)
    if sha(Path(image).read_bytes()) != sha(raw):
        raise AgentError('核对生成证据时本地候选文件已变化')
    return sha(candidate), evidence, sha(raw)


def _generation_binding(candidate_sha: str, evidence: dict, source_sha: str) -> dict:
    """Expose the existing ledger binding without upgrading its authority."""
    result = {'state': 'verified_existing_generation', 'route': evidence['route'],
              'request_id': evidence['request_id'], 'candidate_sha256': candidate_sha,
              'candidate_file_sha256': source_sha, 'evidence_digest': evidence['digest'],
              'packet_digest': evidence['packet_digest'], 'contract_digest': evidence['contract_digest']}
    if evidence['route'] == 'local_transform':
        result.update(state='verified_local_transform', asset_id=evidence['asset_id'],
                      parent=evidence['delivery_asset']['parent'],
                      transform_receipt_digest=evidence['delivery_asset']['transform']['receipt_digest'])
    if evidence['route'] == 'host':
        # Compatibility alias for existing host-side report consumers only.
        result['candidate_normalized_sha256'] = candidate_sha
    return result


def review_card(run: Path) -> dict:
    return _task(Path(run))[2]


def check_task_image(run: Path, image: Path, *, ocr_requirements: list | Path | str | None = None,
                     ocr_timeout_seconds: float = 180, ocr_backend='paddleocr', ocr_language='eng') -> dict:
    """Inspect existing pixels, with local OCR only for explicit frozen text checks."""
    from .adapters.local_ocr_runtime import run_local_ocr, validate_ocr_requirements
    run, image = Path(run).resolve(), Path(image).resolve()
    packet, contract, card = _task(run)
    requirements_file = Path(ocr_requirements).resolve() if isinstance(ocr_requirements, (str, Path)) else None
    requirements = read_json(requirements_file) if requirements_file else ocr_requirements
    if requirements is None:
        requirements = []
    requirements = validate_ocr_requirements(contract, requirements)
    options = packet['brief'].get('generation_options') or {}
    checks = inspect_image(image, expected_ratio=options.get('desired_aspect_ratio'))
    binding = {'state': 'unbound_local_diagnostic', 'request_id': None}
    if checks['state'] == 'completed':
        try:
            candidate_sha, evidence, source_sha = _candidate(run, packet, contract, image)
            binding = _generation_binding(candidate_sha, evidence, source_sha)
            from .contracts import DesignPlan
            from .workflow import generation_settings
            settings = generation_settings(packet, DesignPlan.model_validate(evidence['solution']['design']))
            expected_size = None
            if packet.get('workflow_policy') is not None:
                from .generation_spec import _dimensions
                # Preserve legacy diagnostics. New complete flows also check
                # explicit frozen pixel dimensions; auto adds no guessed size.
                expected_size = _dimensions(settings.get('requested_size') or 'auto')
            checks = inspect_image(image, expected_ratio=settings.get('desired_aspect_ratio'),
                                   expected_size=expected_size)
            if checks.get('image', {}).get('sha256') != source_sha:
                raise AgentError('检查期间候选文件已变化，不能绑定原生成回执')
        except (AgentError, OSError, ValueError) as exc:
            # Unregistered images can still be inspected, never presented as
            # generated, accepted, or reviewed evidence for the existing run.
            binding = {'state': 'unbound_local_diagnostic', 'request_id': None,
                       'reason': str(exc)}
    # The bridge validates requirements before any optional model allocation.
    # An unreadable source still gets an explicit unavailable OCR result.
    if checks['state'] == 'completed' or not requirements:
        ocr = run_local_ocr(image, contract, requirements, contract_path=run / 'acceptance.json',
                            expected_candidate_sha256=checks.get('image', {}).get('sha256'),
                            timeout_seconds=ocr_timeout_seconds, backend=ocr_backend, language=ocr_language)
    else:
        from .adapters.ocr_review import review_text
        ocr = review_text(image, contract, requirements, engine=None)
        ocr['reason'] = '候选图片不可读，未启动OCR'
        ocr['local_process'] = {'state': 'not_started', 'calls': 0, 'elapsed_seconds': 0.0}
    if requirements_file:
        ocr['requirements_file'] = str(requirements_file)
    inspected_sha = checks.get('image', {}).get('sha256')
    if inspected_sha is not None and sha(image.read_bytes()) != inspected_sha:
        raise AgentError('图像检查期间候选文件已变化，不能保存旧检查与当前图片的绑定')
    ocr['generation_binding'] = dict(binding)
    failed = [row for row in checks['checks'] if row['verdict'] == 'not_met']
    checked = '文件读取与当前明确画幅' if any(row['id'] == 'image.ratio' for row in checks['checks']) else '文件读取'
    if any(row['id'] == 'image.size' for row in checks['checks']):
        checked += '、明确像素尺寸'
    if ocr['state'] == 'completed':
        checked += '、本地OCR明确文字核对'
    ocr_findings = [row for row in ocr.get('items', []) if row['verdict'] != 'met']
    if ocr['state'] in {'unavailable', 'failed'}:
        ocr_findings.append({'check': 'ocr', 'state': ocr['state'], 'reason': ocr['reason']})
    return {'format': 'hex.task-image-check.v1', 'contract_digest': contract['digest'],
            'state': 'completed' if checks['state'] == 'completed' and
                     ocr['state'] in {'completed', 'not_applicable'} else 'incomplete',
            'candidate_file': str(Path(image).resolve()), 'generation_binding': binding,
            'candidate_sha256': checks.get('image', {}).get('sha256'),
            'candidate_file_sha256': checks.get('image', {}).get('sha256'),
            'image_checks': checks, 'ocr_checks': ocr,
            'user_view': {'original_image': str(Path(image).resolve()),
                          'focus': [row['inspect'] for row in card['items'][:3]],
                          'checked': checked,
                          'found': failed + ocr_findings or ['未发现已运行检查的失败；尚未判断视觉内容'],
                          'next': '先查看原图与本次重点；必要时在隔离上下文做视觉评审',
                          'user_acceptance': 'not_recorded_by_this_check',
                          'revision_authorization': 'not_granted_by_this_check'},
            'semantic_and_visual_review': 'not_run', 'generation_calls': 0}


def validate_review_destination(run: Path, destination: Path, *, source_files=()) -> Path:
    """Require a new sidecar outside the source task; reusable by local launchers."""
    run, destination = Path(run).resolve(), Path(destination).resolve()
    if destination == run or run in destination.parents:
        raise AgentError('派生报告须写到任务目录之外，不能改写原任务证据')
    if destination in [Path(path).resolve() for path in source_files if path] or destination.exists():
        raise AgentError('输出须使用新的文件，不能覆盖图像、要求、评审或已有报告')
    return destination


def write_task_review_report(run: Path, result: dict, destination: Path, *, source_files=()) -> Path:
    destination = validate_review_destination(run, destination, source_files=source_files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects against another writer arriving after
    # the preflight above; existing reports and source files are never replaced.
    with destination.open('x', encoding='utf-8') as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return destination


def _delivery_requirements(run, packet, contract):
    # Exploration can use self-review only after the actual text-risk boundary
    # is known. Generic and workflow entrypoints must not disagree about it.
    selected = packet.get('workflow_policy') or {}
    if selected.get('version') == 2 and selected.get('delivery_level') == 'exploratory':
        from .workflow_policy import _requirements
        return _requirements(Path(run), packet, contract)
    return None


def prepare_task_review(run: Path, image: Path) -> dict:
    packet, contract, _ = _task(Path(run))
    candidate_sha, evidence, source_sha = _candidate(run, packet, contract, image)
    result = prepare_input(packet, contract, packet['references'], candidate_sha, evidence,
                           requirements=_delivery_requirements(run, packet, contract))
    result['packet_digest'] = packet['digest']
    result['contract_digest'] = contract['digest']
    result['local_images'] = {'candidate_file': str(Path(image).resolve()), 'candidate_source_sha256': source_sha,
                              'candidate_sha256': candidate_sha,
                              'reference_root': str(Path(run).resolve())}
    if evidence['route'] == 'host':
        result['local_images']['candidate_normalized_sha256'] = candidate_sha
    result['generation_binding'] = _generation_binding(candidate_sha, evidence, source_sha)
    result['review_record_template'] = {
        'candidate_sha256': candidate_sha, 'contract_digest': contract['digest'],
        'packet_digest': packet['digest'], 'request_id': evidence['request_id'], 'review': None,
    }
    result['instructions'] += ' 按review_record_template保存报告；review填写符合output_schema的实际观察，保留模板中的图像和合同绑定。'
    if sha(Path(image).read_bytes()) != source_sha:
        raise AgentError('准备隔离输入期间本地候选文件已变化')
    return result


def validate_task_review(run: Path, image: Path, review: Path) -> dict:
    packet, contract, _ = _task(Path(run))
    candidate_sha, evidence, source_sha = _candidate(run, packet, contract, image)
    review = Path(review).resolve()
    source_report = review.read_bytes()
    record = json.loads(source_report.decode('utf-8-sig'))
    if not isinstance(record, dict) or not {'candidate_sha256', 'review'} <= record.keys():
        raise AgentError('视觉评审报告须包含图像、合同绑定与review对象')
    if 'contract_digest' in record:
        if record['contract_digest'] != contract['digest']:
            raise AgentError('原评审报告绑定的冻结合同已变化，不能套用旧报告')
        contract_binding = 'explicit_current_contract'
    elif packet.get('workflow_policy') is not None:
        raise AgentError('完整流程的原评审报告须显式记录当前contract_digest')
    else:
        # Historical reports remain readable, but the new completion gate
        # must not count their missing contract binding as newly supplied proof.
        contract_binding = 'not_supplied_legacy'
    for key, expected in (('packet_digest', packet['digest']), ('request_id', evidence['request_id'])):
        if key in record and record[key] != expected:
            raise AgentError('原评审报告的任务或请求绑定不一致：' + key)
    # Require the image binding alongside the original ImageEval rather than
    # silently attaching arbitrary prior review text to today's image.
    result = validate_output(contract, record['review'], candidate_sha=candidate_sha,
                             reviewed_candidate_sha=record['candidate_sha256'],
                             requirements=_delivery_requirements(run, packet, contract))
    if sha(Path(image).read_bytes()) != source_sha:
        raise AgentError('校验评审期间本地候选文件已变化')
    if review.read_bytes() != source_report:
        raise AgentError('校验期间原评审报告已变化，不能保存旧内容的验证结果')
    result.update(generation_binding=_generation_binding(candidate_sha, evidence, source_sha),
                  candidate_file_sha256=source_sha, source_report_file=str(review),
                  source_report_sha256=sha(source_report), source_contract_binding=contract_binding)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Hex任务派生评价卡与本地图像检查；不生成、不上传')
    parser.add_argument('command', choices=['card', 'check', 'prepare-vlm', 'validate-review'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--ocr-requirements', type=Path,
                        help='check专用：宿主从冻结checks派生的明确画内文字要求JSON列表')
    parser.add_argument('--ocr-timeout', type=float, default=180,
                        help='本地OCR进程最长秒数，默认180；超时停止且不重试')
    parser.add_argument('--ocr-backend', choices=['paddleocr', 'tesseract'], default='paddleocr',
                        help='显式选择已安装的本地 OCR；默认保留 Paddle，不自动降级')
    parser.add_argument('--ocr-language', default='eng', help='Tesseract 已安装语言；不下载语言包')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    try:
        run = args.run.resolve()
        if args.ocr_requirements and args.command != 'check':
            raise AgentError('--ocr-requirements仅用于check')
        if args.out:
            validate_review_destination(run, args.out,
                                        source_files=(args.image, args.review, args.ocr_requirements))
        if args.command != 'card' and args.image is None:
            raise AgentError('此操作需要--image')
        if args.command == 'card':
            result = review_card(run)
        elif args.command == 'check':
            result = check_task_image(run, args.image, ocr_requirements=args.ocr_requirements,
                                      ocr_timeout_seconds=args.ocr_timeout,
                                      ocr_backend=args.ocr_backend, ocr_language=args.ocr_language)
        elif args.command == 'prepare-vlm':
            result = prepare_task_review(run, args.image)
        else:
            if args.review is None:
                raise AgentError('validate-review需要--review')
            result = validate_task_review(run, args.image, args.review)
        if args.out:
            write_task_review_report(run, result, args.out,
                                     source_files=(args.image, args.review, args.ocr_requirements))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if args.command == 'check' and result['state'] != 'completed' else 0
    except (AgentError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
