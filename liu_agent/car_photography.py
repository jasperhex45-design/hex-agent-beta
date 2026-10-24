"""Opt-in car-photography preparation and read-only request inspection.

This is not a provider, a budget manager, or an aesthetic evaluator. All paid
execution stays in the existing dispatch / execute / record-result path.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

from .io import AgentError, ROOT, digest, read_json, sha, write_json
from .visual_decision import CAR_PROFILE, CAR_KNOWLEDGE_PROFILE, CAR_PROFILES

PROFILE = CAR_PROFILE
TOOLS = {'seedream': 'pippit.generate-image.seedream-5-pro', 'image': 'image_gen.imagegen'}

DESIGN_INSTRUCTION = '''
汽车摄影候选 car-photography-v1；尚未通过新实图验收，不宣称审美提高。
先理解当前单车外观静态照片的用途与表达问题，再选择方向；有研究缺口时先实际看案例，不把首个联想当作已比较后的选择。主体身份与摄影方法分开：多张车辆图共同确定车型结构，不继承展台或其布光；单张原图只有在用户要求保留构图/机位时才锁定角度，不能把身份保持变成全部角度锁死。
vehicle_identity=open 仅表示本题未要求保留精确车型，不表示要设计概念车，也不表示无需汽车摄影方法或视觉研究；不得声称保留某一真实车型。用户要求特定品牌/型号、车辆身份或原车编辑时，保留 preserve 与必要 identity 素材阻塞，不能为了继续生成改称 open。身份图决定车型事实，研究案例决定可借用的方法，二者独立。
从用途与当前参考出发，决定车与环境的关系、观看重点、角度和光色。商业摄影强调车辆可读性与使用目的；艺术摄影允许暗调、强色、留白或运动感，不强制明亮海派风。未指定的摄影决策由宿主完成，不让用户填写焦距或技术模板。
将光落实为来源、受光面、遮挡和环境反射；让反射解释车体曲面，避免没有目的的细碎高光和纹理。车漆暗面仍需按当前光照表现材质，玻璃、橡胶、金属与路面的响应不能混同。背景参与构图，但不因堆装置、雾气或反光就自称有设计。
沿用已有 choices/checks 写出实际取舍与可见失败条件，不凑数量，不新增评分表。已有完整有效提示词优先保留版本，不自动压缩、翻译或加装饰。案例只迁移摄影方法，不固定案例的车型、地点、车色、位置或时间；案例车辆不得替代本题车辆身份。
先交完整目标模型提示词。实际图像返回后先展示原图；用户原话决定接受或拒绝。相对更喜欢不等于最终接受；明确续改才绑定父图与反馈，并保留正确部分。不从工程通过、提示词覆盖或自我评价推出照片合格。
'''


def validate_vehicle_identity(brief: dict, refs: list[dict], need: dict | None = None) -> None:
    """Separate unspecified vehicle creation from the identity contract.

    This validates the host's task interpretation; it does not infer makes or
    models from free text. Source-backed constraints remain independently
    enforced by the normal Need/Plan gates.
    """
    mode = brief.get('vehicle_identity', 'preserve')
    if mode not in ('preserve', 'open'):
        raise AgentError('vehicle_identity 必须为 preserve 或 open')
    identities = [r for r in refs if isinstance(r, dict) and r.get('role') == 'identity'
                  and not r.get('evaluation_only', False)]
    if mode == 'preserve':
        if not identities:
            raise AgentError('需要当前车辆 identity 参考；缺少保车型素材不能自动改称开放创作')
        return
    if brief.get('scope', 'create') != 'create':
        raise AgentError('开放车型只用于 create；车辆编辑仍须当前车辆 identity 素材')
    if identities:
        raise AgentError('开放车型不能丢弃已声明的 identity 参考；保留 preserve 或核对真实参考职责')
    if ((need or {}).get('understanding') or {}).get('task_mode') == 'local_edit':
        raise AgentError('开放车型不能代替需要真实车辆素材的局部编辑')
    if any(q.get('decision_type') == 'identity' and q.get('status') == 'open'
           for q in (need or {}).get('question_records', [])):
        raise AgentError('车型身份仍未确定；不能通过 open 绕过未解决的身份问题')


def normalize_brief(raw: dict, origin: Path, *, deliverable_kind: str | None = None) -> dict:
    """Retain explicit model, scope, rights and order; never choose a model."""
    if not isinstance(raw, dict):
        raise AgentError('汽车 brief 必须是 JSON 对象')
    value = deepcopy(raw)
    if value.get('domain', 'auto') not in ('auto', 'vehicle'):
        raise AgentError('汽车入口不接管其他领域任务')
    value['domain'] = 'vehicle'
    from .model_selection import require_explicit_targets
    require_explicit_targets(value)
    if (not isinstance(value['targets'], list) or len(value['targets']) != 1
            or not isinstance(value['targets'][0], str)
            or value['targets'][0] not in (*TOOLS, 'nano')):
        raise AgentError('汽车候选入口仅支持单个 seedream、image 目标，或仅交付提示词的 nano 目标；不静默切换模型')
    if value['targets'][0] == 'nano' and deliverable_kind != 'prompt_only':
        raise AgentError('汽车入口 nano 仅支持 --deliverable-kind prompt_only 提示词交付；未绑定生图工具')
    if value.get('medium', 'still') != 'still' or value.get('model_comparison', False):
        raise AgentError('汽车候选入口只做静态单图，不接管视频或双模型实验')
    refs = value.get('references', [])
    if not isinstance(refs, list):
        raise AgentError('车辆 references 必须是列表')
    validate_vehicle_identity(value, refs)
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get('path'), str) or not ref['path']:
            raise AgentError('车辆参考缺少有效 path')
        ref['path'] = str((origin / ref['path']).resolve())
        if not Path(ref['path']).is_file():
            raise AgentError('参考文件不存在：' + str(ref.get('id', 'unknown')))
    return value


def prepare(brief_file: Path, out: Path, kb: Path, state: Path, *,
            sources_file: Path, need_file: Path | None = None,
            knowledge_config: dict | None = None, workflow_profile: str = 'legacy',
            deliverable_kind: str | None = None) -> dict:
    """Create a new normal packet, binding the configured tool before grants."""
    from .contracts import Brief
    from .generation_spec import host_capabilities
    from .workflow import make_packet, save_packet
    brief_file = brief_file.resolve()
    value = normalize_brief(read_json(brief_file), brief_file.parent, deliverable_kind=deliverable_kind)
    Brief.model_validate(value)
    target = value['targets'][0]
    tool = TOOLS.get(target)
    runtime = host_capabilities(tool) if tool is not None else None  # Adapter facts, NOT a live probe.
    if value.get('vehicle_identity') == 'open' and knowledge_config is None:
        # The explicit unspecified-vehicle route receives methods on every host.
        # This reads only existing local, scope-filtered cards; no external
        # service, private preferences or unpromoted mechanism library is used.
        knowledge_config = {'backend': 'local', 'max_queries': 3, 'max_hits': 6}
    profile = CAR_KNOWLEDGE_PROFILE if knowledge_config is not None else PROFILE
    # Absolute references preserve their original resolution across the temp file.
    with tempfile.TemporaryDirectory(prefix='cl-hex-car-') as folder:
        prepared = Path(folder) / 'brief.json'
        write_json(prepared, value)
        packet = make_packet(prepared, out, kb, state, need_file,
                             sources_file=sources_file.resolve(), execution_profile=profile,
                             knowledge_config=knowledge_config, workflow_profile=workflow_profile,
                             deliverable_kind=deliverable_kind)
    if runtime is not None:
        packet.setdefault('compilation_runtimes', {})[target] = runtime
    packet = save_packet(packet, out)
    return {'run': str(out), 'packet_digest': packet['digest'], 'target': target,
            'configured_tool': tool, 'live_tool_checked': False,
            'generation_submitted': False, 'visual_acceptance': 'not_tested',
            'next': '按原话整理 need，保留真实车型要求与参考职责；实际查看适用素材，沿用 observe/refresh 与有限方法检索，然后 prepare-compilation、preflight、freeze-input。生成须有有效授权。'}


def compare_prompt(expected: str, actual: str) -> dict:
    """Compare exactly, including whitespace; do not repair or strip a baseline."""
    same = expected == actual
    first = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b),
                 min(len(expected), len(actual))) if not same else None
    return {'matches': same, 'first_difference': first,
            'expected_sha256': sha(expected.encode('utf-8')),
            'actual_sha256': sha(actual.encode('utf-8'))}


def preview(run: Path, solution_file: Path, *, resolution: str | None = None,
            ratio: int | None = None, expected_prompt: Path | None = None) -> dict:
    """Inspect current primary input; never dispatch, grant, freeze, or submit.

The real dispatch revalidates all permissions/budgets. This snapshot is not a
reservation, an authorization, or evidence that a provider has been called.
"""
    from .contracts import Solution
    from .generation_spec import host_capabilities
    from .host import read_dispatches
    from .run_lock import dispatch_lock
    from .visual_decision import policy
    from .workflow import read_packet, validate_solution
    run = run.resolve()
    with dispatch_lock(run):
        packet = read_packet(run)
        if (policy(packet) or {}).get('profile') not in CAR_PROFILES:
            raise AgentError('只检查显式新建的汽车候选任务；旧任务按原入口恢复')
        if packet['brief']['targets'] == ['nano']:
            raise AgentError('汽车入口 nano 仅支持 prompt_only 提示词交付；未绑定生图工具，不能预览或冻结生图输入')
        if packet.get('model_adaptation_version') != 1:
            raise AgentError('汽车候选必须使用现有模型适配合同')
        if read_dispatches(run) or any((run / name).exists() for name in
                                       ('api-state.json', 'calls.json', 'api', 'image-tests.json', 'host-image-tests.json')):
            raise AgentError('已有调用证据；请 status/resume 核对原请求，不用预览替代真实记录')
        solution = Solution.model_validate(read_json(solution_file))
        validate_solution(packet, solution)
        target = packet['brief']['targets'][0]
        tool = TOOLS.get(target)
        if tool is None or packet.get('compilation_runtimes', {}).get(target) != host_capabilities(tool):
            raise AgentError('目标工具未正确绑定；先按实际 target/tool 完成 prepare-compilation')
        from .execution_request import build_input
        options = {k: v for k, v in {'resolution': resolution, 'ratio': ratio}.items() if v is not None}
        prepared = build_input(run, packet, solution, target, tool, provider_options=options or None)
        arguments = prepared['arguments']
        prompt = prepared['prompt']
        images = prepared['images_in_order']
        usage = prepared['reference_usage']
        authored = next(p.text for p in solution.compilation.prompts if p.target == target)
        result = {'kind': 'input_preview_only', 'packet_digest': packet['digest'],
                  'target': target, 'tool': tool, 'arguments': arguments,
                  'solution_digest': digest(solution.model_dump()), 'input': prepared,
                  'arguments_digest': digest(arguments), 'images_in_order': images,
                  'reference_usage': usage, 'authored_vs_compiled': compare_prompt(authored, prompt),
                  'generation_submitted': False, 'visual_acceptance': 'not_tested',
                  'notice': '只检查当前输入，不证明授权就绪、provider可用、模型理解或视觉质量；dispatch仍须独立校验。'}
        if expected_prompt is not None:
            result['baseline_comparison'] = compare_prompt(
                expected_prompt.read_bytes().decode('utf-8-sig'), prompt)
        return result


FROZEN_INPUT = 'prepared-request.json'


def freeze_input(run: Path, solution_file: Path, *, resolution: str | None = None,
                 ratio: int | None = None, expected_prompt: Path | None = None) -> dict:
    """Finalize input before authorization; never reserve budget or call a provider."""
    from .run_lock import dispatch_lock
    # preview owns the same lock; do not nest it. Recheck the version under lock.
    result = preview(run, solution_file, resolution=resolution, ratio=ratio,
                     expected_prompt=expected_prompt)
    if not result.get('baseline_comparison', {}).get('matches', True):
        raise AgentError('基线正文不一致；不能冻结或派发，请检查空白、追加约束与正文差异')
    from .workflow import read_packet
    from .contracts import Solution
    from .host import read_dispatches
    with dispatch_lock(run):
        packet = read_packet(run)
        current = Solution.model_validate(read_json(solution_file))
        if (packet['digest'] != result['packet_digest'] or
                digest(current.model_dump()) != result['solution_digest'] or read_dispatches(run)):
            raise AgentError('预览后输入或执行状态已改变；重新预览')
        from .execution_request import build_input
        options = {k: v for k, v in {'resolution': resolution, 'ratio': ratio}.items() if v is not None}
        if build_input(run, packet, current, result['target'], result['tool'],
                       provider_options=options or None) != result['input']:
            raise AgentError('预览后实发输入已改变；重新预览')
        frozen = {k: result[k] for k in ('packet_digest', 'solution_digest', 'target', 'tool', 'input')}
        frozen.update(format='cl-hex.prepared-request.v1',
                      mode='baseline_reproduction' if expected_prompt is not None else 'creation',
                      baseline_comparison=result.get('baseline_comparison'),
                      authored_vs_compiled=result['authored_vs_compiled'])
        if (run / FROZEN_INPUT).exists():
            previous = read_json(run / FROZEN_INPUT)
            if previous.get('mode') == 'baseline_reproduction' and (
                    frozen['mode'] != 'baseline_reproduction' or
                    previous.get('baseline_comparison') != frozen['baseline_comparison'] or
                    any(previous.get(k) != frozen[k] for k in ('input', 'target', 'tool'))):
                raise AgentError('已冻结的基线复现不可降为自由创作或改变基线正文/实参；新需求须建立新任务')
        frozen['digest'] = digest(frozen)
        write_json(run / FROZEN_INPUT, frozen)
        from .io import atomic_text
        atomic_text(run / 'finalized-prompt.txt', result['arguments']['prompt'], newline='')
    return {**result, 'kind': 'frozen_input_not_dispatched', 'mode': frozen['mode'],
            'frozen_input_digest': frozen['digest'], 'finalized_prompt': str(run / 'finalized-prompt.txt')}


def consume_frozen_input(run: Path, packet: dict, solution, target: str, tool: str, current: dict) -> dict:
    """Car dispatch must match the final input; legacy unrelated tasks are unchanged."""
    from .visual_decision import policy
    if (policy(packet) or {}).get('profile') not in CAR_PROFILES:
        return current
    if packet.get('request_input_version') is None and not (run / FROZEN_INPUT).exists():
        return current  # Old task: do not silently migrate its frozen contract.
    if packet.get('request_input_version') not in (None, 1):
        raise AgentError('未知请求冻结版本；停止派发')
    path = run / FROZEN_INPUT
    if not path.exists():
        raise AgentError('汽车任务须先 freeze-input 冻结完整实发输入，再 dispatch')
    frozen = read_json(path)
    if (not isinstance(frozen, dict) or frozen.get('format') != 'cl-hex.prepared-request.v1' or
            frozen.get('digest') != digest({k: v for k, v in frozen.items() if k != 'digest'})):
        raise AgentError('冻结输入结构或摘要无效；停止派发')
    if frozen.get('mode') not in ('creation', 'baseline_reproduction'):
        raise AgentError('冻结输入模式无效')
    if frozen['mode'] == 'baseline_reproduction':
        comparison = frozen.get('baseline_comparison') or {}
        if (comparison.get('matches') is not True or comparison.get('expected_sha256') !=
                sha(current['prompt'].encode('utf-8'))):
            raise AgentError('基线正文不一致；停止派发')
    expected = {'packet_digest': packet['digest'], 'solution_digest': digest(solution.model_dump()),
                'target': target, 'tool': tool, 'input': current}
    if any(frozen.get(k) != v for k, v in expected.items()):
        raise AgentError('当前输入与冻结输入不一致；核对正文、素材顺序、模型和参数后重新 freeze-input')
    return frozen['input']


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='python -m cl_hex.car_photography',
                                description='Hex Agent 汽车摄影候选入口：准备或检查输入，不生成图片')
    sub = p.add_subparsers(dest='command', required=True)
    start = sub.add_parser('prepare')
    start.add_argument('--brief', type=Path, required=True)
    start.add_argument('--sources', type=Path, required=True)
    start.add_argument('--need', type=Path)
    start.add_argument('--workflow-profile', choices=['legacy', 'full-flow-v1'], default='legacy')
    start.add_argument('--deliverable-kind', choices=['prompt_only', 'image', 'local_edit'])
    start.add_argument('--out', type=Path, required=True)
    start.add_argument('--kb', type=Path, default=ROOT / 'knowledge')
    start.add_argument('--state', type=Path, default=ROOT / '.state')
    start.add_argument('--knowledge-config', type=Path,
                       help='显式选择新任务的有限知识检索；开放车型省略时使用本地检索，保身份任务保留原策略')
    for command in ('preview', 'freeze-input'):
        check = sub.add_parser(command)
        check.add_argument('--run', type=Path, required=True)
        check.add_argument('--solution', type=Path, required=True)
        check.add_argument('--resolution')
        check.add_argument('--ratio', type=int)
        check.add_argument('--expected-prompt', type=Path)
    args = p.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.brief, args.out, args.kb, args.state,
                             sources_file=args.sources, need_file=args.need,
                             knowledge_config=read_json(args.knowledge_config) if args.knowledge_config else None,
                             workflow_profile=args.workflow_profile, deliverable_kind=args.deliverable_kind)
        else:
            inspect = freeze_input if args.command == 'freeze-input' else preview
            result = inspect(args.run, args.solution, resolution=args.resolution,
                             ratio=args.ratio, expected_prompt=args.expected_prompt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('baseline_comparison', {}).get('matches', True) else 3
    except (AgentError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
